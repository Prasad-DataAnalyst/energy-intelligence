"""
evolution/meta_learner.py — Meta-Learning Layer
================================================
The system thinking about its own thinking.

After every N videos (default 3), the MetaLearner:

  1. GATHERS a self-report — performance trend, recurring errors, code-upgrade
     success rate, learned config correlations, A/B outcomes.
  2. WRITES a self-improvement plan via Claude — a prioritised, *machine-executable*
     list of actions (tune a config value, audit a weak module, apply learned
     settings, prefer/avoid a style…).
  3. EXECUTES the plan — only safe, allow-listed actions are run automatically;
     everything is logged so the next cycle can judge whether it worked.

This closes the highest loop in the system: not "make a better video" but
"make a better video-making machine."

Cadence is video-count based (every 3 videos), distinct from the time-based
weekly code audit — meta-learning reacts to *output*, the audit reacts to the
*calendar*.
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

log = logging.getLogger("brain.meta_learner")

HERE    = Path(__file__).parent.parent
CFG_DIR = HERE / "config"
PLAN_DIR = HERE / "data" / "improvement_plans"

# Run a meta-learning cycle every N produced videos
CADENCE = 3

# Config keys the meta-learner is allowed to tune autonomously.
# Anything outside this allow-list is logged but NOT applied — a safety rail
# so the system can't rewrite arbitrary config (e.g. API providers) on a whim.
TUNABLE_KEYS = {
    "video.duration_seconds":            (int,   180, 1200),
    "video.style_preset":                (str,   None, None),
    "video.fps":                         (int,   12,  60),
    "script.hook_style":                 (str,   None, None),
    "thumbnails.preferred_variant":      (str,   None, None),
    "publishing.preferred_upload_hour_utc": (int, 0,   23),
}

VALID_STYLES = {"tech-dark", "vlog-warm", "news-clean", "motivation-epic"}

META_SYSTEM = """You are the meta-cognition layer of an autonomous YouTube system.
You analyse the system's OWN performance and code health, then produce a concrete,
prioritised self-improvement plan as machine-executable actions.
Return ONLY valid JSON. No prose, no markdown fences."""

META_PROMPT = """Here is the system's self-report after producing {video_count} videos.

PERFORMANCE TREND (oldest→newest):
{trend}

ERROR HEALTH:
{errors}

CODE UPGRADE HISTORY:
{upgrades}

LEARNED CONFIG CORRELATIONS (best CTR first):
{correlations}

CURRENT CONFIG (relevant slice):
{current_config}

PREVIOUS META PLANS (most recent first — judge if they worked):
{previous_plans}

Produce an improvement plan. Each action MUST be one of these executable types:

- {{"action": "tune_config", "key": "<one of: {tunable}>", "value": <new value>, "reason": "..."}}
- {{"action": "audit_file", "path": "<relative .py path>", "reason": "..."}}
- {{"action": "apply_learned_settings", "reason": "..."}}
- {{"action": "prefer_style", "style": "<tech-dark|vlog-warm|news-clean|motivation-epic>", "reason": "..."}}
- {{"action": "note", "text": "...", "reason": "..."}}   # observation only, no execution

Rules:
- Base every action on the data above. If CTR is declining, diagnose why and act.
- If a module shows recurring errors, schedule an audit_file for it.
- Prefer 1-4 high-impact actions over many shallow ones.
- Only tune_config keys from the allowed list.

