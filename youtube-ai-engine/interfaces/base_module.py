"""
interfaces/base_module.py — Abstract Base Class for All Engine Modules
=======================================================================
Every intelligence, content, production, publishing, monetization,
evolution, and data module inherits from BaseModule.

Guarantees
----------
- Uniform ``run_safe()`` entry-point with circuit-breaker protection
- Automatic event publishing on success and failure
- Health telemetry written to the health monitor after each run
- Deferred imports (all ``core.*`` imports are inside methods) to
  prevent circular-import issues at package load time.

Subclass Contract
-----------------
    class MyModule(BaseModule):
        name     = "my_module"          # required
        category = "intelligence"       # required

        def run(self) -> Dict:
            ...
            return {"key": "value"}
"""

from __future__ import annotations

import logging
import traceback
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# Valid module categories used across the engine
VALID_CATEGORIES = frozenset(
    {
        "intelligence",
        "content",
        "production",
        "publishing",
        "monetization",
        "evolution",
        "data",
    }
)


class BaseModule(ABC):
    """Abstract base for all YouTube AI Engine modules.

    Subclasses must define class-level ``name`` and ``category``
    attributes and implement ``run() -> Dict``.
    """

    # -- Class-level metadata (must override in subclass) --------------------

    name: str = ""
    """Unique snake_case identifier, e.g. ``"trend_hunter"``."""

    category: str = ""
    """Broad category bucket — one of the strings in VALID_CATEGORIES."""

    # -- Instance state ------------------------------------------------------

    def __init__(self, memory: Optional[Any] = None) -> None:
        """
        Parameters
        ----------
        memory:
            An optional ``core.memory.Memory`` instance.  When omitted
            the module will obtain the global singleton via
            ``core.memory.get_memory()`` on first use.
        """
        self._memory = memory
        self._consecutive_failures: int = 0
        self._last_error: str = ""
        self._circuit_open: bool = False

    # -- Mandatory interface -------------------------------------------------

    @abstractmethod
    def run(self) -> Dict:
        """Execute the module's primary logic.

        Must return a non-None dict.  Any exception raised here is
        caught by ``run_safe()`` and does not propagate to the caller.
        """

    # -- Safe execution with circuit breaker ---------------------------------

    def run_safe(self) -> Dict:
        """Wrap ``run()`` with circuit-breaker, event bus, and health logging.

        Returns
        -------
        Dict
            The result returned by ``run()``, or ``{}`` on any failure.
            Never raises.
        """
        # Lazy-import core infrastructure to avoid circular imports
        try:
            from core.circuit_breaker import get_circuit_breaker
            cb = get_circuit_breaker(self.name)
        except ImportError:
            cb = None

        try:
            from core.event_bus import get_event_bus
            bus = get_event_bus()
        except ImportError:
            bus = None

        try:
            from core.health_monitor import get_health_monitor
            hm = get_health_monitor()
        except ImportError:
            hm = None

        # Respect a tripped circuit breaker
        if cb is not None and cb.is_open(self.name):
            self.log.warning(
                "Circuit breaker OPEN for %s — skipping run", self.name
            )
            self._circuit_open = True
            return {}

        correlation_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        try:
            result: Dict = self.run()
            duration_ms = int(
                (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
            )

            # Reset failure counter on success
            self._consecutive_failures = 0
            self._last_error = ""
            self._circuit_open = False

            # Notify circuit breaker of success
            if cb is not None:
                cb.record_success(self.name)

            # Publish success event
            self._publish(
                bus,
                "module.run.success",
                {
                    "module": self.name,
                    "category": self.category,
                    "duration_ms": duration_ms,
                    "correlation_id": correlation_id,
                    "result_keys": list(result.keys()) if result else [],
                },
            )

            # Log health
            if hm is not None:
                try:
                    hm.record(
                        module=self.name,
                        status="ok",
                        duration_ms=duration_ms,
                        correlation_id=correlation_id,
                    )
                except Exception:
                    pass

            return result if result is not None else {}

        except Exception as exc:
            duration_ms = int(
                (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
            )
            tb_str = traceback.format_exc()
            self._consecutive_failures += 1
            self._last_error = str(exc)

            self.log.error(
                "Module %s raised an exception (consecutive_failures=%d): %s",
                self.name,
                self._consecutive_failures,
                exc,
                exc_info=True,
            )

            # Notify circuit breaker of failure
            if cb is not None:
                try:
                    cb.record_failure(self.name)
                except Exception:
                    pass

            # Publish failure event
            self._publish(
                bus,
                "module.run.failure",
                {
                    "module": self.name,
                    "category": self.category,
                    "duration_ms": duration_ms,
                    "correlation_id": correlation_id,
                    "error": str(exc),
                    "consecutive_failures": self._consecutive_failures,
                },
            )

            # Log health
            if hm is not None:
                try:
                    hm.record(
                        module=self.name,
                        status="failed",
                        duration_ms=duration_ms,
                        correlation_id=correlation_id,
                        error=str(exc),
                    )
                except Exception:
                    pass

            # Persist error to memory if available
            mem = self._get_memory()
            if mem is not None:
                try:
                    mem.log_error(
                        module=self.name,
                        file_path="",
                        error_type=type(exc).__name__,
                        error_msg=str(exc),
                        traceback_str=tb_str,
                    )
                except Exception:
                    pass

            return {}

    # -- Health telemetry ----------------------------------------------------

    def health_check(self) -> Dict:
        """Return a snapshot of this module's current health state.

        Returns
        -------
        Dict with keys:
            module               — module name
            status               — "ok" | "degraded" | "failed"
            consecutive_failures — int
            last_error           — str (empty when healthy)
        """
        if self._consecutive_failures == 0:
            status = "ok"
        elif self._consecutive_failures < 3:
            status = "degraded"
        else:
            status = "failed"

        return {
            "module": self.name,
            "status": status,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
        }

    # -- Config access -------------------------------------------------------

    def get_config(self, key: str, default: Any = None) -> Any:
        """Read a config value from ``core.config_manager``.

        Falls back gracefully if the config manager is unavailable.

        Parameters
        ----------
        key:
            Dot-separated config path, e.g. ``"jobs.trend_hunter.enabled"``.
        default:
            Value to return when the key is absent or the config manager
            cannot be loaded.
        """
        try:
            from core.config_manager import get_config_manager
            cm = get_config_manager()
            return cm.get_config(key, default)
        except ImportError:
            return default
        except Exception:
            return default

    # -- Event publishing ----------------------------------------------------

    def emit(self, event_type: str, payload: Dict) -> None:
        """Publish an event to the engine event bus.

        Attaches a ``correlation_id`` and ``source_module`` to every
        payload so consumers can trace events back to their origin.

        Parameters
        ----------
        event_type:
            Dot-separated event name, e.g. ``"trend.found"``.
        payload:
            Arbitrary dict; mutated in-place to add metadata fields.
        """
        try:
            from core.event_bus import get_event_bus
            bus = get_event_bus()
        except ImportError:
            return

        enriched = {
            **payload,
            "source_module": self.name,
            "correlation_id": str(uuid.uuid4()),
            "emitted_at": datetime.now(timezone.utc).isoformat(),
        }
        self._publish(bus, event_type, enriched)

    # -- Logger property -----------------------------------------------------

    @property
    def log(self) -> logging.Logger:
        """Return a module-scoped logger named ``engine.<module_name>``."""
        return logging.getLogger(f"engine.{self.name}")

    # -- Memory accessor -----------------------------------------------------

    def _get_memory(self) -> Optional[Any]:
        """Return the Memory instance, obtaining the singleton if needed."""
        if self._memory is not None:
            return self._memory
        try:
            from core.memory import get_memory
            self._memory = get_memory()
        except ImportError:
            pass
        return self._memory

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _publish(bus: Optional[Any], event_type: str, payload: Dict) -> None:
        """Fire-and-forget publish; swallows all exceptions."""
        if bus is None:
            return
        try:
            bus.publish(event_type, payload)
        except Exception:
            pass
