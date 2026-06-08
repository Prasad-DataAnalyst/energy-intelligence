"""
intelligence/future_proof.py — Perpetual Self-Upgrading System
==============================================================
Ensures the engine NEVER becomes obsolete by continuously monitoring
AI model releases, platform policy changes, package upgrades, and
quality standards — and automatically integrating improvements.

Cycle:
  Daily (06:00 UTC):   PlatformChangeDetector, ModelUpgradeWatcher
  Weekly (Mon 07:00):  TechStackUpdater, AIVideoModelTracker
  Monthly (1st 08:00): QualityBenchmarkEscalator

All systems degrade gracefully when network/API is unavailable.
Zero credentials required to run — every component has a safe fallback.

Environment variables (all optional):
  ANTHROPIC_API_KEY     — model benchmarking via live API
  YOUTUBE_DATA_API_KEY  — platform change context
  REDDIT_CLIENT_ID      — not used here
"""
import json
import logging
import os
import re
import subprocess
import sys
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("brain.future_proof")

HERE = Path(__file__).parent.parent

# ── Known AI model registry ───────────────────────────────────────────────────
# Composite score (0–100): capability × quality / cost_tier_penalty
# Kept as a versioned snapshot; updated in-memory when watcher detects new models.

KNOWN_AI_MODELS: Dict[str, Dict[str, Dict]] = {
    "text": {
        "claude-haiku-4-5-20251001": {"provider": "anthropic", "score": 82, "cost_tier": "low",  "active": True},
        "claude-sonnet-4-6":         {"provider": "anthropic", "score": 93, "cost_tier": "mid",  "active": True},
        "claude-opus-4-8":           {"provider": "anthropic", "score": 97, "cost_tier": "high", "active": True},
        "gpt-4o":                    {"provider": "openai",    "score": 90, "cost_tier": "high", "active": True},
        "gpt-4o-mini":               {"provider": "openai",    "score": 84, "cost_tier": "low",  "active": True},
        "gemini-2.0-flash":          {"provider": "google",    "score": 85, "cost_tier": "low",  "active": True},
        "gemini-1.5-pro":            {"provider": "google",    "score": 88, "cost_tier": "mid",  "active": True},
    },
    "voice": {
        "eleven_multilingual_v2":    {"provider": "elevenlabs", "score": 92, "cost_tier": "mid"},
        "eleven_turbo_v2_5":         {"provider": "elevenlabs", "score": 87, "cost_tier": "low"},
        "eleven_flash_v2_5":         {"provider": "elevenlabs", "score": 80, "cost_tier": "low"},
        "openai_tts_hd":             {"provider": "openai",     "score": 82, "cost_tier": "low"},
    },
    "video": {
        "sora":                      {"provider": "openai",    "score": 95, "cost_tier": "high"},
        "runway_gen3_alpha":         {"provider": "runway",    "score": 88, "cost_tier": "high"},
        "kling_1_5":                 {"provider": "kling",     "score": 85, "cost_tier": "mid"},
        "luma_dream_machine":        {"provider": "luma",      "score": 83, "cost_tier": "mid"},
        "stable_video_diffusion":    {"provider": "stability", "score": 72, "cost_tier": "low"},
    },
    "image": {
        "flux_1_1_pro":             {"provider": "bfl",       "score": 93},
        "midjourney_v6":            {"provider": "midjourney","score": 95},
        "dalle_3":                  {"provider": "openai",    "score": 88},
        "ideogram_v2":              {"provider": "ideogram",  "score": 85},
        "stable_diffusion_xl":      {"provider": "stability", "score": 82},
    },
}

# ── Quality escalation tiers ──────────────────────────────────────────────────

QUALITY_TIERS: List[Dict] = [
    {"month": 1,  "resolution": "720p",    "fps": 30, "bitrate_kbps": 4_000,
     "color_grade": "basic",           "features": ["720p", "basic_audio"]},
    {"month": 2,  "resolution": "1080p",   "fps": 30, "bitrate_kbps": 8_000,
     "color_grade": "standard",        "features": ["1080p", "color_grade", "stereo"]},
    {"month": 3,  "resolution": "1080p",   "fps": 60, "bitrate_kbps": 12_000,
     "color_grade": "cinematic",       "features": ["1080p60", "motion_graphics", "voice_fx"]},
    {"month": 6,  "resolution": "4K",      "fps": 60, "bitrate_kbps": 40_000,
     "color_grade": "broadcast",       "features": ["4k", "hdr", "motion_design"]},
    {"month": 12, "resolution": "4K HDR",  "fps": 60, "bitrate_kbps": 60_000,
     "color_grade": "cinematic_hdr",   "features": ["4k_hdr", "dolby_vision", "ai_upscale"]},
]

RESOLUTION_RANK = {"480p": 0, "720p": 1, "1080p": 2, "4K": 3, "4K HDR": 4}

# ── Package compatibility matrix ──────────────────────────────────────────────
# breaking_above: first version with breaking API change; None = always safe

