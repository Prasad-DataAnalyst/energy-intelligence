"""
modules/intelligence/self_enhancement_loop.py
24/7 background daemon that keeps the pipeline self-healing and self-improving.

Cycle schedule (all in background thread, never blocks the pipeline):
  Every  30 min  → Health check: probe all sources, quarantine failing ones
  Every   6 h    → Discovery: find new RSS feeds for today's trending topics
  Every   6 h    → Probe known public APIs that might have appeared
  Every  24 h    → Performance report: which sources produced the best content
  On start       → Immediate bootstrap (register any new env keys, probe sources)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Intervals in seconds
_HEALTH_CHECK_INTERVAL_S   = 30 * 60     # 30 minutes
_DISCOVERY_INTERVAL_S      = 6  * 3600   # 6 hours
_PERFORMANCE_INTERVAL_S    = 24 * 3600   # 24 hours

_REPORTS_DIR = Path(__file__).parent.parent.parent / "logs" / "enhancement_reports"
_PERF_LOG    = Path(__file__).parent.parent.parent / "logs" / "source_performance.json"


class SelfEnhancementLoop:
    """
    Long-running daemon that continuously improves pipeline data quality.

    Usage:
        loop = SelfEnhancementLoop()
        loop.start()          # background thread, non-blocking
        loop.stop()           # graceful shutdown
        loop.run_now()        # force immediate full cycle (blocking)
    """

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_health   = 0.0
        self._last_discovery = 0.0
        self._last_perf     = 0.0
        self._perf_log: Dict[str, List[dict]] = {}
        self._load_perf_log()

    # ------------------------------------------------------------------ #
    # Public control                                                       #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the background enhancement thread."""
        if self._thread and self._thread.is_alive():
            log.debug("SelfEnhancementLoop already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="SelfEnhancementLoop",
            daemon=True,
        )
        self._thread.start()
        log.info("SelfEnhancementLoop started (background daemon)")

    def stop(self) -> None:
        """Signal the loop to stop and wait for it."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("SelfEnhancementLoop stopped")

    def run_now(self) -> None:
        """Force a full immediate cycle (blocking — use for manual runs)."""
        log.info("SelfEnhancementLoop: running full cycle now")
        self._bootstrap()
        self._health_check_cycle()
        self._discovery_cycle()

    # ------------------------------------------------------------------ #
    # Main loop                                                            #
    # ------------------------------------------------------------------ #

    def _run_loop(self) -> None:
        self._bootstrap()

        while not self._stop_event.is_set():
            now = time.time()

            if now - self._last_health >= _HEALTH_CHECK_INTERVAL_S:
                try:
                    self._health_check_cycle()
                except Exception as exc:
                    log.warning("Health check cycle error: %s", exc)
                self._last_health = time.time()

            if now - self._last_discovery >= _DISCOVERY_INTERVAL_S:
                try:
                    self._discovery_cycle()
                except Exception as exc:
                    log.warning("Discovery cycle error: %s", exc)
                self._last_discovery = time.time()

            if now - self._last_perf >= _PERFORMANCE_INTERVAL_S:
                try:
                    self._performance_report_cycle()
                except Exception as exc:
                    log.warning("Performance cycle error: %s", exc)
                self._last_perf = time.time()

            # Sleep 60s between loop ticks, checking stop flag each second
            for _ in range(60):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    # ------------------------------------------------------------------ #
    # Bootstrap: runs once on start                                        #
    # ------------------------------------------------------------------ #

    def _bootstrap(self) -> None:
        """
        Scan the environment for API keys not yet in the key manager,
        ensure the source registry is populated, and log current status.
        """
        from services.api_key_manager import get_manager
        from services.source_registry import get_registry
        from services.adaptive_fetcher import source_health_report

        mgr = get_manager()
        reg = get_registry()

        # Scan environment for any *_API_KEY or *_KEY variables we might have missed
        discovered_keys = 0
        for var, value in os.environ.items():
            if not value.strip():
                continue
            name_lower = var.lower()
            if not ("api_key" in name_lower or name_lower.endswith("_key") or
                    name_lower.endswith("_token")):
                continue
            # Derive service name from env var: PEXELS_API_KEY → pexels
            service = (
                var.lower()
                   .replace("_api_key", "")
                   .replace("_key", "")
                   .replace("_token", "")
            )
            if service and not mgr.is_available(service):
                mgr.add_key(service, value, source="env_scan")
                discovered_keys += 1

        log.info(
            "Bootstrap: %d new keys from env scan. Registry: %d sources across %d categories.",
            discovered_keys,
            sum(len(reg.get_sources(c, require_available=False)) for c in reg.all_categories()),
            len(reg.all_categories()),
        )
        log.info("\n%s", source_health_report())

    # ------------------------------------------------------------------ #
    # Health check cycle (every 30 min)                                   #
    # ------------------------------------------------------------------ #

    def _health_check_cycle(self) -> None:
        """
        Probe all registered sources.
        - Re-activate sources that were quarantined > 6h ago
        - Probe keyless sources to verify they still respond
        - Update reliability scores
        """
        from services.source_registry import get_registry
        import requests

        reg = get_registry()
        probed = 0
        recovered = 0

        for cat in reg.all_categories():
            for src in reg.get_sources(cat, require_available=False):
                # Try to re-activate long-quarantined sources
                if not src.active:
                    age_quarantined = time.time() - src.last_tested
                    if age_quarantined > 6 * 3600:   # retry after 6 h
                        reg.reactivate(src.id)
                        recovered += 1
                        continue

                # Only probe keyless sources to avoid burning API quotas
                if src.requires_key:
                    continue
                if src.source_type == "local":
                    continue

                # Skip sources probed in last 20 min
                if time.time() - src.last_tested < 1200:
                    continue

                endpoint = src.endpoint
                if not endpoint:
                    continue

                try:
                    r = requests.get(
                        endpoint, timeout=6,
                        headers={"User-Agent": "mind-fuel-health/1.0"},
                    )
                    if r.status_code < 400:
                        reg.record_success(src.id)
                    else:
                        reg.record_failure(src.id)
                    probed += 1
                except Exception:
                    reg.record_failure(src.id)
                    probed += 1

                time.sleep(0.5)  # be polite

        log.info(
            "Health check: probed %d keyless sources, recovered %d from quarantine",
            probed, recovered,
        )

    # ------------------------------------------------------------------ #
    # Discovery cycle (every 6 h)                                         #
    # ------------------------------------------------------------------ #

    def _discovery_cycle(self) -> None:
        """
        1. Fetch today's top trending topics.
        2. Search for new RSS feeds related to each topic.
        3. Probe known public API candidates.
        4. Add working new sources to the registry.
        """
        from services.source_registry import get_registry
        from services.adaptive_fetcher import fetch_trending_topics

        reg = get_registry()
        total_new = 0

        # Get today's top topics to drive RSS discovery
        try:
            trends = fetch_trending_topics(limit=5)
            topics = [t["title"] for t in trends if t.get("title")]
        except Exception:
            topics = ["artificial intelligence", "finance", "health", "science", "technology"]

        log.info("Discovery cycle: searching for new sources on topics: %s",
                 ", ".join(topics[:3]))

        # RSS feed discovery per topic
        for topic in topics[:4]:   # cap at 4 topics per cycle
            try:
                new = reg.discover_rss_feeds(topic, max_new=3)
                total_new += new
                if new:
                    log.info("Discovered %d new RSS feeds for topic: %s", new, topic)
                time.sleep(1)
            except Exception as exc:
                log.debug("RSS discovery error for '%s': %s", topic, exc)

        # Probe for new public API endpoints
        try:
            new = reg.discover_public_apis()
            total_new += new
            if new:
                log.info("Discovered %d new public API sources", new)
        except Exception as exc:
            log.debug("Public API discovery error: %s", exc)

        # Scan for new image/video sources via known free-API index
        try:
            new = self._discover_free_media_sources(reg)
            total_new += new
        except Exception as exc:
            log.debug("Media source discovery error: %s", exc)

        log.info("Discovery cycle complete: %d new sources added", total_new)
        self._write_report("discovery", {
            "timestamp": datetime.utcnow().isoformat(),
            "topics_searched": topics[:4],
            "new_sources_found": total_new,
            "registry_total": sum(
                len(reg.get_sources(c, require_available=False))
                for c in reg.all_categories()
            ),
        })

    def _discover_free_media_sources(self, reg) -> int:
        """
        Check a curated list of completely-free, no-key media APIs.
        Adds any that respond successfully.
        """
        import requests

        candidates = [
            # Images
            {"id": "lorem_faces", "name": "ThisPersonDoesNotExist",
             "category": "images", "type": "api",
             "endpoint": "https://thispersondoesnotexist.com/",
             "priority": 85, "reliability": 0.90, "tags": ["keyless", "ai"]},
            {"id": "robohash",    "name": "RoboHash",
             "category": "images", "type": "api",
             "endpoint": "https://robohash.org/test.png",
             "priority": 88, "reliability": 0.95, "tags": ["keyless"]},
            # Stock video
            {"id": "mixkit_video", "name": "Mixkit Free Video",
             "category": "video", "type": "scrape",
             "endpoint": "https://mixkit.co/free-stock-video/",
             "priority": 20, "reliability": 0.70, "tags": ["keyless"]},
            # Music
            {"id": "musopen_api", "name": "Musopen (CC Classical)",
             "category": "music", "type": "api",
             "endpoint": "https://api.musopen.org/recordings?license=1&per_page=5",
             "priority": 25, "reliability": 0.72, "tags": ["keyless"]},
            {"id": "freemusicarchive", "name": "Free Music Archive",
             "category": "music", "type": "api",
             "endpoint": "https://freemusicarchive.org/api/get/tracks.json?limit=5",
             "priority": 22, "reliability": 0.70, "tags": ["keyless"]},
        ]

        added = 0
        for cand in candidates:
            if cand["id"] in {src.id for cat in reg.all_categories()
                              for src in reg.get_sources(cat, require_available=False)}:
                continue
            try:
                r = requests.get(cand["endpoint"], timeout=6,
                                 headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code < 400:
                    cand.setdefault("capabilities", [cand["category"]])
                    reg.add_source(cand)
                    added += 1
                    log.info("New media source verified: %s", cand["name"])
            except Exception:
                pass
            time.sleep(0.3)
        return added

    # ------------------------------------------------------------------ #
    # Performance report (every 24 h)                                     #
    # ------------------------------------------------------------------ #

    def record_video_outcome(
        self,
        sources_used: List[str],
        quality_score: float,
        views: int = 0,
        ctr: float = 0.0,
    ) -> None:
        """
        Call this after a video is produced and optionally after upload analytics.
        Records which sources were used and what quality/engagement resulted.
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "sources": sources_used,
            "quality_score": quality_score,
            "views": views,
            "ctr": ctr,
        }
        self._perf_log.setdefault("outcomes", []).append(entry)
        self._save_perf_log()

    def _performance_report_cycle(self) -> None:
        """
        Analyse the perf log to find which sources correlate with
        higher quality scores. Boosts priority of top performers.
        """
        from services.source_registry import get_registry

        outcomes = self._perf_log.get("outcomes", [])
        if len(outcomes) < 3:
            return   # not enough data yet

        reg = get_registry()

        # Map source_id → list of quality scores it contributed to
        source_quality: Dict[str, List[float]] = {}
        for outcome in outcomes[-50:]:   # last 50 videos
            for src_id in outcome.get("sources", []):
                source_quality.setdefault(src_id, []).append(outcome["quality_score"])

        adjustments = 0
        for src_id, scores in source_quality.items():
            src = reg.get_by_id(src_id)
            if not src:
                continue
            avg_q = sum(scores) / len(scores)
            # If source correlates with quality > 80, nudge priority up (lower number)
            if avg_q >= 80 and src.priority > 5:
                src.priority = max(1, src.priority - 2)
                adjustments += 1
            # If source correlates with quality < 50, nudge priority down
            elif avg_q < 50:
                src.priority = min(99, src.priority + 5)
                adjustments += 1

        if adjustments:
            reg.save()

        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "videos_analysed": min(50, len(outcomes)),
            "source_adjustments": adjustments,
            "top_performers": [
                {"source": s, "avg_quality": sum(q) / len(q)}
                for s, q in sorted(source_quality.items(),
                                   key=lambda x: -sum(x[1]) / len(x[1]))[:5]
                if len(q) >= 2
            ],
        }
        log.info("Performance report: %d source priorities adjusted", adjustments)
        self._write_report("performance", report)

    # ------------------------------------------------------------------ #
    # Reporting                                                            #
    # ------------------------------------------------------------------ #

    def _write_report(self, report_type: str, data: dict) -> None:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        path = _REPORTS_DIR / f"{report_type}_{date_str}.json"
        try:
            existing = []
            if path.exists():
                existing = json.loads(path.read_text())
            existing.append(data)
            path.write_text(json.dumps(existing, indent=2))
        except Exception as exc:
            log.debug("Report write failed: %s", exc)

    def _load_perf_log(self) -> None:
        if _PERF_LOG.exists():
            try:
                self._perf_log = json.loads(_PERF_LOG.read_text())
            except Exception:
                self._perf_log = {}

    def _save_perf_log(self) -> None:
        _PERF_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            _PERF_LOG.write_text(json.dumps(self._perf_log, indent=2))
        except Exception as exc:
            log.debug("Perf log save failed: %s", exc)


# Module-level singleton — import and call .start() from the pipeline
_loop: Optional[SelfEnhancementLoop] = None


def get_loop() -> SelfEnhancementLoop:
    global _loop
    if _loop is None:
        _loop = SelfEnhancementLoop()
    return _loop


def start_background_loop() -> SelfEnhancementLoop:
    """Convenience function: get the singleton and start it."""
    loop = get_loop()
    loop.start()
    return loop
