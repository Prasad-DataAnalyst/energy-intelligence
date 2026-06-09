"""
core/circuit_breaker.py — Circuit Breaker Pattern
==================================================
Prevents cascading failures by tracking per-module error rates.

States:
  CLOSED    — normal operation, calls pass through
  OPEN      — too many failures, calls return fallback immediately
  HALF_OPEN — testing recovery, one call allowed through

Usage:
    breaker = get_breaker("trend_hunter")
    result  = breaker.call(fn, *args, fallback={}, **kwargs)
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("brain.circuit_breaker")


class CircuitState(Enum):
    CLOSED    = "closed"      # normal — calls pass through
    OPEN      = "open"        # tripped — calls return fallback immediately
    HALF_OPEN = "half_open"   # probing — one test call allowed through


@dataclass
class BreakerStats:
    """Cumulative counters for a single circuit breaker."""

    total_calls: int = 0
    failures: int = 0
    successes: int = 0
    last_failure_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None

    def to_dict(self) -> Dict:
        return {
            "total_calls":      self.total_calls,
            "failures":         self.failures,
            "successes":        self.successes,
            "last_failure_at":  self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_success_at":  self.last_success_at.isoformat() if self.last_success_at else None,
        }


class CircuitBreaker:
    """
    Per-module circuit breaker.

    Transitions:
      CLOSED  → OPEN      after *failure_threshold* consecutive failures
      OPEN    → HALF_OPEN after *timeout_s* seconds have elapsed since trip
      HALF_OPEN → CLOSED  after *success_threshold* consecutive successes on the probe call
      HALF_OPEN → OPEN    if the probe call fails

    Thread safety is guaranteed by an internal :class:`threading.Lock`.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout_s: float = 120.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_s = timeout_s

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at: Optional[float] = None   # time.monotonic() timestamp
        self._stats = BreakerStats()

    # ── Public call interface ─────────────────────────────────────────────────

    def call(
        self,
        fn: Callable,
        *args: Any,
        fallback: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute *fn*(\*args, \*\*kwargs) with circuit-breaker protection.

        * If the breaker is **OPEN** and the timeout has not expired, *fallback*
          is returned immediately without calling *fn*.
        * If the breaker is **OPEN** and the timeout *has* expired, the breaker
          transitions to HALF_OPEN and one probe call is attempted.
        * If the breaker is **HALF_OPEN**, exactly one probe call is attempted.
        * If the breaker is **CLOSED**, the call passes through normally.

        On a successful call the breaker moves toward CLOSED; on failure it
        moves toward (or stays) OPEN.
        """
        with self._lock:
            current_state = self._maybe_transition()

            if current_state == CircuitState.OPEN:
                log.debug(
                    f"CircuitBreaker '{self.name}': OPEN — returning fallback "
                    f"without calling {fn.__name__}"
                )
                self._stats.total_calls += 1
                return fallback

        # Attempt the call outside the lock to avoid blocking other threads
        # during potentially slow I/O operations.
        self._stats.total_calls += 1
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self._on_failure(exc)
            return fallback
        else:
            self._on_success()
            return result

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        """Current state of the breaker (re-evaluates timeout on each access)."""
        with self._lock:
            return self._maybe_transition()

    @property
    def stats(self) -> BreakerStats:
        """Snapshot of cumulative call statistics."""
        with self._lock:
            return BreakerStats(
                total_calls=self._stats.total_calls,
                failures=self._stats.failures,
                successes=self._stats.successes,
                last_failure_at=self._stats.last_failure_at,
                last_success_at=self._stats.last_success_at,
            )

    # ── Admin / testing ───────────────────────────────────────────────────────

    def reset(self) -> None:
        """Force the breaker back to CLOSED and clear all counters."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._opened_at = None
        log.info(f"CircuitBreaker '{self.name}': manually reset to CLOSED")

    def to_dict(self) -> Dict:
        """Return a JSON-serialisable snapshot of the breaker's full state."""
        with self._lock:
            state = self._maybe_transition()
        return {
            "name":                    self.name,
            "state":                   state.value,
            "failure_threshold":       self.failure_threshold,
            "success_threshold":       self.success_threshold,
            "timeout_s":               self.timeout_s,
            "consecutive_failures":    self._consecutive_failures,
            "consecutive_successes":   self._consecutive_successes,
            "opened_at":               (
                datetime.fromtimestamp(self._opened_at, tz=timezone.utc).isoformat()
                if self._opened_at is not None else None
            ),
            "stats":                   self._stats.to_dict(),
        }

    # ── Internal helpers (must be called with self._lock held) ────────────────

    def _maybe_transition(self) -> CircuitState:
        """
        Evaluate whether a CLOSED→OPEN or OPEN→HALF_OPEN transition should
        happen based on the current wall-clock time.

        Must be called while ``self._lock`` is held.
        """
        if self._state == CircuitState.OPEN:
            if (
                self._opened_at is not None
                and (time.monotonic() - self._opened_at) >= self.timeout_s
            ):
                log.info(
                    f"CircuitBreaker '{self.name}': timeout expired — "
                    f"OPEN → HALF_OPEN"
                )
                self._state = CircuitState.HALF_OPEN
                self._consecutive_successes = 0
        return self._state

    def _on_success(self) -> None:
        with self._lock:
            self._stats.successes += 1
            self._stats.last_success_at = datetime.now(timezone.utc)
            self._consecutive_failures = 0

            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.success_threshold:
                    log.info(
                        f"CircuitBreaker '{self.name}': "
                        f"recovery confirmed — HALF_OPEN → CLOSED"
                    )
                    self._state = CircuitState.CLOSED
                    self._opened_at = None
                    self._consecutive_successes = 0
            elif self._state == CircuitState.CLOSED:
                # Already healthy; nothing extra to do.
                pass

    def _on_failure(self, exc: Exception) -> None:
        with self._lock:
            self._stats.failures += 1
            self._stats.last_failure_at = datetime.now(timezone.utc)
            self._consecutive_failures += 1
            self._consecutive_successes = 0

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — trip immediately again
                log.warning(
                    f"CircuitBreaker '{self.name}': probe failed "
                    f"({type(exc).__name__}: {exc}) — HALF_OPEN → OPEN"
                )
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

            elif self._state == CircuitState.CLOSED:
                if self._consecutive_failures >= self.failure_threshold:
                    log.warning(
                        f"CircuitBreaker '{self.name}': failure threshold reached "
                        f"({self._consecutive_failures}/{self.failure_threshold}) "
                        f"— CLOSED → OPEN"
                    )
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()
                else:
                    log.debug(
                        f"CircuitBreaker '{self.name}': failure "
                        f"{self._consecutive_failures}/{self.failure_threshold} "
                        f"({type(exc).__name__}: {exc})"
                    )


# ── Breaker Registry ──────────────────────────────────────────────────────────


class BreakerRegistry:
    """
    Singleton registry that owns one :class:`CircuitBreaker` per named module.

    Breakers are created on first access with the default thresholds.  Use
    :func:`get_breaker` for the typical single-breaker use-case.
    """

    _instance: Optional["BreakerRegistry"] = None

    def __new__(cls) -> "BreakerRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._breakers: Dict[str, CircuitBreaker] = {}
            cls._instance._lock = threading.Lock()
        return cls._instance

    def get(self, name: str) -> CircuitBreaker:
        """Return the breaker for *name*, creating it with defaults if absent."""
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name=name)
                log.debug(f"BreakerRegistry: created new breaker for '{name}'")
            return self._breakers[name]

    def get_all(self) -> Dict[str, CircuitBreaker]:
        """Return a shallow copy of the breaker dictionary."""
        with self._lock:
            return dict(self._breakers)

    def reset_all(self) -> None:
        """Force every tracked breaker back to CLOSED."""
        with self._lock:
            breakers = list(self._breakers.values())
        for breaker in breakers:
            breaker.reset()
        log.info(f"BreakerRegistry: reset {len(breakers)} breakers to CLOSED")


# ── Module-level convenience function ─────────────────────────────────────────

def get_breaker(name: str) -> CircuitBreaker:
    """
    Return the :class:`CircuitBreaker` for *name* from the global
    :class:`BreakerRegistry`, creating it if it does not yet exist.

    Example::

        breaker = get_breaker("trend_hunter")
        result  = breaker.call(trend_hunter.fetch_all, fallback=[])
    """
    return BreakerRegistry().get(name)