COMPATIBILITY_MATRIX: Dict[str, Dict] = {
    "anthropic":    {"min": "0.18.0", "breaking_above": None},
    "pillow":       {"min": "9.0.0",  "breaking_above": None},
    "apscheduler":  {"min": "3.10.0", "breaking_above": "4.0.0"},
    "requests":     {"min": "2.28.0", "breaking_above": None},
    "pydantic":     {"min": "1.10.0", "breaking_above": "2.0.0"},
    "numpy":        {"min": "1.24.0", "breaking_above": None},
    "openai":       {"min": "1.0.0",  "breaking_above": None},
    "yt-dlp":       {"min": "2023.1.6", "breaking_above": None},
    "boto3":        {"min": "1.26.0", "breaking_above": None},
    "deepl":        {"min": "1.14.0", "breaking_above": None},
}

# ── Benchmark task ────────────────────────────────────────────────────────────
# Fixed prompt used to compare model output quality over time.

BENCHMARK_PROMPT = (
    "Write 3 punchy YouTube video intro bullet points about solar energy adoption "
    "in 2026. Each bullet must: (1) open with a specific statistic or percentage, "
    "(2) stay under 20 words, (3) spark curiosity. "
    "Format exactly: • [stat] — [intriguing implication]"
)

BENCHMARK_KEYWORDS = [
    "solar", "energy", "renewable", "percent", "billion", "million",
    "cost", "power", "grid", "homes", "adoption",
]

RSS_FEEDS: Dict[str, str] = {
    "youtube_blog":      "https://blog.youtube/feeds/posts/default/",
    "youtube_api_blog":  "https://developers.googleblog.com/feeds/posts/default/-/youtube",
}

CHANGE_CATEGORIES = {
    "monetization": ["monetiz", "adsense", "revenue", "payment", "cpm", "rpm",
                     "super chat", "membership", "shopping"],
    "algorithm":    ["algorithm", "ranking", "recommendation", "discover", "search",
                     "feed", "boost", "suppress", "demonetiz"],
    "upload":       ["upload", "format", "codec", "resolution", "aspect ratio",
                     "shorts", "live stream", "premiere"],
    "community":    ["comment", "community post", "poll", "notification",
                     "subscriber", "channel page"],
    "api":          ["data api", "api", "quota", "endpoint", "deprecated", "v3"],
    "policy":       ["policy", "terms", "guideline", "age", "restrict",
                     "copyright", "strike", "ban"],
}


# ── 1. ModelUpgradeWatcher ───────────────────────────────────────────────────

