"""
channel_manager/performance_tracker.py — DriftWire326 Module 31
PerformanceTracker: learns which styles, hooks, templates, and upload times
perform best, using Exponential Moving Average (alpha=0.3) to weight
recent performance more heavily.

State persisted to logs/performance_state.json.
Called after analytics pulls to update EMA scores.
Exposes get_best_* methods used by schedulers to pick optimal settings.
"""
import json
import random
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

_STATE_PATH = settings.logs_dir / "performance_state.json"
_EMA_ALPHA = 0.3  # weight for newest observation

# How often to pick something other than the current leader. Without this
# the first option to land on a good day wins forever and nothing else ever
# gets a score — the channel would optimise itself into whatever it tried
# first. A quarter keeps the alternatives alive at a small cost.
EXPLORE_RATE = 0.25

# Observations before a score is treated as signal rather than one lucky day.
MIN_OBSERVATIONS = 3


def _ema_update(current: float, new_value: float, alpha: float = _EMA_ALPHA) -> float:
    """Exponential Moving Average update: new_ema = alpha * value + (1 - alpha) * current."""
    return round(alpha * new_value + (1 - alpha) * current, 4)


@dataclass
class ScoreEntry:
    label: str
    score: float = 0.0
    observations: int = 0
    last_updated: str = ""

    def update(self, new_score: float) -> None:
        self.score = _ema_update(self.score, new_score)
        self.observations += 1
        self.last_updated = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "score": self.score,
            "observations": self.observations,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScoreEntry":
        return cls(
            label=d.get("label", ""),
            score=float(d.get("score", 0.0)),
            observations=int(d.get("observations", 0)),
            last_updated=d.get("last_updated", ""),
        )


def apply_recent_performance(days: int = 30) -> int:
    """
    Feed observed results back into the scores. Returns videos scored.

    This is the half of the loop that was missing. Selection without
    feedback is just a slower random choice, and feedback without selection
    is a spreadsheet nobody reads — the pieces existed separately and never
    met. Joins the analytics files to the upload manifest, which is the only
    place a video's style and hook are recorded.

    Idempotent across runs: a video is scored once and remembered, so
    re-running the weekly job does not stack the same result repeatedly and
    inflate whatever ran most recently.
    """
    import json as _json
    from datetime import date, timedelta

    try:
        from uploader.uploader import load_upload_manifest
        manifest = {r["video_id"]: r for r in (load_upload_manifest() or [])
                    if r.get("video_id")}
    except Exception as exc:
        logger.warning("Manifest unavailable for scoring: %s", exc)
        return 0
    if not manifest:
        return 0

    analytics_dir = settings.logs_dir / "analytics"
    if not analytics_dir.exists():
        return 0

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    latest: dict = {}
    for path in sorted(analytics_dir.glob("*.json")):
        if path.name.startswith("weekly_report_") or path.stem < cutoff:
            continue
        try:
            for entry in _json.loads(path.read_text(encoding="utf-8")):
                if entry.get("video_id"):
                    latest[entry["video_id"]] = entry
        except Exception:
            continue

    tracker = PerformanceTracker()
    scored = set(tracker._state.get("scored_videos", {}))
    counted = 0
    for video_id, stats in latest.items():
        record = manifest.get(video_id)
        if not record or video_id in scored:
            continue
        style, hook = record.get("script_style"), record.get("script_hook")
        if not style and not hook:
            continue          # published before attribution existed
        tracker.record_performance(
            style=style or None,
            hook=hook or None,
            time_slot=(record.get("uploaded_at") or "")[11:16] or None,
            ctr=float(stats.get("ctr", 0) or 0),
            views=int(stats.get("views", 0) or 0),
            watch_time_minutes=float(stats.get("watch_time_minutes", 0) or 0),
        )
        scored.add(video_id)
        counted += 1

    if counted:
        tracker._state["scored_videos"] = {v: ScoreEntry(label=v) for v in scored}
        tracker._save()
        logger.info("Performance applied for %d video(s)", counted)
    return counted


