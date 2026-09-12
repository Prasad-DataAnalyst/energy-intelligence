"""
core/registry.py — Module Registry & Autodiscovery
===================================================
Central catalogue of every engine module.

Usage:
    from core.registry import register, Registry

    @register(
        name="trend_hunter",
        category="intelligence",
        phase="trend",
        schedule={"hour": 7, "minute": 0},
        description="Hunts trending topics",
    )
    class TrendHunterEngine(BaseModule):
        def run(self) -> Dict: ...
"""
import importlib
import importlib.util
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

log = logging.getLogger("brain.registry")


@dataclass
class ModuleEntry:
    """Catalogue record for a registered engine module."""

    name: str
    category: str
    phase: Optional[str]
    module_class: Type[Any]
    schedule: Optional[Dict]          # keys: hour, minute, day_of_week (all optional)
    description: str
    capabilities: List[str]
    enabled: bool


class Registry:
    """
    Singleton, thread-safe module registry.

    All registered modules live in ``_entries`` keyed by their canonical name.
    The ``@register`` classmethod decorator is the primary entry point:

        @Registry.register(name="trend_hunter", category="intelligence")
        class TrendHunterEngine:
            ...
    """

    _entries: Dict[str, ModuleEntry] = {}
    _lock: threading.Lock = threading.Lock()
    _instance: Optional["Registry"] = None

    def __new__(cls) -> "Registry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── Registration ──────────────────────────────────────────────────────────

    @classmethod
    def register(
        cls,
        name: str,
        category: str,
        phase: Optional[str] = None,
        schedule: Optional[Dict] = None,
        description: str = "",
        capabilities: Optional[List[str]] = None,
        enabled: bool = True,
    ):
        """
        Decorator factory.  Registers the decorated class and returns it
        unchanged so it can still be used normally.

        Example::

            @Registry.register(
                name="script_writer",
                category="production",
                phase="script",
                schedule={"hour": 4, "minute": 0},
                description="Generates video scripts",
                capabilities=["script_generation", "style_selection"],
            )
            class ScriptWriter:
                ...
        """
        def decorator(cls_: Type[Any]) -> Type[Any]:
            entry = ModuleEntry(
                name=name,
                category=category,
                phase=phase,
                module_class=cls_,
                schedule=schedule,
                description=description,
                capabilities=capabilities or [],
                enabled=enabled,
            )
            with Registry._lock:
                if name in Registry._entries:
                    log.warning(
                        f"Registry: overwriting existing entry for '{name}' "
                        f"({Registry._entries[name].module_class.__qualname__} → "
                        f"{cls_.__qualname__})"
                    )
                Registry._entries[name] = entry
            log.debug(f"Registry: registered '{name}' [{category}]")
            return cls_

        return decorator

    # ── Lookups ───────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, name: str) -> Optional[ModuleEntry]:
        """Return the entry for *name*, or ``None`` if not found."""
        with cls._lock:
            return cls._entries.get(name)

    @classmethod
    def get_all(cls) -> List[ModuleEntry]:
        """Return all registered entries in insertion order."""
        with cls._lock:
            return list(cls._entries.values())

    @classmethod
    def get_by_category(cls, category: str) -> List[ModuleEntry]:
        """Return all entries whose *category* matches (case-sensitive)."""
        with cls._lock:
            return [e for e in cls._entries.values() if e.category == category]

    @classmethod
    def get_scheduled(cls) -> List[ModuleEntry]:
        """Return only entries that carry a schedule dict."""
        with cls._lock:
            return [e for e in cls._entries.values() if e.schedule]

    @classmethod
    def get_phases(cls) -> Dict[str, "ModuleEntry"]:
        """
        Return a mapping of ``phase_name → ModuleEntry`` for all entries that
        have a non-None phase.  If two modules share the same phase the last
        registered one wins and a warning is logged.
        """
        phases: Dict[str, ModuleEntry] = {}
        with cls._lock:
            for entry in cls._entries.values():
                if entry.phase is not None:
                    if entry.phase in phases:
                        log.warning(
                            f"Registry.get_phases: phase '{entry.phase}' is claimed by "
                            f"both '{phases[entry.phase].name}' and '{entry.name}' — "
                            f"last one wins"
                        )
                    phases[entry.phase] = entry
        return phases

    # ── Autodiscovery ─────────────────────────────────────────────────────────

    @classmethod
    def discover(cls, package_paths: List[str]) -> None:
        """
        Dynamically import every ``.py`` file found in *package_paths* so that
        ``@register`` decorators execute and populate the registry.

        Errors during import are caught and logged individually; a single bad
        module will never abort discovery of the remaining modules.

        Args:
            package_paths: List of directory paths (str or Path-like) to scan.
                           Each path is scanned non-recursively for ``*.py``
                           files whose name does not start with ``_``.
        """
        for raw_path in package_paths:
            directory = Path(raw_path)
            if not directory.is_dir():
                log.warning(f"Registry.discover: '{directory}' is not a directory — skipped")
                continue

            py_files = sorted(
                p for p in directory.glob("*.py")
                if not p.name.startswith("_")
            )
            log.debug(f"Registry.discover: scanning {directory} ({len(py_files)} files)")

            for py_file in py_files:
                module_name = f"_discovered_.{directory.name}.{py_file.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec is None or spec.loader is None:
                        log.warning(f"Registry.discover: could not build spec for '{py_file}'")
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    log.debug(f"Registry.discover: imported '{py_file.name}'")
                except Exception as exc:
                    log.warning(
                        f"Registry.discover: failed to import '{py_file}': "
                        f"{type(exc).__name__}: {exc}"
                    )


# ── Module-level convenience function ─────────────────────────────────────────

def register(
    name: str,
    category: str,
    phase: Optional[str] = None,
    schedule: Optional[Dict] = None,
    description: str = "",
    capabilities: Optional[List[str]] = None,
    enabled: bool = True,
):
    """
    Module-level alias for ``Registry.register``.  Import this for cleaner
    decorator syntax::

        from core.registry import register

        @register(name="trend_hunter", category="intelligence")
        class TrendHunterEngine:
            ...
    """
    return Registry.register(
        name=name,
        category=category,
        phase=phase,
        schedule=schedule,
        description=description,
        capabilities=capabilities,
        enabled=enabled,
    )