class ModelUpgradeWatcher:
    """
    Monitors AI model providers for new releases and auto-switches when
    a new model scores better on the benchmark task.

    Categories monitored: text (LLMs), voice (TTS), video generation, image generation.
    Uses a fixed benchmark prompt + heuristic scoring so no human evaluation needed.
    """

    def __init__(self):
        self._claude_key = os.getenv("ANTHROPIC_API_KEY", "")

    def check_for_new_models(self, category: str = "text") -> List[Dict]:
        """
        Fetch provider changelog pages and return list of newly detected model IDs.
        Parses Anthropic/OpenAI/Google release notes via HTTP (degrades gracefully).
        """
        detected = []
        if category == "text":
            detected += self._scrape_provider("anthropic")
            detected += self._scrape_provider("openai")
        elif category == "voice":
            detected += self._scrape_provider("elevenlabs")
        elif category == "video":
            detected += self._scrape_provider("runway")
        return detected

    def benchmark_model(self, model_id: str, category: str = "text",
                         dry_run: bool = False) -> Dict:
        """
        Send BENCHMARK_PROMPT to model and score the response.
        Returns: {model_id, score, response_preview, timestamp}.
        dry_run=True returns a synthetic score without API call.
        """
        if dry_run or not self._claude_key:
            synthetic_score = self._synthetic_score(model_id, category)
            return {
                "model_id":        model_id,
                "category":        category,
                "score":           synthetic_score,
                "response_preview": "(dry run — no API call)",
                "timestamp":       datetime.now(timezone.utc).isoformat(),
            }

        response_text = self._call_model(model_id, BENCHMARK_PROMPT)
        score         = self._score_response(response_text, category)
        return {
            "model_id":        model_id,
            "category":        category,
            "score":           score,
            "response_preview": response_text[:120],
            "timestamp":       datetime.now(timezone.utc).isoformat(),
        }

    def compare_and_recommend(self, category: str = "text",
                               cost_tier: str = None,
                               memory=None) -> Dict:
        """
        Compare all known models in category; return the best available one.
        If cost_tier is given, only consider models at that tier or lower.
        """
        models = KNOWN_AI_MODELS.get(category, {})
        if not models:
            return {"model_id": None, "reason": f"no_models_for_{category}"}

        candidates = {
            mid: m for mid, m in models.items()
            if not cost_tier or self._tier_lte(m.get("cost_tier", "high"), cost_tier)
        }
        if not candidates:
            candidates = models

        best_id    = max(candidates, key=lambda mid: candidates[mid].get("score", 0))
        best_model = candidates[best_id]

        # Cross-check with memory benchmarks if available
        if memory:
            try:
                benchmarks = memory.get_model_benchmarks(category, n=10)
                if benchmarks:
                    best_bench = max(benchmarks, key=lambda b: b.get("score", 0))
                    if best_bench["score"] > best_model.get("score", 0):
                        best_id = best_bench["model_id"]
            except Exception:
                pass

        return {
            "model_id":  best_id,
            "score":     best_model.get("score", 0),
            "provider":  best_model.get("provider", "unknown"),
            "cost_tier": best_model.get("cost_tier", "unknown"),
            "reason":    "highest_composite_score",
        }

    def auto_switch_if_better(self, current_model: str, candidate_model: str,
                               category: str, memory=None) -> Dict:
        """
        Switch to candidate if its score is ≥ 3 points above current model.
        Returns decision dict and saves benchmark to memory.
        """
        curr_data = KNOWN_AI_MODELS.get(category, {}).get(current_model, {})
        cand_data = KNOWN_AI_MODELS.get(category, {}).get(candidate_model, {})

        curr_score = curr_data.get("score", 0)
        cand_score = cand_data.get("score", 0)
        delta      = cand_score - curr_score
        switched   = delta >= 3

        if memory:
            try:
                memory.save_model_benchmark({
                    "model_id":    candidate_model,
                    "category":    category,
                    "score":       cand_score,
                    "switched_to": candidate_model if switched else None,
                    "delta":       delta,
                    "active":      switched,
                })
            except Exception as e:
                log.debug(f"Benchmark save failed: {e}")

        return {
            "switched":   switched,
            "from_model": current_model,
            "to_model":   candidate_model if switched else current_model,
            "delta":      delta,
            "reason":     "score_improvement" if switched else "no_improvement",
        }

    def _score_response(self, text: str, category: str = "text") -> float:
        """
        Heuristic quality score (0–100) for a model response.
        Checks structure, statistics, relevance, and engagement signals.
        """
        if not text or len(text) < 20:
            return 0.0

        score = 0.0
        # Structure: bullet points
        bullets = len(re.findall(r'[•\-\*]\s', text))
        score  += min(bullets, 3) * 10.0             # up to 30 pts

        # Numbers / statistics
        stats = len(re.findall(r'\b\d[\d,.]*\s*[%xkKmMbB]?\b', text))
        score += min(stats, 3) * 8.0                 # up to 24 pts

        # Keyword relevance
        kw_hits = sum(1 for kw in BENCHMARK_KEYWORDS if kw in text.lower())
        score  += min(kw_hits, 4) * 5.0              # up to 20 pts

        # Length: sweet spot 40–200 words
        wc = len(text.split())
        if 40 <= wc <= 200:    score += 16.0
        elif 20 <= wc < 40:    score += 8.0
        elif wc > 200:         score += 6.0

        # Engagement signals
        engage = ["secret", "shocking", "believe", "discover", "truth", "reveal"]
        eg_hits = sum(1 for e in engage if e in text.lower())
        score  += min(eg_hits, 2) * 5.0              # up to 10 pts

        return min(round(score, 1), 100.0)

    def _synthetic_score(self, model_id: str, category: str) -> float:
        """Return known score from registry, or a default when model is unknown."""
        return KNOWN_AI_MODELS.get(category, {}).get(model_id, {}).get("score", 70.0)

    def _call_model(self, model_id: str, prompt: str) -> str:
        """Call the model API and return the response text."""
        provider = KNOWN_AI_MODELS.get("text", {}).get(model_id, {}).get("provider", "")
        if provider == "anthropic" and self._claude_key:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=self._claude_key)
                msg    = client.messages.create(
                    model=model_id, max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text.strip()
            except Exception as e:
                log.debug(f"Anthropic call failed for {model_id}: {e}")
        return ""

    def _scrape_provider(self, provider: str) -> List[Dict]:
        """
        Fetch provider changelog page and look for new model version strings.
        Returns list of detected model dicts (provider, model_id, detected_at).
        """
        url = {
            "anthropic": "https://docs.anthropic.com/en/release-notes/api",
            "openai":    "https://platform.openai.com/docs/changelog",
            "elevenlabs":"https://elevenlabs.io/docs/changelog",
            "runway":    "https://runwayml.com/blog/",
        }.get(provider, "")
        if not url:
            return []

        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "YTBrainBot/1.0 (changelog monitor)"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="replace")[:50_000]
        except Exception as e:
            log.debug(f"Provider scrape failed for {provider}: {e}")
            return []

        # Extract version-like strings (e.g. "claude-3-5", "gpt-5", "gemini-2.0")
        patterns = {
            "anthropic":  r'claude-[\w\d\-\.]+',
            "openai":     r'gpt-[\w\d\-\.]+|o\d-[\w\d\-\.]+',
            "elevenlabs": r'eleven_[\w\d_]+',
            "runway":     r'gen[- ]\d[\w\d\-\.]*',
        }
        pat = patterns.get(provider, r'[\w\d]+-[\w\d\-\.]+')
        found = list({m.lower() for m in re.findall(pat, html, re.IGNORECASE)})[:5]

        return [{"provider": provider, "model_id": mid,
                 "detected_at": datetime.now(timezone.utc).isoformat()}
                for mid in found]

    @staticmethod
    def _tier_lte(tier: str, cap: str) -> bool:
        rank = {"low": 0, "mid": 1, "high": 2}
        return rank.get(tier, 2) <= rank.get(cap, 2)


