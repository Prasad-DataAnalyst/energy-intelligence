"""
core/brain.py — Central AI Decision Engine
============================================
The Brain is the system's executive function.  It:
  1. Reads memory to understand what worked historically
  2. Consults the config to know current system preferences
  3. Makes all high-level decisions: topic selection, style, pacing
  4. Writes its decisions back to memory for future learning
  5. Adjusts configs automatically when data shows better options

Every decision is logged so the system can reason about its own past.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic
import yaml

log = logging.getLogger("brain.central")

HERE    = Path(__file__).parent.parent
CFG_DIR = HERE / "config"


class Brain:
    def __init__(self):
        self._client: Optional[anthropic.Anthropic] = None
        self._cfg:    Optional[Dict] = None
        self._persona: Optional[Dict] = None
        from core.memory import get_memory
        self.memory = get_memory()

    # ── Config access ─────────────────────────────────────────────────────────

    @property
    def cfg(self) -> Dict:
        if self._cfg is None:
            self._cfg = self._load_yaml("master_config.yaml")
        return self._cfg

    @property
    def persona(self) -> Dict:
        if self._persona is None:
            self._persona = self._load_yaml("channel_persona.yaml")
        return self._persona

    @staticmethod
    def _load_yaml(name: str) -> Dict:
        path = CFG_DIR / name
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f) or {}
        return {}

    def reload_config(self):
        self._cfg = None
        self._persona = None

    # ── Claude client ─────────────────────────────────────────────────────────

    def _claude(self) -> Optional[anthropic.Anthropic]:
        if self._client is None:
            key = os.getenv("ANTHROPIC_API_KEY", "")
            if key:
                self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def ask(self, prompt: str, system: str = "You are a strategic AI system.",
             max_tokens: int = 1024, json_mode: bool = False) -> str:
        """Send a prompt to Claude and return the text response."""
        client = self._claude()
        if not client:
            log.warning("No ANTHROPIC_API_KEY — brain running in fallback mode")
            return ""
        model = self.cfg.get("ai", {}).get("model", "claude-sonnet-4-6")
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            log.error(f"Claude call failed: {e}")
            return ""

    # ── Daily decisions ────────────────────────────────────────────────────────

    def decide_topic(self, candidates: List[Dict],
                      recent_topics: List[str]) -> Dict:
        """
        Given trend candidates + recent topics (avoid repeats),
        pick the best topic for today using memory + Claude reasoning.
        """
        if not candidates:
            return {"title": "AI Tools Changing American Work in 2025",
                    "source": "fallback", "score": 0.5}

        # Filter out recently covered topics
        filtered = [c for c in candidates
                    if not any(rt.lower() in c["title"].lower()
                               for rt in recent_topics)]
        if not filtered:
            filtered = candidates   # if everything was covered, allow repeats

        # Ask Claude for the strategically best pick
        summary = self.memory.performance_summary()
        prompt = f"""You are a YouTube channel strategist.

Channel persona: {json.dumps(self.persona, indent=2)}

Performance summary (historical):
{json.dumps(summary, indent=2)}

Trending topic candidates:
{json.dumps([{"title": c["title"], "score": c.get("score", 0),
              "source": c.get("source", "")} for c in filtered[:15]], indent=2)}

Recently covered topics (avoid repeating within 7 days):
{recent_topics}

Select the single best topic for today's video that:
1. Maximises expected CTR and watch time for this audience
2. Hasn't been covered recently
3. Is advertiser-friendly (no controversy)
4. Has high search intent

Return ONLY a JSON object: {{"title": "...", "reason": "...", "score": 0.0}}"""

        raw = self.ask(prompt, json_mode=True)
        try:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                decision = json.loads(m.group())
                # Find matching candidate
                title = decision.get("title", "")
                match = next(
                    (c for c in filtered if c["title"][:40] in title or
                     title[:40] in c["title"]),
                    filtered[0]
                )
                log.info(f"Brain chose: {match['title']!r} — {decision.get('reason', '')[:80]}")
                return match
        except Exception as e:
            log.warning(f"Brain decision parse failed: {e}")

        return filtered[0]   # fallback: highest-scored candidate

    def decide_style(self, topic: str) -> str:
        """Return the style preset that historically performs best for this topic."""
        # Check memory for best-performing style
        best = self.memory.get_best_config("style_preset")
        if best:
            log.info(f"Brain: using historically best style: {best}")
            return best

        # Ask Claude based on topic
        prompt = (
            f"Given the YouTube video topic: '{topic}'\n"
            f"Channel persona: {json.dumps(self.persona)}\n\n"
            f"Return ONLY one of: tech-dark | vlog-warm | news-clean | motivation-epic"
        )
        raw = self.ask(prompt, max_tokens=20)
        for preset in ("tech-dark", "vlog-warm", "news-clean", "motivation-epic"):
            if preset in raw:
                return preset
        return self.persona.get("lut_profile", "tech-dark")

    def decide_video_length(self) -> int:
        """Return optimal video length in seconds based on historical performance."""
        best = self.memory.get_best_config("video_length_bucket")
        if best:
            return int(best)

        platform = self.cfg.get("video", {}).get("platform", "youtube")
        defaults = {"youtube": 540, "tiktok": 55, "reels": 60}
        return defaults.get(platform, 540)

    # ── Config self-update ────────────────────────────────────────────────────

    def apply_learned_settings(self) -> None:
        """
        Read config_performance from memory and update master_config.yaml
        with the settings that are empirically performing best.
        """
        cfg_path = CFG_DIR / "master_config.yaml"
        if not cfg_path.exists():
            return

        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}

        changed = False
        keys_to_check = [
            ("style_preset",   "video.style_preset"),
            ("hook_style",     "script.hook_style"),
            ("video_length",   "video.duration_seconds"),
        ]
        for mem_key, cfg_path_key in keys_to_check:
            best_val = self.memory.get_best_config(mem_key)
            if best_val:
                section, key = cfg_path_key.split(".")
                old_val = cfg.get(section, {}).get(key)
                if str(old_val) != str(best_val):
                    cfg.setdefault(section, {})[key] = best_val
                    log.info(f"Brain updated config: {cfg_path_key} "
                             f"{old_val!r} → {best_val!r}")
                    changed = True

        if changed:
            with open(CFG_DIR / "master_config.yaml", "w") as f:
                yaml.dump(cfg, f, default_flow_style=False)
            self.reload_config()
            log.info("master_config.yaml updated from learned settings")

    # ── Summary ───────────────────────────────────────────────────────────────

    def daily_brief(self) -> str:
        summary = self.memory.performance_summary()
        return (
            f"\n{'━'*55}\n"
            f"BRAIN DAILY BRIEF — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"{'━'*55}\n"
            f"Total videos produced : {summary['total_videos']}\n"
            f"Average CTR           : {summary['avg_ctr']:.2f}%\n"
            f"Average view duration : {summary['avg_avd_pct']:.1f}%\n"
            f"Average RPM           : ${summary['avg_rpm']:.2f}\n"
            f"{'━'*55}"
        )


# Module-level singleton
_brain: Optional[Brain] = None

def get_brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain()
    return _brain