Return JSON:
{{
  "diagnosis": "2-3 sentence summary of the system's current state",
  "priority": "what matters most right now",
  "actions": [ ... ],
  "expected_impact": "what should improve by the next cycle"
}}"""


class MetaLearner:
    def __init__(self, memory=None, model: str = "claude-sonnet-4-6"):
        from core.memory import get_memory
        self.memory = memory or get_memory()
        self.model  = model
        self._client: Optional[anthropic.Anthropic] = None

    def _claude(self) -> Optional[anthropic.Anthropic]:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            return None
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    # ── Trigger logic ──────────────────────────────────────────────────────────

    def should_run(self) -> bool:
        """True when we've hit a fresh N-video milestone not yet processed."""
        count = self.memory.video_count()
        if count == 0 or count % CADENCE != 0:
            return False
        milestone = count // CADENCE
        return milestone > self.memory.last_meta_milestone()

    # ── 1. Gather self-report ────────────────────────────────────────────────────

    def gather_self_report(self) -> Dict[str, Any]:
        videos = self.memory.get_video_stats(n=30)
        # oldest→newest for trend readability
        trend = [
            {
                "date":    v.get("date", ""),
                "title":   (v.get("title") or "")[:50],
                "style":   v.get("script_style", ""),
                "length":  v.get("video_length_s", 0),
                "ctr":     v.get("ctr", 0),
                "avd_pct": v.get("avd_pct", 0),
                "rpm":     v.get("rpm", 0),
            }
            for v in reversed(videos)
        ]
        return {
            "video_count":  self.memory.video_count(),
            "trend":        trend,
            "errors":       self.memory.error_summary(),
            "upgrades":     self.memory.upgrade_summary(),
            "correlations": self.memory.config_correlations(),
            "summary":      self.memory.performance_summary(),
        }

    # ── 2. Write improvement plan ────────────────────────────────────────────────

    def _current_config_slice(self) -> Dict:
        path = CFG_DIR / "master_config.yaml"
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            return {}
        return {
            "video":       cfg.get("video", {}),
            "script":      cfg.get("script", {}),
            "thumbnails":  cfg.get("thumbnails", {}),
            "publishing":  cfg.get("publishing", {}),
        }

    def write_improvement_plan(self, report: Dict) -> Dict:
        client = self._claude()
        if not client:
            return self._fallback_plan(report)

        prev = self.memory.recent_meta_runs(n=3)
        prev_brief = [
            {"milestone": p.get("milestone"),
             "priority": json.loads(p.get("plan") or "{}").get("priority", ""),
             "actions_done": p.get("actions_done", 0)}
            for p in prev
        ]

        prompt = META_PROMPT.format(
            video_count=report["video_count"],
            trend=json.dumps(report["trend"], indent=2)[:2500],
            errors=json.dumps(report["errors"], indent=2)[:1200],
            upgrades=json.dumps(report["upgrades"], indent=2),
            correlations=json.dumps(report["correlations"], indent=2)[:1500],
            current_config=json.dumps(self._current_config_slice(), indent=2)[:1500],
            previous_plans=json.dumps(prev_brief, indent=2)[:1000],
            tunable=", ".join(sorted(TUNABLE_KEYS)),
        )
        try:
            msg = client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=META_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            m   = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                plan = json.loads(m.group())
                plan.setdefault("actions", [])
                log.info(f"Meta plan: {plan.get('priority','')[:80]}")
                return plan
        except Exception as e:
            log.warning(f"Meta plan generation failed: {e}")
        return self._fallback_plan(report)

    def _fallback_plan(self, report: Dict) -> Dict:
        """Rule-based plan when Claude is unavailable — still genuinely useful."""
        actions: List[Dict] = []
        trend = report.get("trend", [])

        # If CTR is trending down over the last 3 vs prior, prefer best-known style.
        if len(trend) >= 4:
            recent = [t["ctr"] for t in trend[-3:] if t.get("ctr")]
            older  = [t["ctr"] for t in trend[:-3] if t.get("ctr")]
            if recent and older and (sum(recent)/len(recent)) < (sum(older)/len(older)):
                actions.append({
                    "action": "apply_learned_settings",
                    "reason": "Recent CTR below earlier average — revert to best-known settings",
                })

        # Audit any module with recurring errors.
        for rec in report.get("errors", {}).get("recurring", [])[:2]:
            mod = (rec.get("module") or "").replace(".", "/")
            if mod:
                actions.append({
                    "action": "audit_file",
                    "path": f"{mod}.py",
                    "reason": f"{rec.get('count')}× {rec.get('error_type')} errors",
                })

        # Prefer the best-correlated style if we have a confident winner.
        best = self.memory.get_best_config("style_preset")
        if best in VALID_STYLES:
            actions.append({
                "action": "prefer_style", "style": best,
                "reason": "Highest historical CTR style",
            })

        if not actions:
            actions.append({
                "action": "note",
                "text": "Performance stable; continue current strategy and keep collecting data.",
                "reason": "No negative signal detected",
            })

        return {
            "diagnosis": "Heuristic self-review (no LLM available).",
            "priority":  "Stabilise CTR and clear recurring errors.",
            "actions":   actions,
            "expected_impact": "Fewer errors and CTR held at or above best-known config.",
        }

    # ── 3. Execute plan ──────────────────────────────────────────────────────────

    def execute_plan(self, plan: Dict) -> Dict:
        results: List[Dict] = []
        for action in plan.get("actions", []):
            kind = action.get("action", "")
            try:
                if kind == "tune_config":
                    res = self._do_tune_config(action)
                elif kind == "audit_file":
                    res = self._do_audit_file(action)
                elif kind == "apply_learned_settings":
                    res = self._do_apply_learned()
                elif kind == "prefer_style":
                    res = self._do_prefer_style(action)
                elif kind == "note":
                    res = {"status": "noted", "text": action.get("text", "")}
                else:
                    res = {"status": "skipped", "why": f"unknown action {kind!r}"}
            except Exception as e:
                res = {"status": "error", "why": str(e)}
            res["action"] = kind
            results.append(res)
            log.info(f"  [{kind}] → {res.get('status')}")

        done = sum(1 for r in results if r.get("status") in ("applied", "noted", "audited"))
        return {"results": results, "actions_done": done}

    def _do_tune_config(self, action: Dict) -> Dict:
        key   = action.get("key", "")
        value = action.get("value")
        if key not in TUNABLE_KEYS:
            return {"status": "skipped", "why": f"{key!r} not in allow-list"}

        typ, lo, hi = TUNABLE_KEYS[key]
        # Coerce + validate
        try:
            value = typ(value)
        except (TypeError, ValueError):
            return {"status": "skipped", "why": f"value {value!r} not {typ.__name__}"}
        if typ is int and lo is not None and not (lo <= value <= hi):
            return {"status": "skipped", "why": f"{value} outside [{lo},{hi}]"}
        if key == "video.style_preset" and value not in VALID_STYLES:
            return {"status": "skipped", "why": f"unknown style {value!r}"}

        path = CFG_DIR / "master_config.yaml"
        if not path.exists():
            return {"status": "skipped", "why": "master_config.yaml missing"}
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        section, leaf = key.split(".", 1)
        old = cfg.get(section, {}).get(leaf)
        if str(old) == str(value):
            return {"status": "skipped", "why": "already at target value"}
        cfg.setdefault(section, {})[leaf] = value
        with open(path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
        return {"status": "applied", "key": key, "old": old, "new": value}

    def _do_audit_file(self, action: Dict) -> Dict:
        rel = (action.get("path") or "").lstrip("/")
        target = (HERE / rel).resolve()
        # Safety: must stay inside the engine and be a real .py file
        if HERE.resolve() not in target.parents or target.suffix != ".py" \
                or not target.exists():
            return {"status": "skipped", "why": f"invalid target {rel!r}"}

        from evolution.code_auditor import CodeAuditor
        auditor  = CodeAuditor(self.memory, model=self.model)
        improved = auditor.audit_file(target)
        if not improved or not auditor._validate_syntax(improved):
            return {"status": "skipped", "why": "no valid improvement produced"}
        original = target.read_text()
        if not auditor._meaningful_change(original, improved):
            return {"status": "skipped", "why": "no meaningful change"}

        if auditor.apply_upgrade(target, improved) and auditor.run_tests():
            self.memory.log_upgrade(
                file_path=str(target), reason="meta_learner",
                original_hash=auditor._code_hash(original),
                improved_hash=auditor._code_hash(improved),
                test_passed=True,
            )
            return {"status": "audited", "path": rel}
        # tests failed → roll back
        bak = target.with_suffix(".py.bak")
        if bak.exists():
            target.write_text(bak.read_text())
        return {"status": "skipped", "why": "tests failed, rolled back"}

    def _do_apply_learned(self) -> Dict:
        from core.brain import get_brain
        get_brain().apply_learned_settings()
        return {"status": "applied", "what": "learned settings → master_config.yaml"}

    def _do_prefer_style(self, action: Dict) -> Dict:
        style = action.get("style", "")
        if style not in VALID_STYLES:
            return {"status": "skipped", "why": f"unknown style {style!r}"}
        return self._do_tune_config(
            {"key": "video.style_preset", "value": style}
        )

    # ── Full cycle ───────────────────────────────────────────────────────────────

    def run_cycle(self) -> Dict:
        count     = self.memory.video_count()
        milestone = count // CADENCE
        log.info("━" * 55)
        log.info(f"META-LEARNER — cycle at {count} videos (milestone {milestone})")

        report = self.gather_self_report()
        plan   = self.write_improvement_plan(report)
        execd  = self.execute_plan(plan)

        # Persist plan to disk for auditability
        PLAN_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(PLAN_DIR / f"plan_{milestone:04d}_{stamp}.json", "w") as f:
            json.dump({"report": report, "plan": plan, "execution": execd},
                      f, indent=2)

        self.memory.record_meta_run(
            video_count=count, milestone=milestone,
            report=report, plan=plan, results=execd,
            actions_done=execd.get("actions_done", 0),
        )

        log.info(f"  Diagnosis: {plan.get('diagnosis','')[:100]}")
        log.info(f"  Actions executed: {execd.get('actions_done', 0)}"
                 f"/{len(plan.get('actions', []))}")
        return {"milestone": milestone, "plan": plan, "execution": execd}

    def maybe_run(self) -> Optional[Dict]:
        """Run a cycle only if a fresh milestone has been reached."""
        if self.should_run():
            return self.run_cycle()
        return None