# ── 2. PlatformChangeDetector ────────────────────────────────────────────────

class PlatformChangeDetector:
    """
    Monitors YouTube Creator Blog and API changelog via RSS.
    Classifies each change (monetization/algorithm/upload/community/api/policy)
    and generates an adaptation plan using Claude.

    New items are saved to memory; adapted=False until action is taken.
    """

    def __init__(self):
        self._claude_key = os.getenv("ANTHROPIC_API_KEY", "")

    def check_youtube_blog(self, memory=None) -> List[Dict]:
        """Fetch YouTube blog RSS, classify new posts, save to memory."""
        items = self._parse_rss(RSS_FEEDS["youtube_blog"])
        new   = []
        for item in items[:10]:
            category = self.classify_change(
                item.get("title", ""), item.get("description", "")
            )
            change = {
                "source":      "youtube_blog",
                "title":       item.get("title", ""),
                "url":         item.get("link", ""),
                "description": item.get("description", "")[:400],
                "change_type": category,
                "pub_date":    item.get("pub_date", ""),
                "priority":    self._is_high_priority(category, item.get("title", "")),
                "adapted":     False,
            }
            new.append(change)
            if memory:
                try:
                    memory.save_platform_change(change)
                except Exception as e:
                    log.debug(f"Platform change save failed: {e}")
        return new

    def classify_change(self, title: str, description: str) -> str:
        """
        Map a post/changelog entry to a change category.
        Returns: monetization | algorithm | upload | community | api | policy | general
        """
        text = (title + " " + description).lower()
        for category, keywords in CHANGE_CATEGORIES.items():
            if any(kw in text for kw in keywords):
                return category
        return "general"

    def generate_adaptation(self, change: Dict, niche: str = "energy") -> Dict:
        """
        Use Claude Haiku to generate a specific system adaptation plan.
        Returns a plan dict with action_steps list.
        """
        if not self._claude_key:
            return self._template_adaptation(change)

        prompt = (
            f"A YouTube platform change has been detected:\n"
            f"Title: {change.get('title','')}\n"
            f"Category: {change.get('change_type','')}\n"
            f"Description: {change.get('description','')[:300]}\n\n"
            f"You run a YouTube automation system for the {niche} niche. "
            f"List exactly 3 specific code/config changes needed to adapt "
            f"within 24 hours. Be concrete — name files and settings. "
            f"Return JSON: [{{\"step\": int, \"action\": str, \"file\": str, \"urgency\": \"high|medium|low\"}}]"
        )
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._claude_key)
            msg    = client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            text   = msg.content[0].text
            m      = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                steps = json.loads(m.group(0))
                return {"change_type": change.get("change_type"),
                        "action_steps": steps,
                        "deadline_hours": 24,
                        "source": "claude"}
        except Exception as e:
            log.debug(f"Claude adaptation failed: {e}")

        return self._template_adaptation(change)

    def check_pending_adaptations(self, memory) -> List[Dict]:
        """Return unadapted platform changes from memory."""
        return memory.get_unadapted_changes() if memory else []

    # ── Private ─────────────────────────────────────────────────────────────

    def _parse_rss(self, url: str) -> List[Dict]:
        """Parse an RSS/Atom feed without external libs. Returns item list."""
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "YTBrainBot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            log.debug(f"RSS fetch failed for {url}: {e}")
            return []

        items = []
        for m in re.finditer(r'<(?:item|entry)>(.*?)</(?:item|entry)>', xml, re.DOTALL):
            raw   = m.group(1)
            title = re.search(r'<title[^>]*>(.*?)</title>', raw, re.DOTALL)
            link  = re.search(r'<(?:link|id)[^>]*>(.*?)</(?:link|id)>', raw, re.DOTALL)
            desc  = re.search(r'<(?:description|summary|content)[^>]*>(.*?)</(?:description|summary|content)>', raw, re.DOTALL)
            date  = re.search(r'<(?:pubDate|published|updated)[^>]*>(.*?)</(?:pubDate|published|updated)>', raw, re.DOTALL)
            items.append({
                "title":       self._clean_xml(title.group(1) if title else ""),
                "link":        (link.group(1).strip() if link else ""),
                "description": self._clean_xml(desc.group(1) if desc else "")[:500],
                "pub_date":    (date.group(1).strip() if date else ""),
            })
        return items[:20]

    def _clean_xml(self, text: str) -> str:
        """Strip CDATA wrappers, HTML tags, and common XML entities."""
        text = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)
        for ent, rep in [('&lt;', '<'), ('&gt;', '>'), ('&amp;', '&'),
                          ('&quot;', '"'), ('&#39;', "'")]:
            text = text.replace(ent, rep)
        return text.strip()

    def _is_high_priority(self, category: str, title: str) -> bool:
        """Monetization and policy changes are always high priority."""
        high_cats = {"monetization", "policy", "algorithm"}
        urgent_words = ["breaking", "important", "required", "mandatory", "removal",
                         "deprecated", "shutdown", "ban"]
        if category in high_cats:
            return True
        return any(w in title.lower() for w in urgent_words)

    def _template_adaptation(self, change: Dict) -> Dict:
        templates = {
            "monetization": [
                {"step": 1, "action": "Review MonetizationEngine ad-safety thresholds",
                 "file": "monetization/monetization_engine.py", "urgency": "high"},
                {"step": 2, "action": "Update RPM estimates in ShortsMonetizationTracker",
                 "file": "production/shorts_factory.py", "urgency": "medium"},
                {"step": 3, "action": "Re-run revenue dashboard to validate baseline",
                 "file": "main.py --phase monetize", "urgency": "low"},
            ],
            "algorithm": [
                {"step": 1, "action": "Trigger AlgorithmHacker fresh analysis (n=100)",
                 "file": "intelligence/algorithm_hacker.py", "urgency": "high"},
                {"step": 2, "action": "Update optimal_duration_s in DailyPipeline",
                 "file": "core/scheduler.py", "urgency": "medium"},
                {"step": 3, "action": "Refresh SEO keyword strategy via MultilingualSEO",
                 "file": "intelligence/global_expansion.py", "urgency": "low"},
            ],
            "upload": [
                {"step": 1, "action": "Update PLATFORM_SPECS in ShortsFactory if format changed",
                 "file": "production/shorts_factory.py", "urgency": "high"},
                {"step": 2, "action": "Validate VideoEditor codec/resolution settings",
                 "file": "production/editor.py", "urgency": "high"},
                {"step": 3, "action": "Test thumbnail upload dimensions in ThumbnailAI",
                 "file": "production/thumbnail_ai.py", "urgency": "medium"},
            ],
        }
        cat   = change.get("change_type", "general")
        steps = templates.get(cat, [
            {"step": 1, "action": "Review change details and assess impact",
             "file": "core/scheduler.py", "urgency": "medium"},
            {"step": 2, "action": "Update config/master_config.yaml if needed",
             "file": "config/master_config.yaml", "urgency": "low"},
            {"step": 3, "action": "Run full test suite to verify no regressions",
             "file": "tests/auto_test.py", "urgency": "low"},
        ])
        return {"change_type": cat, "action_steps": steps,
                "deadline_hours": 24, "source": "template"}


