"""
services/api_key_manager.py
Self-healing API key manager.

Responsibilities:
  - Load keys from .env, environment, and config/api_keys.json
  - Track key health: failures, rate-limits, last-success
  - Rotate between multiple keys per service
  - Auto-clear failure flags after 24h (keys may be renewed)
  - Surface a prioritised working key for any service on demand
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# File that stores per-key health metadata (created automatically)
_HEALTH_FILE = Path(__file__).parent.parent / "config" / "api_key_health.json"

# File users can drop extra keys into (optional, never required)
_EXTRA_KEYS_FILE = Path(__file__).parent.parent / "config" / "api_keys.json"

# Maps service name → list of environment variable names to check
_ENV_VAR_MAP: Dict[str, List[str]] = {
    "elevenlabs":    ["ELEVENLABS_API_KEY"],
    "openai":        ["OPENAI_API_KEY"],
    "replicate":     ["REPLICATE_API_TOKEN"],
    "runway":        ["RUNWAY_API_KEY"],
    "pexels":        ["PEXELS_API_KEY"],
    "pixabay":       ["PIXABAY_API_KEY"],
    "newsapi":       ["NEWSAPI_KEY", "NEWS_API_KEY"],
    "guardian":      ["GUARDIAN_API_KEY"],
    "youtube":       ["YOUTUBE_DATA_API_KEY", "YOUTUBE_API_KEY"],
    "nasa":          ["NASA_API_KEY"],
    "fred":          ["FRED_API_KEY"],
    "azure_speech":  ["AZURE_SPEECH_KEY"],
    "unsplash":      ["UNSPLASH_ACCESS_KEY"],
    "flickr":        ["FLICKR_API_KEY"],
    "reddit":        ["REDDIT_CLIENT_ID"],  # praw
    "reddit_secret": ["REDDIT_CLIENT_SECRET"],
    "coverr":        ["COVERR_API_KEY"],
}

# Services that work with a known demo/free key (no sign-up needed)
_DEMO_KEYS: Dict[str, str] = {
    "nasa": "DEMO_KEY",  # NASA officially provides this
}

# After this many consecutive failures, key is quarantined until TTL expires
_FAIL_THRESHOLD = 3
# Failed keys are retried after this many seconds (24 h)
_FAILURE_TTL_S = 86_400
# Rate-limited keys default reset window
_RATE_LIMIT_DEFAULT_S = 3_600


class APIKeyManager:
    """
    Central key broker for all external API services.

    Usage:
        mgr = APIKeyManager()
        key = mgr.get_key("pexels")          # → str or None
        mgr.mark_success("pexels", key)
        mgr.mark_failed("pexels", key, "401 Unauthorized")
        mgr.mark_rate_limited("pexels", key, reset_after_s=3600)
        mgr.add_key("pexels", "NEW_KEY_HERE")
        status = mgr.get_service_status()    # health dashboard dict
    """

    def __init__(self) -> None:
        self._health: Dict[str, Dict] = {}
        self._load_health()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def get_key(self, service: str) -> Optional[str]:
        """
        Return the best available API key for *service*.

        Search order:
          1. Environment variables (checked at runtime so .env reloads work)
          2. Extra keys from config/api_keys.json
          3. Keys manually added via add_key()
          4. Demo/free key if the service has one
        """
        # --- Demo key (no env needed) ---
        if service in _DEMO_KEYS:
            env_key = self._first_env_key(service)
            # prefer real key over demo; demo is last resort
            if env_key and not self._is_bad(service, env_key):
                self._touch_key(service, env_key)
                return env_key
            return _DEMO_KEYS[service]

        # --- Environment variables ---
        env_key = self._first_env_key(service)
        if env_key and not self._is_bad(service, env_key):
            self._touch_key(service, env_key)
            return env_key

        # --- Extra keys from config file ---
        for key in self._extra_keys_for(service):
            if not self._is_bad(service, key):
                self._touch_key(service, key)
                return key

        # --- Keys added at runtime via add_key() ---
        for key, meta in self._health.get(service, {}).get("keys", {}).items():
            if not self._is_bad(service, key):
                self._touch_key(service, key)
                return key

        log.debug("No working key for service '%s'", service)
        return None

    def is_available(self, service: str) -> bool:
        """True if at least one working key exists for *service*."""
        return self.get_key(service) is not None

    def mark_success(self, service: str, key: str) -> None:
        entry = self._get_key_entry(service, key)
        entry.update({"failed": False, "fail_count": 0,
                       "rate_limited": False, "last_success": time.time()})
        self._save_health()

    def mark_failed(self, service: str, key: str, reason: str = "") -> None:
        entry = self._get_key_entry(service, key)
        entry["fail_count"] = entry.get("fail_count", 0) + 1
        if entry["fail_count"] >= _FAIL_THRESHOLD:
            entry["failed"] = True
            entry["failure_time"] = time.time()
            log.warning("Key quarantined: service=%s reason=%s", service, reason)
        entry["last_failure_reason"] = reason
        self._save_health()

    def mark_rate_limited(self, service: str, key: str,
                          reset_after_s: int = _RATE_LIMIT_DEFAULT_S) -> None:
        entry = self._get_key_entry(service, key)
        entry["rate_limited"] = True
        entry["rate_reset_at"] = time.time() + reset_after_s
        self._save_health()
        log.info("Key rate-limited: service=%s reset_in=%ds", service, reset_after_s)

    def add_key(self, service: str, key: str, source: str = "manual") -> None:
        """Persist a new key for a service."""
        entry = self._get_key_entry(service, key)
        entry.setdefault("added", time.time())
        entry["source"] = source
        entry["failed"] = False
        entry["fail_count"] = 0
        self._save_health()
        log.info("New key registered: service=%s source=%s", service, source)

    def get_service_status(self) -> Dict[str, dict]:
        """Return a health summary dict for every tracked service."""
        result: Dict[str, dict] = {}
        for svc in _ENV_VAR_MAP:
            key = self.get_key(svc)
            result[svc] = {
                "available": key is not None,
                "env_var": _ENV_VAR_MAP[svc][0],
                "demo_key": svc in _DEMO_KEYS,
            }
        return result

    def all_available_services(self) -> List[str]:
        return [s for s in _ENV_VAR_MAP if self.is_available(s)]

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _is_bad(self, service: str, key: str) -> bool:
        meta = self._health.get(service, {}).get("keys", {}).get(key, {})
        # Hard failure with TTL
        if meta.get("failed"):
            age = time.time() - meta.get("failure_time", 0)
            if age < _FAILURE_TTL_S:
                return True
            # Auto-reset after TTL
            meta["failed"] = False
            meta["fail_count"] = 0
        # Rate limit
        if meta.get("rate_limited"):
            if time.time() < meta.get("rate_reset_at", 0):
                return True
            meta["rate_limited"] = False
        return False

    def _first_env_key(self, service: str) -> Optional[str]:
        for var in _ENV_VAR_MAP.get(service, []):
            val = os.getenv(var, "").strip()
            if val:
                return val
        return None

    def _extra_keys_for(self, service: str) -> List[str]:
        if not _EXTRA_KEYS_FILE.exists():
            return []
        try:
            data = json.loads(_EXTRA_KEYS_FILE.read_text())
            raw = data.get(service, [])
            if isinstance(raw, str):
                raw = [raw]
            return [k for k in raw if k]
        except Exception:
            return []

    def _get_key_entry(self, service: str, key: str) -> dict:
        self._health.setdefault(service, {}).setdefault("keys", {}).setdefault(key, {})
        return self._health[service]["keys"][key]

    def _touch_key(self, service: str, key: str) -> None:
        self._get_key_entry(service, key)["last_used"] = time.time()

    def _load_health(self) -> None:
        if _HEALTH_FILE.exists():
            try:
                self._health = json.loads(_HEALTH_FILE.read_text())
            except Exception:
                self._health = {}

    def _save_health(self) -> None:
        _HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            _HEALTH_FILE.write_text(json.dumps(self._health, indent=2))
        except Exception as exc:
            log.debug("Could not save key health: %s", exc)


# Module-level singleton
_manager: Optional[APIKeyManager] = None


def get_manager() -> APIKeyManager:
    global _manager
    if _manager is None:
        _manager = APIKeyManager()
    return _manager
