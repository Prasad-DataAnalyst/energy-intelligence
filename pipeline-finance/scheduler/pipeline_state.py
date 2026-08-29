"""
scheduler/pipeline_state.py — DriftWire326 reliability layer
Checkpoint/resume support for daily pipelines.

Each pipeline run records completed steps and their artifact paths to
logs/pipeline_state/<pipeline>_<YYYY-MM-DD>.json (atomic writes).
A crashed run can resume from the first incomplete step instead of
re-scraping and re-calling Claude from scratch.

The state file doubles as the daily run report: step timings, retries,
errors, and final outcome are all recorded in one place.
"""
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from config.settings import settings

logger = logging.getLogger(__name__)

STATE_DIR = settings.logs_dir / "pipeline_state"

# Canonical step names for the weekday pipeline (order matters)
WEEKDAY_STEPS = [
    "scrape_market",
    "scrape_earnings",
    "scrape_economic",
    "generate_script",
    "compliance_check",
    "generate_assets",      # charts + audio + title + thumbnail
    "build_video",
    "upload",
    "post_upload",          # playlist, captions, pinned comment, manifest
]

SUNDAY_STEPS = [
    "pick_topic",
    "generate_script",
    "compliance_check",
    "generate_assets",
    "build_video",
    "upload",
    "post_upload",
]


class PipelineState:
    """
    Persistent checkpoint tracker for one pipeline day.

    Usage:
        state = PipelineState("weekday")
        if not state.is_done("scrape_market"):
            data = scrape()
            state.mark_done("scrape_market", artifacts={"market_json": str(path)})
        else:
            data = load(state.artifact("scrape_market", "market_json"))
    """

    def __init__(self, pipeline: str, run_date: Optional[str] = None,
                 slot: Optional[str] = None):
        self.pipeline = pipeline
        self.slot = slot
        self.run_date = run_date or date.today().isoformat()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        # A pipeline that runs more than once a day needs one checkpoint per
        # slot. Keying on the date alone meant the 5:15pm post-market run saw
        # the 8am run's success and returned without doing anything, so that
        # slot never produced a video. Sunday has one slot a week and keeps
        # the plain name, which is also the name older files already use.
        stem = f"{pipeline}_{slot}" if slot else pipeline
        self._path = STATE_DIR / f"{stem}_{self.run_date}.json"
        self._data: dict = self._load()

    @property
    def exists(self) -> bool:
        """Whether this slot has a checkpoint file — i.e. it ever started."""
        return self._path.exists()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Corrupt pipeline state %s (%s) — starting fresh", self._path.name, exc)
        return {
            "pipeline": self.pipeline,
            "slot": self.slot,
            "run_date": self.run_date,
            "started_at": datetime.now().isoformat(),
            "steps": {},          # step -> {done, at, seconds, attempts, artifacts, error}
            "outcome": None,      # "success" | "failed" | None (running)
            "video_id": None,
            "errors": [],
        }

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self._path)

    # ── Step tracking ─────────────────────────────────────────────────────────

    def is_done(self, step: str) -> bool:
        return bool(self._data["steps"].get(step, {}).get("done"))

    def mark_started(self, step: str) -> None:
        entry = self._data["steps"].setdefault(step, {"done": False, "attempts": 0})
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["started_at"] = datetime.now().isoformat()
        self._save()

    def mark_done(self, step: str, artifacts: Optional[dict[str, Any]] = None) -> None:
        entry = self._data["steps"].setdefault(step, {"attempts": 1})
        entry["done"] = True
        entry["at"] = datetime.now().isoformat()
        started = entry.get("started_at")
        if started:
            try:
                entry["seconds"] = round(
                    (datetime.now() - datetime.fromisoformat(started)).total_seconds(), 1
                )
            except ValueError:
                pass
        if artifacts:
            entry.setdefault("artifacts", {}).update(
                {k: str(v) for k, v in artifacts.items()}
            )
        entry.pop("error", None)
        self._save()
        logger.info("[checkpoint] %s/%s done", self.pipeline, step)

    def mark_failed(self, step: str, error: str) -> None:
        entry = self._data["steps"].setdefault(step, {"attempts": 1})
        entry["done"] = False
        entry["error"] = error[:500]
        self._data["errors"].append(f"{step}: {error[:200]}")
        self._save()
        logger.warning("[checkpoint] %s/%s FAILED: %s", self.pipeline, step, error[:120])

    def artifact(self, step: str, key: str) -> Optional[str]:
        """Return a recorded artifact path/value for a completed step."""
        return self._data["steps"].get(step, {}).get("artifacts", {}).get(key)

    def artifact_path(self, step: str, key: str) -> Optional[Path]:
        """Like artifact() but returns a Path only if the file still exists."""
        raw = self.artifact(step, key)
        if raw:
            p = Path(raw)
            if p.exists():
                return p
        return None

    # ── Run outcome ──────────────────────────────────────────────────────────

    def finish(self, success: bool, video_id: Optional[str] = None) -> None:
        self._data["outcome"] = "success" if success else "failed"
        self._data["finished_at"] = datetime.now().isoformat()
        if video_id:
            self._data["video_id"] = video_id
        self._save()

    @property
    def outcome(self) -> Optional[str]:
        return self._data.get("outcome")

    @property
    def video_id(self) -> Optional[str]:
        return self._data.get("video_id")

    def next_step(self, steps: Optional[list[str]] = None) -> Optional[str]:
        """Return the first incomplete step, or None if all are done."""
        step_list = steps or (WEEKDAY_STEPS if self.pipeline == "weekday" else SUNDAY_STEPS)
        for step in step_list:
            if not self.is_done(step):
                return step
        return None

    def summary(self) -> dict:
        """Compact run report for logging/monitoring/dashboard."""
        done = [s for s, e in self._data["steps"].items() if e.get("done")]
        return {
            "pipeline": self.pipeline,
            "run_date": self.run_date,
            "outcome": self._data.get("outcome"),
            "video_id": self._data.get("video_id"),
            "steps_done": done,
            "next_step": self.next_step(),
            "errors": self._data.get("errors", []),
        }


