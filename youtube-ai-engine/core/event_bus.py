"""
core/event_bus.py — In-Process Event Bus
=========================================
Decouples modules from each other via pub/sub.

Standard event types:
  module.started        payload: {module, job_id, run_id}
  module.succeeded      payload: {module, duration_s, result_summary}
  module.failed         payload: {module, error, traceback}
  content.idea          payload: {title, score, source}
  analytics.snapshot    payload: {date, scope, metrics}
  error.detected        payload: {module, error, file_path, traceback}
  patch.applied         payload: {patch_id, file_path, module}
  patch.rolled_back     payload: {patch_id, reason}
  health.degraded       payload: {module, consecutive_failures}
  health.recovered      payload: {module}
  config.changed        payload: {key, old_value, new_value}
"""
import logging
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from fnmatch import fnmatch
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional

log = logging.getLogger("brain.event_bus")


@dataclass
class Event:
    """Immutable record of a single published event."""

    event_id: str
    event_type: str
    source: str
    payload: Dict
    timestamp: datetime

    @classmethod
    def create(cls, event_type: str, payload: Dict, source: str = "") -> "Event":
        return cls(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            source=source,
            payload=payload,
            timestamp=datetime.now(timezone.utc),
        )


@dataclass
class _Subscription:
    """Internal record linking a subscription ID to its handler."""

    subscription_id: str
    pattern: str          # raw pattern string (may contain wildcards)
    handler: Callable


class EventBus:
    """
    Singleton, thread-safe in-process pub/sub event bus.

    Publishing an event calls all matching subscribers synchronously in the
    calling thread.  Handler exceptions are caught and logged — a misbehaving
    subscriber can never crash the bus or affect other subscribers.

    Wildcard patterns are supported via ``fnmatch``::

        bus.subscribe("module.*", handler)   # matches module.started, module.failed …
        bus.subscribe("*",        handler)   # matches everything
    """

    _instance: Optional["EventBus"] = None

    def __new__(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialised = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialised:  # type: ignore[has-type]
            return
        self._lock: threading.Lock = threading.Lock()
        # Maps pattern string → list of _Subscription objects
        self._subscribers: Dict[str, List[_Subscription]] = {}
        # Flat index for O(1) unsubscribe: subscription_id → _Subscription
        self._sub_index: Dict[str, _Subscription] = {}
        self._history: Deque[Event] = deque(maxlen=1000)
        self._initialised = True

    # ── Core API ──────────────────────────────────────────────────────────────

    def publish(
        self,
        event_type: str,
        payload: Dict,
        source: str = "",
    ) -> str:
        """
        Create an :class:`Event` and deliver it to all matching subscribers.

        Returns the event_id of the newly created event.  Subscriber
        exceptions are caught individually so that one bad handler does not
        block delivery to subsequent handlers.
        """
        event = Event.create(event_type=event_type, payload=payload, source=source)

        # Collect matching subscriptions under the lock, then call handlers
        # outside the lock to avoid re-entrant deadlocks.
        matching: List[_Subscription] = []
        with self._lock:
            self._history.append(event)
            for pattern, subs in self._subscribers.items():
                if fnmatch(event_type, pattern):
                    matching.extend(subs)

        for sub in matching:
            try:
                sub.handler(event)
            except Exception as exc:
                log.error(
                    f"EventBus: handler {sub.handler!r} raised while processing "
                    f"'{event_type}' (sub={sub.subscription_id}): "
                    f"{type(exc).__name__}: {exc}"
                )

        log.debug(
            f"EventBus: published '{event_type}' from='{source}' "
            f"id={event.event_id} handlers={len(matching)}"
        )
        return event.event_id

    def subscribe(self, event_type: str, handler: Callable) -> str:
        """
        Register *handler* to receive events matching *event_type*.

        *event_type* supports ``fnmatch`` wildcards, e.g. ``"module.*"`` or
        ``"*"``.  Returns a unique subscription_id that can be passed to
        :meth:`unsubscribe`.
        """
        sub = _Subscription(
            subscription_id=str(uuid.uuid4()),
            pattern=event_type,
            handler=handler,
        )
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(sub)
            self._sub_index[sub.subscription_id] = sub
        log.debug(
            f"EventBus: subscribed to '{event_type}' "
            f"handler={handler!r} id={sub.subscription_id}"
        )
        return sub.subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        """
        Remove the subscription identified by *subscription_id*.

        Silently ignores unknown IDs so callers do not need to guard teardown
        paths with try/except.
        """
        with self._lock:
            sub = self._sub_index.pop(subscription_id, None)
            if sub is None:
                return
            bucket = self._subscribers.get(sub.pattern, [])
            try:
                bucket.remove(sub)
            except ValueError:
                pass
            if not bucket:
                self._subscribers.pop(sub.pattern, None)
        log.debug(f"EventBus: unsubscribed id={subscription_id}")

    # ── History ───────────────────────────────────────────────────────────────

    def get_history(
        self,
        event_type: Optional[str] = None,
        n: int = 100,
    ) -> List[Event]:
        """
        Return up to *n* recent events from the rolling history buffer.

        If *event_type* is given (wildcards supported) only matching events
        are returned.  Results are ordered oldest-first.
        """
        with self._lock:
            snapshot = list(self._history)

        if event_type is not None:
            snapshot = [e for e in snapshot if fnmatch(e.event_type, event_type)]

        # Return the most-recent n events, oldest-first
        return snapshot[-n:] if len(snapshot) > n else snapshot

    # ── Testing helpers ───────────────────────────────────────────────────────

    def clear_subscriptions(self) -> None:
        """
        Remove all subscriptions.  Intended for use in unit tests so that
        subscriptions from one test case do not bleed into the next.
        """
        with self._lock:
            self._subscribers.clear()
            self._sub_index.clear()
        log.debug("EventBus: all subscriptions cleared")


# ── Module-level singleton & convenience functions ─────────────────────────

_bus: Optional[EventBus] = None


def get_bus() -> EventBus:
    """Return the process-wide :class:`EventBus` singleton."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def publish(event_type: str, payload: Dict, source: str = "") -> str:
    """
    Convenience wrapper — publishes *event_type* on the global bus.

    Returns the event_id of the created event.
    """
    return get_bus().publish(event_type, payload, source)


def subscribe(event_type: str, handler: Callable) -> str:
    """
    Convenience wrapper — subscribes *handler* to *event_type* on the global
    bus and returns the subscription_id.
    """
    return get_bus().subscribe(event_type, handler)
