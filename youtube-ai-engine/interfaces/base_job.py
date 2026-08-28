"""
interfaces/base_job.py — Abstract Base Class for Scheduled Jobs
================================================================
Every APScheduler-managed job in the engine extends ``BaseJob``.

BaseJob extends BaseModule with scheduling metadata and an
``execute()`` method that:
  - checks whether the job is enabled via config
  - delegates to ``run_safe()`` for fault-tolerant execution
  - records wall-clock duration and a result summary to memory

Subclass Contract
-----------------
    class MyJob(BaseJob):
        name     = "my_job"
        category = "intelligence"
        schedule = "0 2 * * *"          # cron expression (string)
        # — or —
        schedule = {"hour": 2}          # APScheduler-style dict
        job_id   = "my_job_daily"
        job_name = "My Job — Daily Run"

        def run(self) -> Dict:
            ...
            return {"items_processed": 42}

The scheduler (``core.scheduler``) reads ``schedule``, ``job_id``,
and ``is_enabled()`` to decide whether and when to fire ``execute()``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Union

from interfaces.base_module import BaseModule


class BaseJob(BaseModule):
    """Base class for all scheduled engine jobs.

    Adds scheduling metadata on top of ``BaseModule`` and wraps
    execution in timing + memory logging.
    """

    # -- Class-level scheduling attributes (override in subclass) -----------

    schedule: Union[str, Dict] = "0 2 * * *"
    """Cron expression string (e.g. ``"0 2 * * *"``) or APScheduler
    trigger-kwargs dict (e.g. ``{"hour": 2, "minute": 0}``)."""

    job_id: str = ""
    """Globally unique snake_case identifier used by the scheduler,
    e.g. ``"trend_hunter_daily"``."""

    job_name: str = ""
    """Human-readable job name shown in scheduler logs and dashboards,
    e.g. ``"Trend Hunter — Daily Run"``."""

    # -- Execution entry-point -----------------------------------------------

    def execute(self) -> Dict:
        """Run the job, log duration and a result summary to memory.

        The method checks ``is_enabled()`` first; if the job is disabled
        in config it returns immediately without calling ``run_safe()``.

        Returns
        -------
        Dict
            Result dict from ``run_safe()``, or ``{"skipped": True}``
            when the job is disabled.  Never raises.
        """
        if not self.is_enabled():
            self.log.info(
                "Job %s (%s) is disabled in config — skipping", self.job_id, self.name
            )
            return {"skipped": True, "reason": "disabled_in_config"}

        started_at = datetime.now(timezone.utc)
        self.log.info(
            "Job %s starting at %s", self.job_id, started_at.isoformat()
        )

        result: Dict = self.run_safe()

        finished_at = datetime.now(timezone.utc)
        duration_s = round(
            (finished_at - started_at).total_seconds(), 3
        )

        self.log.info(
            "Job %s finished in %.3fs — result_keys=%s",
            self.job_id,
            duration_s,
            list(result.keys()) if result else [],
        )

        # Persist a lightweight execution record to memory
        self._log_job_run(
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_s=duration_s,
            result=result,
        )

        return result

    # -- Enable/disable gate -------------------------------------------------

    def is_enabled(self) -> bool:
        """Return True unless config explicitly disables this job.

        Reads ``jobs.<job_id>.enabled`` from ``core.config_manager``.
        Defaults to ``True`` when the key is absent or the config
        manager cannot be imported.
        """
        config_key = f"jobs.{self.job_id}.enabled"
        return bool(self.get_config(config_key, default=True))

    # -- Memory logging ------------------------------------------------------

    def _log_job_run(
        self,
        started_at: str,
        finished_at: str,
        duration_s: float,
        result: Dict,
    ) -> None:
        """Write a job-execution record to the memory database.

        Uses the generic ``monetization_events``-style fallback via
        ``memory.save_monetization_event`` when a dedicated job log
        table is unavailable, so the record is always persisted without
        requiring a schema migration.
        """
        mem = self._get_memory()
        if mem is None:
            return

        # Build a compact summary — avoid storing huge result blobs
        summary: Dict[str, Any] = {
            "job_id": self.job_id,
            "job_name": self.job_name,
            "module": self.name,
            "category": self.category,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": duration_s,
            "consecutive_failures": self._consecutive_failures,
            "result_keys": list(result.keys()) if result else [],
            "status": "ok" if self._consecutive_failures == 0 else "degraded",
        }

        # Use a dedicated method when it exists; fall back to generic event log
        if hasattr(mem, "log_job_run"):
            try:
                mem.log_job_run(summary)
                return
            except Exception:
                pass

        # Generic fallback — piggy-back on monetization_events table
        try:
            mem.save_monetization_event(
                {
                    "event_type": "job_run",
                    "data": summary,
                }
            )
        except Exception as exc:
            self.log.debug("Could not persist job run record: %s", exc)
