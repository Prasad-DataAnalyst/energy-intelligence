"""
interfaces/base_manager.py — Abstract Base Class for API Managers
==================================================================
API manager classes (``YouTubeAPIClient``, ``ChannelManager``,
``YouTubePublisher``, ``YouTubeStudio``, etc.) inherit from
``BaseManager`` to get:

  - Configurable retry logic with exponential back-off
  - Structured error logging (module name + operation context)
  - Rate-limit awareness: a flag that other callers can query before
    attempting operations, and a timed auto-reset

Usage
-----
    class MyAPIClient(BaseManager):
        _manager_name = "my_api_client"

        def fetch_data(self, resource_id: str) -> Optional[Dict]:
            return self.retry(self._do_fetch, resource_id, max_attempts=4)

        def _do_fetch(self, resource_id: str) -> Dict:
            ...  # may raise — retry() handles it
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional, Tuple, TypeVar

F = TypeVar("F")  # return type of the wrapped callable


class BaseManager:
    """Reusable foundation for every API-facing manager in the engine.

    Instances are lightweight — no I/O on construction.  All heavy
    work happens lazily inside methods.

    Attributes
    ----------
    _manager_name : str
        Override in subclasses to customise the logger name.
        Defaults to the class name in lower-case.
    """

    _manager_name: str = ""

    def __init__(self) -> None:
        self._rate_limited: bool = False
        self._rate_limit_until: Optional[datetime] = None

    # -- Logger --------------------------------------------------------------

    @property
    def log(self) -> logging.Logger:
        """Return a logger scoped to this manager."""
        name = self._manager_name or type(self).__name__.lower()
        return logging.getLogger(f"engine.manager.{name}")

    # -- Retry logic ---------------------------------------------------------

    def retry(
        self,
        fn: Callable[..., F],
        *args: Any,
        max_attempts: int = 3,
        backoff: float = 2.0,
        **kwargs: Any,
    ) -> Optional[F]:
        """Call *fn* with *args*/*kwargs*, retrying on any exception.

        Uses exponential back-off: the wait before attempt *n* is
        ``backoff ** (n - 1)`` seconds (1 s, 2 s, 4 s … for backoff=2).

        Parameters
        ----------
        fn:
            Callable to invoke.  May be a bound method or plain function.
        *args:
            Positional arguments forwarded to *fn*.
        max_attempts:
            Maximum number of total attempts (default 3).
        backoff:
            Multiplicative back-off base in seconds (default 2.0).
        **kwargs:
            Keyword arguments forwarded to *fn*.

        Returns
        -------
        Optional[F]
            Return value of *fn* on success, or ``None`` after all
            attempts are exhausted.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            # Honour rate-limit before every attempt
            self._wait_if_rate_limited()

            try:
                result = fn(*args, **kwargs)
                if attempt > 1:
                    self.log.info(
                        "Retry succeeded on attempt %d/%d for %s",
                        attempt,
                        max_attempts,
                        getattr(fn, "__name__", repr(fn)),
                    )
                return result

            except Exception as exc:
                last_exc = exc
                self.log_error(
                    operation=getattr(fn, "__name__", repr(fn)),
                    error=exc,
                )

                if attempt < max_attempts:
                    wait = backoff ** (attempt - 1)
                    self.log.info(
                        "Attempt %d/%d failed — retrying in %.1fs",
                        attempt,
                        max_attempts,
                        wait,
                    )
                    time.sleep(wait)

        self.log.error(
            "All %d attempts exhausted for %s. Last error: %s",
            max_attempts,
            getattr(fn, "__name__", repr(fn)),
            last_exc,
        )
        return None

    # -- Structured error logging --------------------------------------------

    def log_error(self, operation: str, error: Exception) -> None:
        """Emit a structured error log entry.

        The entry includes the manager name, the failing operation, the
        exception type, and the error message so log aggregators can
        group and alert on common failure patterns.

        Parameters
        ----------
        operation:
            Name of the operation that failed, typically a method name.
        error:
            The exception that was raised.
        """
        self.log.error(
            "Manager=%s | operation=%s | error_type=%s | message=%s",
            self._manager_name or type(self).__name__,
            operation,
            type(error).__name__,
            str(error),
        )

    # -- Rate-limit awareness ------------------------------------------------

    def set_rate_limited(self, seconds: int) -> None:
        """Mark this manager as rate-limited for the given duration.

        While rate-limited, ``retry()`` will sleep until the window
        expires before each attempt.  Callers can also query
        ``_rate_limited`` directly to skip non-critical work.

        Parameters
        ----------
        seconds:
            How many seconds until the rate limit is expected to lift.
            Pass 0 to clear the flag immediately.
        """
        if seconds <= 0:
            self._rate_limited = False
            self._rate_limit_until = None
            self.log.debug("Rate-limit flag cleared")
            return

        self._rate_limited = True
        self._rate_limit_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self.log.warning(
            "Rate-limited for %ds — operations will be delayed until %s",
            seconds,
            self._rate_limit_until.isoformat(),
        )

    def is_rate_limited(self) -> bool:
        """Return True if the manager is currently rate-limited.

        Also clears the flag automatically once the window has passed,
        so callers never need to call ``set_rate_limited(0)`` manually.
        """
        if not self._rate_limited:
            return False

        # Auto-reset when the window has elapsed
        if self._rate_limit_until is not None:
            if datetime.now(timezone.utc) >= self._rate_limit_until:
                self._rate_limited = False
                self._rate_limit_until = None
                self.log.info("Rate-limit window expired — flag cleared")
                return False

        return True

    def _wait_if_rate_limited(self) -> None:
        """Block until the rate-limit window expires (if active).

        Called automatically by ``retry()`` before each attempt.
        """
        if not self._rate_limited:
            return

        if self._rate_limit_until is not None:
            now = datetime.now(timezone.utc)
            remaining = (self._rate_limit_until - now).total_seconds()
            if remaining > 0:
                self.log.info(
                    "Rate-limited — sleeping %.1fs before retrying", remaining
                )
                time.sleep(remaining)

        # Clear after wait
        self._rate_limited = False
        self._rate_limit_until = None
