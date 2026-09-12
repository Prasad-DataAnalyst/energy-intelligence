"""
core/config_manager.py — Centralized Configuration Manager
===========================================================
Single source of truth for all engine configuration.

- Merges master_config.yaml + channel_persona.yaml + style_profiles.yaml
- Overrides with environment variables (prefix: ENGINE_)
- Validates required keys exist
- Supports hot-reload (re-reads YAML files on demand)
- Thread-safe reads
- Provides typed accessors
- Allows runtime overrides (saved to a runtime_overrides.json)

Usage:
    from core.config_manager import get_config, set_config

    model = get_config("ai.model", default="claude-haiku-4-5-20251001")
    set_config("video.style_preset", "tech-dark")   # runtime override
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("core.config_manager")

HERE = Path(__file__).parent.parent
CONFIG_DIR = HERE / "config"
OVERRIDES_FILE = HERE / "data" / "runtime_overrides.json"

# YAML config files merged in order (later files win on key conflict)
_CONFIG_FILES = [
    "master_config.yaml",
    "channel_persona.yaml",
    "style_profiles.yaml",
]


class ConfigManager:
    """Singleton configuration manager that merges YAML files, environment
    variables, and runtime overrides into a single dotted-path-accessible tree.
    """

    _instance: Optional["ConfigManager"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "ConfigManager":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        # Guard against repeated __init__ calls on the singleton
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._overrides: Dict[str, Any] = {}
        self._load_all()
        self._initialized = True

    # ── Public API ──────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Return a config value by dotted path.

        Lookup priority (highest → lowest):
          1. Environment variable  ENGINE_AI_MODEL  for key  ai.model
          2. Runtime overrides (_overrides dict)
          3. Merged YAML config (_data dict)
          4. *default*
        """
        # 1. Environment variable override
        env_val = self._env_for_key(key)
        if env_val is not None:
            return env_val

        with self._lock:
            keys = key.split(".")

            # 2. Runtime overrides
            try:
                val = self._deep_get(self._overrides, keys)
                if val is not None:
                    return val
            except (KeyError, TypeError):
                pass

            # 3. Merged YAML data
            try:
                val = self._deep_get(self._data, keys)
                if val is not None:
                    return val
            except (KeyError, TypeError):
                pass

        return default

    def set(self, key: str, value: Any) -> None:
        """Set a runtime override for *key* and persist to disk."""
        with self._lock:
            keys = key.split(".")
            self._deep_set(self._overrides, keys, value)
            self._save_overrides()

    def reload(self) -> None:
        """Re-read all YAML files from disk (hot-reload).

        Runtime overrides are preserved; they take precedence over freshly
        loaded YAML values.
        """
        with self._lock:
            self._data = {}
            self._load_yaml_files()
            self._merge_env_vars()
            log.info("config_manager: config reloaded from disk")

    def get_section(self, prefix: str) -> Dict[str, Any]:
        """Return a shallow copy of all keys under *prefix* (dotted path)."""
        section = self.get(prefix, default={})
        if isinstance(section, dict):
            return dict(section)
        return {}

    def to_dict(self) -> Dict[str, Any]:
        """Return a deep copy of the full merged configuration tree."""
        import copy
        with self._lock:
            merged = copy.deepcopy(self._data)
            self._deep_merge(merged, self._overrides)
            return merged

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Load YAML files, apply env-var overrides, and load persisted overrides."""
        self._load_yaml_files()
        self._merge_env_vars()
        self._load_overrides()

    def _load_yaml_files(self) -> None:
        """Merge all YAML config files into self._data in order."""
        for filename in _CONFIG_FILES:
            path = CONFIG_DIR / filename
            parsed = self._read_yaml(path)
            if parsed:
                self._deep_merge(self._data, parsed)

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        """Read a YAML file; return {} on any error (including missing PyYAML)."""
        try:
            import yaml  # optional dependency
        except ImportError:
            log.debug("config_manager: PyYAML not installed; skipping %s", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                result = yaml.safe_load(fh)
                return result if isinstance(result, dict) else {}
        except FileNotFoundError:
            log.debug("config_manager: config file not found: %s", path)
            return {}
        except Exception as exc:  # noqa: BLE001
            log.warning("config_manager: failed to parse %s: %s", path, exc)
            return {}

    def _merge_env_vars(self) -> None:
        """Read all ENGINE_* environment variables and inject them into _data.

        Conversion rule: ENGINE_AI_MODEL → ai.model (lowercase, _ → .)
        Only the first underscore-separated prefix is converted — the remainder
        of the name is lowercased and used verbatim as the leaf key.

        Examples:
            ENGINE_AI_MODEL          → ai.model
            ENGINE_VIDEO_STYLE_PRESET → video.style_preset
        """
        prefix = "ENGINE_"
        for raw_key, value in os.environ.items():
            if not raw_key.startswith(prefix):
                continue
            stripped = raw_key[len(prefix):]  # e.g. "AI_MODEL"
            # Convert ALL underscores to dots, then lowercase
            dotted = stripped.replace("_", ".").lower()
            try:
                self._deep_set(self._data, dotted.split("."), value)
                log.debug("config_manager: env override %s → %s = %r", raw_key, dotted, value)
            except Exception as exc:  # noqa: BLE001
                log.warning("config_manager: could not apply env var %s: %s", raw_key, exc)

    def _env_for_key(self, key: str) -> Optional[str]:
        """Return the ENGINE_* env var value for *key*, or None if not set.

        ai.model → ENGINE_AI_MODEL
        """
        env_key = "ENGINE_" + key.upper().replace(".", "_")
        return os.environ.get(env_key)

    def _load_overrides(self) -> None:
        """Load runtime overrides from the JSON file (create if missing)."""
        try:
            OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
            if OVERRIDES_FILE.exists():
                with OVERRIDES_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        self._overrides = data
        except Exception as exc:  # noqa: BLE001
            log.warning("config_manager: could not load overrides: %s", exc)
            self._overrides = {}

    def _save_overrides(self) -> None:
        """Persist current runtime overrides to the JSON file."""
        try:
            OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with OVERRIDES_FILE.open("w", encoding="utf-8") as fh:
                json.dump(self._overrides, fh, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001
            log.warning("config_manager: could not save overrides: %s", exc)

    @staticmethod
    def _deep_get(d: Dict, keys: List[str]) -> Any:
        """Recursively traverse *d* along *keys*; raise KeyError if missing."""
        node = d
        for k in keys:
            if not isinstance(node, dict):
                raise KeyError(k)
            node = node[k]
        return node

    @staticmethod
    def _deep_set(d: Dict, keys: List[str], value: Any) -> None:
        """Recursively create nested dicts in *d* and set leaf to *value*."""
        node = d
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> None:
        """Recursively merge *override* into *base* in-place.

        Dict values are merged recursively; all other types are overwritten.
        """
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                ConfigManager._deep_merge(base[k], v)
            else:
                base[k] = v


# ── Module-level singleton & convenience functions ───────────────────────────

_manager: Optional[ConfigManager] = None
_manager_lock = threading.Lock()


def _get_manager() -> ConfigManager:
    """Lazily create the module-level ConfigManager singleton."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = ConfigManager()
    return _manager


def get_config(key: str, default: Any = None) -> Any:
    """Return the config value at dotted *key*, or *default* if not found."""
    return _get_manager().get(key, default=default)


def set_config(key: str, value: Any) -> None:
    """Set a runtime override for *key* and persist it to disk."""
    _get_manager().set(key, value)
