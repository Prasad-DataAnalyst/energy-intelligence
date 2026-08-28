"""
core/health_monitor.py — Module Health Monitor
==============================================
Tracks health state for every engine module.

Health states:
  healthy    — last N runs all succeeded
  degraded   — some recent failures but still running
  failed     — consecutive failure threshold exceeded
  unknown    — no runs recorded yet

Usage:
    from core.health_monitor import get_monitor, record_success, record_failure

    monitor = get_monitor()
    monitor.record_success("trend_hunter", duration_s=1.2)
    monitor.record_failure("trend_hunter", error="API timeout")
    status = monitor.get_status("trend_hunter")
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger("core.health_monitor")

# Thresholds for state transitions
_DEGRADED_THRESHOLD = 2   # consecutive failures before DEGRADED
_FAILED_THRESHOLD = 4     # consecutive failures before FAILED
_MAX_RECENT_DURATIONS = 10


class HealthState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class ModuleHealth:
    name: str
    state: HealthState = HealthState.UNKNOWN
    consecutive_failures: int = 0
    total_runs: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_error: str = ""
    avg_duration_s: float = 0.0
    recent_durations: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_runs": self.total_runs,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_error": self.last_error,
            "avg_duration_s": round(self.avg_duration_s, 4),
            "recent_durations": list(self.recent_durations),
        }

    def _update_avg_duration(self, duration_s: float) -> None:
        """Append to recent_durations (capped at _MAX_RECENT_DURATIONS) and
        recompute the rolling average."""
        self.recent_durations.append(duration_s)
        if len(self.recent_durations) > _MAX_RECENT_DURATIONS:
            self.recent_durations = self.recent_durations[-_MAX_RECENT_DURATIONS:]
        self.avg_duration_s = sum(self.recent_durations) / len(self.recent_durations)


class HealthMonitor:
    """Singleton, thread-safe health tracker for all engine modules."""

    _instance: Optional["HealthMonitor"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "HealthMonitor":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._health: Dict[str, ModuleHealth] = {}
        self._lock = threading.RLock()
        self._initialized = True

    # ── Public API ──────────────────────────────────────────────────────────────

    def record_success(self, module: str, duration_s: float = 0.0) -> None:
        """Record a successful run for *module*.

        Resets consecutive_failures to 0.  If the module was previously
        DEGRADED or FAILED it transitions to HEALTHY and a ``health.recovered``
        event is published.
        """
        with self._lock:
            health = self._get_or_create(module)
            was_unhealthy = health.state in (HealthState.DEGRADED, HealthState.FAILED)

            health.total_runs += 1
            health.total_successes += 1
            health.consecutive_failures = 0
            health.last_success_at = datetime.now(tz=timezone.utc)
            health.state = HealthState.HEALTHY
            health._update_avg_duration(duration_s)

            log.debug("health_monitor: [%s] success (%.3fs)", module, duration_s)

        if was_unhealthy:
            self._publish_event("health.recovered", module, {
                "previous_state": health.state.value,
                "duration_s": duration_s,
            })

    def record_failure(self, module: str, error: str = "") -> None:
        """Record a failed run for *module*.

        Transitions:
          consecutive_failures 0-1  → HEALTHY
          consecutive_failures 2-3  → DEGRADED  (publishes ``health.degraded``)
          consecutive_failures 4+   → FAILED    (publishes ``health.failed``)
        """
        with self._lock:
            health = self._get_or_create(module)
            previous_state = health.state

            health.total_runs += 1
            health.total_failures += 1
            health.consecutive_failures += 1
            health.last_failure_at = datetime.now(tz=timezone.utc)
            if error:
                health.last_error = error

            cf = health.consecutive_failures
            if cf >= _FAILED_THRESHOLD:
                health.state = HealthState.FAILED
            elif cf >= _DEGRADED_THRESHOLD:
                health.state = HealthState.DEGRADED
            else:
                health.state = HealthState.HEALTHY

            new_state = health.state
            log.warning(
                "health_monitor: [%s] failure #%d (%s) – state=%s error=%r",
                module, cf, health.last_failure_at.strftime("%H:%M:%S"),
                new_state.value, error,
            )

        # Publish transition events outside the lock to avoid potential deadlocks
        if new_state == HealthState.FAILED and previous_state != HealthState.FAILED:
            self._publish_event("health.failed", module, {
                "consecutive_failures": health.consecutive_failures,
                "last_error": error,
            })
        elif new_state == HealthState.DEGRADED and previous_state not in (
            HealthState.DEGRADED, HealthState.FAILED
        ):
            self._publish_event("health.degraded", module, {
                "consecutive_failures": health.consecutive_failures,
                "last_error": error,
            })

    def get_status(self, module: str) -> ModuleHealth:
        """Return the ModuleHealth record for *module* (creates it if absent)."""
        with self._lock:
            return self._get_or_create(module)

    def get_all_statuses(self) -> Dict[str, ModuleHealth]:
        """Return a snapshot dict of all tracked module health records."""
        with self._lock:
            return dict(self._health)

    def get_degraded(self) -> List[ModuleHealth]:
        """Return all modules whose state is DEGRADED or FAILED."""
        with self._lock:
            return [
                h for h in self._health.values()
                if h.state in (HealthState.DEGRADED, HealthState.FAILED)
            ]

    def reset(self, module: str) -> None:
        """Reset *module* back to UNKNOWN state, clearing all counters."""
        with self._lock:
            self._health[module] = ModuleHealth(name=module)
        log.info("health_monitor: [%s] reset to UNKNOWN", module)

    def summary(self) -> Dict[str, int]:
        """Return a count of modules in each health state."""
        counts: Dict[str, int] = {s.value: 0 for s in HealthState}
        with self._lock:
            for h in self._health.values():
                counts[h.state.value] += 1
        return counts

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _get_or_create(self, module: str) -> ModuleHealth:
        """Return existing ModuleHealth or create a fresh UNKNOWN entry."""
        if module not in self._health:
            self._health[module] = ModuleHealth(name=module)
        return self._health[module]

    def _publish_event(self, event_type: str, module: str, payload: Dict[str, Any]) -> None:
        """Optionally publish to event_bus; silently skip if not available."""
        try:
            from core import event_bus  # type: ignore[import]
            event_bus.publish(event_type, {"module": module, **payload})
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("health_monitor: event_bus publish failed (%s): %s", event_type, exc)


# ── Module-level singleton & convenience functions ───────────────────────────

_monitor: Optional[HealthMonitor] = None
_monitor_lock = threading.Lock()


def get_monitor() -> HealthMonitor:
    """Return the module-level HealthMonitor singleton (lazy init)."""
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = HealthMonitor()
    return _monitor


def record_success(module: str, duration_s: float = 0.0) -> None:
    """Convenience wrapper: record a successful run for *module*."""
    get_monitor().record_success(module, duration_s=duration_s)


def record_failure(module: str, error: str = "") -> None:
    """Convenience wrapper: record a failed run for *module*."""
    get_monitor().record_failure(module, error=error)