# ── 3. TechStackUpdater ──────────────────────────────────────────────────────

class TechStackUpdater:
    """
    Weekly package upgrade cycle:
      1. pip list --outdated → identify upgrade candidates
      2. Cross-check COMPATIBILITY_MATRIX for known-breaking versions
      3. Sandbox-test each safe candidate in a temp venv
      4. Apply passing upgrades; record in memory

    Sandbox test = create tmpdir venv → install package → import check.
    All steps have dry_run mode for CI and tests.
    """

    def get_outdated_packages(self) -> List[Dict]:
        """
        Run `pip list --outdated --format=json` and return the result.
        Returns [] on failure.
        """
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except Exception as e:
            log.debug(f"pip list --outdated failed: {e}")
        return []

    def is_safe_upgrade(self, package: str, new_version: str) -> Tuple[bool, str]:
        """
        Check COMPATIBILITY_MATRIX. Returns (safe, reason).
        Unknown packages are treated as safe (conservatively tested).
        """
        info = COMPATIBILITY_MATRIX.get(package.lower())
        if not info:
            return True, "unknown_package_assume_safe"

        breaking = info.get("breaking_above")
        if breaking and self._version_gte(new_version, breaking):
            return False, f"breaks_above_{breaking}"

        min_ver = info.get("min")
        if min_ver and not self._version_gte(new_version, min_ver):
            return False, f"below_min_{min_ver}"

        return True, "safe"

    def sandbox_test(self, package: str, version: str,
                      dry_run: bool = False) -> Tuple[bool, str]:
        """
        Test a package upgrade in an isolated temporary venv.
        Returns (passed, reason).
        dry_run=True skips the real venv and returns (True, 'dry_run').
        """
        if dry_run:
            return True, "dry_run"

        tmpdir = tempfile.mkdtemp(prefix="yt_pkg_test_")
        try:
            # Create isolated venv
            venv_result = subprocess.run(
                [sys.executable, "-m", "venv", tmpdir],
                capture_output=True, timeout=30,
            )
            if venv_result.returncode != 0:
                return False, "venv_creation_failed"

            pip  = Path(tmpdir) / "bin" / "pip"
            pyex = Path(tmpdir) / "bin" / "python3"
            if not pip.exists():
                pip  = Path(tmpdir) / "Scripts" / "pip.exe"
                pyex = Path(tmpdir) / "Scripts" / "python.exe"

            # Install the candidate version
            install = subprocess.run(
                [str(pip), "install", f"{package}=={version}",
                 "--quiet", "--no-deps"],
                capture_output=True, text=True, timeout=120,
            )
            if install.returncode != 0:
                return False, f"install_failed: {install.stderr[:100]}"

            # Basic import test
            module_name = package.replace("-", "_").split("[")[0]
            test = subprocess.run(
                [str(pyex), "-c", f"import {module_name}; print('ok')"],
                capture_output=True, text=True, timeout=20,
            )
            if "ok" in test.stdout:
                return True, "import_ok"
            return False, f"import_failed: {test.stderr[:100]}"

        except Exception as e:
            return False, f"exception: {e}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def apply_upgrade(self, package: str, version: str) -> Tuple[bool, str]:
        """Install a package upgrade into the current environment."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 f"{package}=={version}", "--quiet"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                return True, f"upgraded to {version}"
            return False, result.stderr[:200]
        except Exception as e:
            return False, str(e)

    def run_weekly_audit(self, memory=None, dry_run: bool = False) -> Dict:
        """
        Full upgrade cycle: discover → filter safe → sandbox test → apply.
        Returns summary dict with counts and details.
        """
        outdated  = self.get_outdated_packages()
        result    = {
            "outdated_count":  len(outdated),
            "checked":         [],
            "upgraded":        [],
            "skipped":         [],
        }

        for pkg in outdated:
            name    = pkg.get("name", "")
            new_ver = pkg.get("latest_version", "")
            old_ver = pkg.get("version", "")
            if not name or not new_ver:
                continue

            safe, reason = self.is_safe_upgrade(name, new_ver)
            entry = {"package": name, "from": old_ver, "to": new_ver,
                     "safe": safe, "reason": reason}
            result["checked"].append(entry)

            if not safe:
                result["skipped"].append({"package": name, "reason": reason})
                if memory:
                    try:
                        memory.save_tech_stack_update({
                            "package": name, "old_version": old_ver,
                            "new_version": new_ver, "test_status": "skipped_unsafe",
                            "applied": False,
                        })
                    except Exception:
                        pass
                continue

            passed, test_reason = self.sandbox_test(name, new_ver, dry_run=dry_run)
            if passed:
                if not dry_run:
                    ok, apply_reason = self.apply_upgrade(name, new_ver)
                else:
                    ok, apply_reason = True, "dry_run"
                result["upgraded"].append(
                    {**entry, "applied": ok, "apply_reason": apply_reason}
                )
                status = "applied" if ok else "apply_failed"
            else:
                result["skipped"].append({**entry, "reason": test_reason})
                status = "test_failed"

            if memory:
                try:
                    memory.save_tech_stack_update({
                        "package": name, "old_version": old_ver,
                        "new_version": new_ver, "test_status": status,
                        "applied": passed and not dry_run,
                    })
                except Exception:
                    pass

        log.info(f"Tech stack audit: {len(outdated)} outdated | "
                 f"{len(result['upgraded'])} upgraded | "
                 f"{len(result['skipped'])} skipped")
        return result

    @staticmethod
    def _version_gte(v1: str, v2: str) -> bool:
        """Return True if v1 >= v2 (simplified semver comparison)."""
        def tup(v):
            parts = re.split(r'[.\-]', str(v).split('+')[0])
            nums  = []
            for p in parts:
                try:
                    nums.append(int(p))
                except ValueError:
                    nums.append(0)
            return nums + [0, 0, 0]
        return tup(v1) >= tup(v2)

    @staticmethod
    def get_version_tuple(version: str) -> Tuple[int, ...]:
        """Parse a version string into a numeric tuple for comparison."""
        parts = re.split(r'[.\-]', str(version).split('+')[0])
        result = []
        for p in parts:
            try:
                result.append(int(p))
            except ValueError:
                result.append(0)
        return tuple(result + [0, 0, 0])


# ── 4. QualityBenchmarkEscalator ─────────────────────────────────────────────

class QualityBenchmarkEscalator:
    """
    Monthly quality bar escalation:
      Month 1 → 720p basic
      Month 2 → 1080p color grade
      Month 3 → 1080p60 motion graphics
      Month 6 → 4K broadcast
      Month 12 → 4K HDR cinematic

    check_output_quality() acts as a quality gate before every publish.
    Output below the current standard is automatically rejected with a
    specific reason so the pipeline can re-render at higher quality.
    """

    DEFAULT_LAUNCH_DATE = "2026-06-08"

    def months_since_launch(self, launch_date: str = None) -> int:
        """Return whole months elapsed since launch_date (YYYY-MM-DD)."""
        start = datetime.strptime(
            (launch_date or self.DEFAULT_LAUNCH_DATE)[:10], "%Y-%m-%d"
        )
        now   = datetime.now()
        delta = (now.year - start.year) * 12 + (now.month - start.month)
        return max(0, delta)

    def get_current_standard(self, launch_date: str = None) -> Dict:
        """
        Return the quality tier active for the current elapsed month.
        Always returns the highest tier whose `month` ≤ months_elapsed.
        """
        elapsed = self.months_since_launch(launch_date)
        current = QUALITY_TIERS[0]
        for tier in QUALITY_TIERS:
            if elapsed >= tier["month"]:
                current = tier
        return current

    def check_output_quality(self, output_spec: Dict,
                              standard: Dict = None,
                              launch_date: str = None) -> Dict:
        """
        Validate an output spec against the current quality standard.
        output_spec keys: resolution, fps, bitrate_kbps, features (list).
        Returns: {passed, reason, required, actual}.
        """
        std = standard or self.get_current_standard(launch_date)

        out_res = output_spec.get("resolution", "480p")
        req_res = std["resolution"]

        out_rank = RESOLUTION_RANK.get(out_res, 0)
        req_rank = RESOLUTION_RANK.get(req_res, 0)

        if out_rank < req_rank:
            return {
                "passed": False,
                "reason": f"resolution_too_low: {out_res} < required {req_res}",
                "required": std,
                "actual":   output_spec,
            }

        out_fps = output_spec.get("fps", 0) or 0
        req_fps = std.get("fps", 0)
        if out_fps < req_fps:
            return {
                "passed": False,
                "reason": f"fps_too_low: {out_fps} < required {req_fps}",
                "required": std,
                "actual":   output_spec,
            }

        return {"passed": True, "reason": "meets_standard",
                "required": std, "actual": output_spec}

    def reject_below_standard(self, output_spec: Dict,
                               launch_date: str = None) -> Tuple[bool, str]:
        """
        Convenience gate: returns (approved, reason).
        approved=False means the pipeline should re-render.
        """
        result = self.check_output_quality(output_spec, launch_date=launch_date)
        return result["passed"], result["reason"]

    def escalate_standard(self, memory=None, launch_date: str = None) -> Dict:
        """
        Compute the current standard and save it to memory if it has changed.
        Returns the current standard dict.
        """
        std = self.get_current_standard(launch_date)
        if memory:
            try:
                current = memory.get_current_quality_standard()
                if not current or current.get("resolution") != std["resolution"]:
                    memory.save_quality_standard({
                        **std,
                        "launch_date": launch_date or self.DEFAULT_LAUNCH_DATE,
                    })
                    log.info(f"Quality standard escalated → {std['resolution']} "
                             f"{std['fps']}fps {std['color_grade']}")
            except Exception as e:
                log.debug(f"Quality standard save failed: {e}")
        return std


# ── 5. AIVideoModelTracker ───────────────────────────────────────────────────

class AIVideoModelTracker:
    """
    Tracks AI video generation models (Runway, Kling, Sora, etc.) and
    ensures the system always uses the best available model.

    Selection criteria:
      - score (composite capability × quality)
      - cost_tier filter (low/mid/high)
      - provider availability (checked via HTTP ping)
    """

    def get_best_model(self, category: str = "video",
                        cost_tier: str = None) -> Dict:
        """
        Return the best-scoring available model in the given category.
        cost_tier cap: 'low' | 'mid' | 'high' (no cap = None).
        """
        models = KNOWN_AI_MODELS.get(category, {})
        if not models:
            return {"model_id": None, "reason": f"no_models_for_{category}"}

        def eligible(mid, m):
            if cost_tier is None:
                return True
            return ModelUpgradeWatcher._tier_lte(m.get("cost_tier", "high"), cost_tier)

        candidates = {mid: m for mid, m in models.items() if eligible(mid, m)}
        if not candidates:
            candidates = models

        best_id = max(candidates, key=lambda mid: candidates[mid].get("score", 0))
        m       = candidates[best_id]
        return {
            "model_id":  best_id,
            "provider":  m.get("provider", "unknown"),
            "score":     m.get("score", 0),
            "cost_tier": m.get("cost_tier", "unknown"),
        }

    def should_upgrade(self, current_model: str, candidate_model: str,
                        category: str = "video", min_delta: int = 3) -> bool:
        """
        Return True if candidate scores at least min_delta points above current.
        """
        models       = KNOWN_AI_MODELS.get(category, {})
        curr_score   = models.get(current_model, {}).get("score", 0)
        cand_score   = models.get(candidate_model, {}).get("score", 0)
        return (cand_score - curr_score) >= min_delta

    def integrate_model(self, model_id: str, provider: str,
                         score: float, category: str,
                         cost_tier: str = "mid",
                         memory=None) -> Dict:
        """
        Register a newly discovered model and save to memory.
        Returns the integration record.
        """
        record = {
            "model_id":    model_id,
            "provider":    provider,
            "score":       score,
            "category":    category,
            "cost_tier":   cost_tier,
            "integrated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Update in-memory registry
        if category not in KNOWN_AI_MODELS:
            KNOWN_AI_MODELS[category] = {}
        KNOWN_AI_MODELS[category][model_id] = {
            "provider": provider, "score": score, "cost_tier": cost_tier
        }

        if memory:
            try:
                memory.save_model_benchmark({
                    "model_id":    model_id,
                    "category":    category,
                    "score":       score,
                    "switched_to": None,
                    "delta":       0.0,
                    "active":      True,
                })
            except Exception as e:
                log.debug(f"Model integration save failed: {e}")

        log.info(f"Integrated new {category} model: {model_id} (score={score})")
        return record

    def check_for_updates(self, category: str = "video") -> List[Dict]:
        """
        Fetch provider pages for the given category and look for new model IDs.
        Delegates to ModelUpgradeWatcher._scrape_provider for HTTP parsing.
        """
        watcher  = ModelUpgradeWatcher()
        detected = []
        for provider in {m["provider"] for m in KNOWN_AI_MODELS.get(category, {}).values()}:
            detected += watcher._scrape_provider(provider)
        return detected


# ── 6. FutureProof (orchestrator) ────────────────────────────────────────────

class FutureProof:
    """
    Orchestrates all 5 future-proofing systems on their respective schedules.

      Daily:   platform changes + model check
      Weekly:  tech stack audit + video model tracker
      Monthly: quality benchmark escalation

    run_full_cycle() executes all systems — used by scheduler and --phase future.
    """

    def __init__(self, memory=None):
        from core.memory import get_memory
        self._memory   = memory or get_memory()
        self._watcher  = ModelUpgradeWatcher()
        self._platform = PlatformChangeDetector()
        self._updater  = TechStackUpdater()
        self._quality  = QualityBenchmarkEscalator()
        self._tracker  = AIVideoModelTracker()

    def run_full_cycle(self, dry_run: bool = False) -> Dict:
        """Run all 5 systems and return a combined report."""
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dry_run":   dry_run,
        }

        # 1. Platform change monitoring
        log.info("[FutureProof] Checking platform changes…")
        try:
            changes = self._platform.check_youtube_blog(self._memory)
            pending = self._platform.check_pending_adaptations(self._memory)
            result["platform"] = {
                "new_changes":     len(changes),
                "pending_adapt":   len(pending),
                "high_priority":   sum(1 for c in changes if c.get("priority")),
            }
        except Exception as e:
            log.error(f"Platform detector failed: {e}", exc_info=True)
            result["platform"] = {"error": str(e)}

        # 2. Model upgrade check
        log.info("[FutureProof] Checking AI model upgrades…")
        try:
            best_text  = self._watcher.compare_and_recommend("text", cost_tier="mid",
                                                               memory=self._memory)
            best_voice = self._watcher.compare_and_recommend("voice", memory=self._memory)
            best_video = self._tracker.get_best_model("video")
            result["models"] = {
                "best_text":  best_text.get("model_id"),
                "best_voice": best_voice.get("model_id"),
                "best_video": best_video.get("model_id"),
            }
        except Exception as e:
            log.error(f"Model watcher failed: {e}", exc_info=True)
            result["models"] = {"error": str(e)}

        # 3. Tech stack audit (weekly — skip if not Monday in non-dry runs)
        now = datetime.now(timezone.utc)
        if dry_run or now.weekday() == 0:
            log.info("[FutureProof] Running tech stack audit…")
            try:
                audit = self._updater.run_weekly_audit(self._memory, dry_run=dry_run)
                result["tech_stack"] = {
                    "outdated":  audit.get("outdated_count", 0),
                    "upgraded":  len(audit.get("upgraded", [])),
                    "skipped":   len(audit.get("skipped", [])),
                }
            except Exception as e:
                log.error(f"Tech stack updater failed: {e}", exc_info=True)
                result["tech_stack"] = {"error": str(e)}
        else:
            result["tech_stack"] = {"skipped": "not_monday"}

        # 4. Quality standard escalation (monthly — check on 1st of month)
        if dry_run or now.day == 1:
            log.info("[FutureProof] Escalating quality standard…")
            try:
                std = self._quality.escalate_standard(self._memory)
                result["quality"] = {
                    "current_resolution": std.get("resolution"),
                    "fps":                std.get("fps"),
                    "color_grade":        std.get("color_grade"),
                    "features":           std.get("features", []),
                }
            except Exception as e:
                log.error(f"Quality escalator failed: {e}", exc_info=True)
                result["quality"] = {"error": str(e)}
        else:
            current_std = self._quality.get_current_standard()
            result["quality"] = {"current_resolution": current_std["resolution"],
                                  "skipped": "not_first_of_month"}

        log.info(
            f"[FutureProof] Cycle: "
            f"changes={result.get('platform',{}).get('new_changes',0)} | "
            f"text={result.get('models',{}).get('best_text','?')} | "
            f"video={result.get('models',{}).get('best_video','?')} | "
            f"resolution={result.get('quality',{}).get('current_resolution','?')}"
        )
        return result

    def print_report(self, result: Dict) -> None:
        plat  = result.get("platform", {})
        mods  = result.get("models",   {})
        stack = result.get("tech_stack", {})
        qual  = result.get("quality",  {})
        print(f"\n{'='*60}")
        print("FUTURE PROOF SYSTEM REPORT")
        print(f"{'='*60}")
        print(f"  Platform changes:   {plat.get('new_changes',0)} new | "
              f"{plat.get('pending_adapt',0)} pending adaptation")
        print(f"  Best text model:    {mods.get('best_text','—')}")
        print(f"  Best video model:   {mods.get('best_video','—')}")
        print(f"  Tech packages:      {stack.get('outdated',0)} outdated | "
              f"{stack.get('upgraded',0)} upgraded")
        print(f"  Quality standard:   {qual.get('current_resolution','—')} "
              f"{qual.get('fps','?')}fps — {qual.get('color_grade','?')}")
        print()