def state_files(pipeline: str, run_date: Optional[str] = None) -> list[Path]:
    """
    Every checkpoint file for one pipeline on one day, across all slots.

    Matches both the per-slot names and the older single-file name, so a
    machine carrying state written before slots existed still reads back.
    """
    day = run_date or date.today().isoformat()
    if not STATE_DIR.exists():
        return []
    found = {STATE_DIR / f"{pipeline}_{day}.json"} | set(
        STATE_DIR.glob(f"{pipeline}_*_{day}.json"))
    return sorted(path for path in found if path.exists())


def incomplete_slots(pipeline: str, run_date: Optional[str] = None) -> list[str]:
    """
    Slots that started today and did not reach success — what a retry should
    pick up. A slot that never started is not incomplete: its own scheduled
    time either has not arrived or was missed, and the main job owns that.
    """
    day = run_date or date.today().isoformat()
    pending: list[str] = []
    for path in state_files(pipeline, day):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Unreadable state %s (%s) — treating as incomplete",
                           path.name, exc)
            data = {}
        if data.get("outcome") != "success":
            stem = path.stem[len(pipeline) + 1:-len(day) - 1]
            pending.append(stem or None)
    return pending


def needs_retry(pipeline: str, run_date: Optional[str] = None) -> bool:
    """
    True if any of today's slots started but did not reach a successful
    outcome. Used by the retry job scheduled after each main slot.
    """
    return bool(incomplete_slots(pipeline, run_date))


def todays_upload_recorded() -> bool:
    """
    Dead-man's-switch data source: True if the upload manifest has an
    entry dated today. Checks logs/upload_manifest.jsonl.
    """
    from uploader.uploader import load_upload_manifest
    today = date.today().isoformat()
    for record in load_upload_manifest():
        uploaded_at = record.get("uploaded_at", "")
        if uploaded_at.startswith(today):
            return True
    return False