class PerformanceTracker:
    """
    Tracks EMA-weighted performance scores for:
      - Script styles (energetic, calm, analytical, storytelling, …)
      - Hook types (stat_first, question_hook, bold_claim, …)
      - Thumbnail templates (A-G)
      - Upload time slots (e.g., "08:00", "09:00", "17:00")
    """

    def __init__(self, state_path: Optional[Path] = None):
        self._path = state_path or _STATE_PATH
        self._state: dict[str, dict[str, ScoreEntry]] = {}
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            self._state = {
                "styles": {},
                "hooks": {},
                "templates": {},
                "time_slots": {},
            }
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._state = {
                category: {
                    label: ScoreEntry.from_dict(entry)
                    for label, entry in entries.items()
                }
                for category, entries in raw.items()
            }
        except Exception as exc:
            logger.warning("PerformanceTracker state load failed: %s — using empty state", exc)
            self._state = {"styles": {}, "hooks": {}, "templates": {}, "time_slots": {}}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        data = {
            category: {
                label: entry.to_dict()
                for label, entry in entries.items()
            }
            for category, entries in self._state.items()
        }
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        logger.debug("PerformanceTracker state saved → %s", self._path.name)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_or_create(self, category: str, label: str) -> ScoreEntry:
        if category not in self._state:
            self._state[category] = {}
        if label not in self._state[category]:
            self._state[category][label] = ScoreEntry(label=label)
        return self._state[category][label]

    # ── Update methods ────────────────────────────────────────────────────────

    def record_performance(
        self,
        style: Optional[str] = None,
        hook: Optional[str] = None,
        template: Optional[str] = None,
        time_slot: Optional[str] = None,
        ctr: float = 0.0,
        views: int = 0,
        watch_time_minutes: float = 0.0,
    ) -> None:
        """
        Record observed performance metrics and update EMA scores.
        Composite score = 0.5*ctr_normalized + 0.3*views_normalized + 0.2*watch_time_normalized.
        Caller normalizes inputs to [0, 1] range or passes raw values.
        """
        # Simple composite score: weight CTR most heavily
        composite = min(ctr * 10 + views / 10000 + watch_time_minutes / 100, 1.0)

        updates = [
            ("styles", style),
            ("hooks", hook),
            ("templates", template),
            ("time_slots", time_slot),
        ]
        for category, label in updates:
            if label:
                entry = self._get_or_create(category, label)
                entry.update(composite)

        self._save()
        logger.info(
            "Performance recorded: style=%s hook=%s template=%s slot=%s score=%.3f",
            style, hook, template, time_slot, composite,
        )

    def update_from_video_stats(
        self,
        video_stats: dict,
        style: Optional[str] = None,
        hook: Optional[str] = None,
        template: Optional[str] = None,
        time_slot: Optional[str] = None,
    ) -> None:
        """
        Convenience: update from a VideoStats dict (from AnalyticsTracker).
        Expected keys: ctr (float 0-1), views (int), avg_watch_seconds (float).
        """
        ctr = float(video_stats.get("ctr", 0.0))
        views = int(video_stats.get("views", 0))
        watch_minutes = float(video_stats.get("avg_watch_seconds", 0)) / 60
        self.record_performance(
            style=style,
            hook=hook,
            template=template,
            time_slot=time_slot,
            ctr=ctr,
            views=views,
            watch_time_minutes=watch_minutes,
        )

    # ── Query methods ─────────────────────────────────────────────────────────

    def choose(self, category: str, options: list, default: str = "") -> str:
        """
        Pick an option, balancing what has worked against what is untested.

        Always taking the highest score would be wrong here twice over. With
        a handful of videos the leader is noise, and an option that never
        gets picked never earns a score — so whichever one happened to run on
        a good day would win permanently and the rest would stay at zero
        forever. So: try everything at least once, keep sampling
        alternatives a quarter of the time, and only trust a score once it
        has enough observations behind it to mean something.
        """
        if not options:
            return default
        entries = self._state.get(category, {})

        untried = [o for o in options
                   if o not in entries or entries[o].observations == 0]
        if untried:
            return random.choice(untried)

        if random.random() < EXPLORE_RATE:
            return random.choice(options)

        known = [entries[o] for o in options if o in entries]
        proven = [e for e in known if e.observations >= MIN_OBSERVATIONS]
        pool = proven or known
        return max(pool, key=lambda e: e.score).label if pool else default

    def get_best(self, category: str, default: str = "") -> str:
        """Return the highest-scoring label in a category, or default if empty."""
        entries = self._state.get(category, {})
        if not entries:
            return default
        return max(entries.values(), key=lambda e: e.score).label

    def get_best_style(self, default: str = "energetic") -> str:
        return self.get_best("styles", default)

    def get_best_hook(self, default: str = "stat_first") -> str:
        return self.get_best("hooks", default)

    def get_best_template(self, default: str = "A") -> str:
        return self.get_best("templates", default)

    def get_best_time_slot(self, default: str = "09:00") -> str:
        return self.get_best("time_slots", default)

    def get_scores(self, category: str) -> list[dict]:
        """Return all scores for a category, sorted by score descending."""
        entries = self._state.get(category, {})
        return sorted(
            [e.to_dict() for e in entries.values()],
            key=lambda d: d["score"],
            reverse=True,
        )

    def get_summary(self) -> dict:
        """Return best pick for each category — used by schedulers."""
        return {
            "best_style": self.get_best_style(),
            "best_hook": self.get_best_hook(),
            "best_template": self.get_best_template(),
            "best_time_slot": self.get_best_time_slot(),
        }
