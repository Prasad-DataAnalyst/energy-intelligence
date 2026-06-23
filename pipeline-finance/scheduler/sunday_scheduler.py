"""
Sunday educational pipeline — picks topic from the library,
generates a deep-dive script, builds the video, uploads.
Tracks used topics to avoid repeats within the cooldown window.
"""
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

TOPIC_LIBRARY_PATH = Path(__file__).parent.parent / "scrapers" / "sunday_topic_library.json"


def _load_topic_library() -> dict:
    return json.loads(TOPIC_LIBRARY_PATH.read_text(encoding="utf-8"))


def _save_topic_library(data: dict) -> None:
    TOPIC_LIBRARY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _pick_topic(library: dict) -> dict:
    """
    Weighted random selection. Respects cooldown_weeks — avoids
    topics used within the cooldown window.
    """
    topics = library["topics"]
    weights = library["rotation_schedule"]["weights"]
    cooldown_weeks = library["rotation_schedule"]["cooldown_weeks"]
    last_used: dict = library.get("last_used", {})

    cutoff = (date.today() - timedelta(weeks=cooldown_weeks)).isoformat()
    eligible = [t for t in topics if last_used.get(t["id"], "1970-01-01") < cutoff]

    if not eligible:
        logger.warning("All topics in cooldown — using full list")
        eligible = topics

    # Build weighted pool
    pool = []
    for topic in eligible:
        w = weights.get(topic.get("estimated_views", "medium"), 1)
        pool.extend([topic] * w)

    chosen = random.choice(pool)
    logger.info("Selected Sunday topic: %s", chosen["title"])

    # Mark as used
    library["last_used"][chosen["id"]] = date.today().isoformat()
    _save_topic_library(library)

    return chosen


_THEME_CYCLE = [
    "investment_banking",
    "insurance_protection",
    "savings_wealth",
    "rotating_bonus",
]

_WEEK_CYCLE_FILE = Path(__file__).parent.parent / "logs" / "sunday_week_cycle.json"


class SundayScheduler:
    """Orchestrates the Sunday educational pipeline."""

    def __init__(self):
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.errors: list[str] = []

    def get_week_in_cycle(self) -> int:
        """Return 0-based week index within the 4-week theme cycle."""
        try:
            if _WEEK_CYCLE_FILE.exists():
                data = json.loads(_WEEK_CYCLE_FILE.read_text())
                return int(data.get("week_index", 0)) % len(_THEME_CYCLE)
        except Exception:
            pass
        return date.today().isocalendar()[1] % len(_THEME_CYCLE)

    def get_sunday_theme(self) -> str:
        """Return the Sunday theme for this week based on cycle position."""
        idx = self.get_week_in_cycle()
        theme = _THEME_CYCLE[idx]
        logger.info("Sunday theme this week: %s (week %d in cycle)", theme, idx)
        return theme

    def get_sunday_topic(self, theme: str) -> dict:
        """Pick a topic from the library matching the given theme (best-effort)."""
        try:
            library = _load_topic_library()
            topics = library["topics"]
            theme_topics = [t for t in topics if t.get("theme") == theme]
            if not theme_topics:
                theme_topics = topics
            return _pick_topic({"topics": theme_topics, **{k: v for k, v in library.items() if k != "topics"}})
        except Exception as exc:
            logger.warning("get_sunday_topic failed (%s) — using default: %s", theme, exc)
            return {"title": f"{theme.replace('_', ' ').title()} Explained", "theme": theme,
                    "tags": [], "current_relevance": "Timely financial education topic"}

    def run_sunday_pipeline(self) -> Optional["PipelineResult"]:
        """
        Full Sunday pipeline with correct generate_sunday_script() call.
        Detects theme from week cycle, picks topic from library.
        """
        return self.run()

    def upload_morning_sunday(self) -> None:
        """10:00 ET Sunday upload slot."""
        logger.info("[%s] upload_morning_sunday (10:00 ET)", self.run_id)
        try:
            from uploader.quota_tracker import QuotaTracker
            from uploader.uploader import YouTubeUploader
            qt = QuotaTracker()
            if not qt.can_upload():
                logger.warning("Morning Sunday upload skipped — quota exceeded")
                return
            uploader = YouTubeUploader(qt)
            uploader.process_failed_queue()
        except Exception as exc:
            logger.error("upload_morning_sunday failed: %s", exc)
            self.errors.append(f"upload_morning_sunday: {exc}")

    def upload_afternoon_sunday(self) -> None:
        """16:00 ET Sunday upload slot — main educational video."""
        logger.info("[%s] upload_afternoon_sunday (16:00 ET)", self.run_id)
        try:
            from uploader.quota_tracker import QuotaTracker
            from uploader.uploader import YouTubeUploader
            qt = QuotaTracker()
            if not qt.can_upload():
                logger.warning("Afternoon Sunday upload skipped — quota exceeded")
                return
            videos_dir = settings.output_dir / "videos"
            today_str = datetime.now().strftime("%Y%m%d")
            videos = sorted(videos_dir.glob(f"sunday_*{today_str}*.mp4"), reverse=True)
            if not videos:
                logger.info("No Sunday videos ready for afternoon upload")
                return
            uploader = YouTubeUploader(qt)
            if not uploader.authenticate():
                return
            uploader.upload_main_video(
                video_path=videos[0],
                title=f"Sunday Finance Deep Dive {today_str}",
                description=settings.disclaimer_text,
                tags=["investing", "financial education", "DriftWire326"],
            )
        except Exception as exc:
            logger.error("upload_afternoon_sunday failed: %s", exc)
            self.errors.append(f"upload_afternoon_sunday: {exc}")

    def run(self):
        start = datetime.now()
        logger.info("SundayScheduler started: %s", self.run_id)

        video_path = None
        video_id = None
        title = None

        try:
            # ── Pick topic ─────────────────────────────────────────────────
            library = _load_topic_library()
            topic_data = _pick_topic(library)

            topic = topic_data["title"]
            current_relevance = topic_data.get("current_relevance", "")
            tags_base = topic_data.get("tags", [])

            # Determine theme from week cycle
            theme = topic_data.get("theme") or self.get_sunday_theme()
            week_context = current_relevance or "Markets were active this week."
            bonus_theme = topic_data.get("bonus_theme", "macro_finance")

            # ── Generate script ────────────────────────────────────────────
            logger.info("Generating Sunday script for: %s | theme=%s", topic, theme)
            from generators.script_gen import generate_sunday_script
            script = generate_sunday_script(
                topic=topic,
                theme=theme,
                week_context=week_context,
                bonus_theme=bonus_theme,
            )
            script.save(settings.output_dir / "scripts")

            # ── Compliance ─────────────────────────────────────────────────
            from generators.compliance_filter import check_compliance, auto_fix_script
            compliance = check_compliance(script.script)
            if not compliance.passed:
                script.script = auto_fix_script(script.script, compliance)
                if compliance.risk_level == "high":
                    self.errors.append(f"High compliance risk: {compliance.issues}")
                    return None

            # ── Charts (educational — concept charts) ──────────────────────
            # Sunday videos use simpler concept charts (no market data needed)
            # Build a placeholder index chart using the tracked tickers
            from scrapers.market_scraper import scrape_market
            try:
                market = scrape_market()
                from generators.chart_generator import generate_all_charts
                charts = generate_all_charts(market)
                chart_paths = [c.path for c in charts[:2]]
            except Exception as exc:
                logger.warning("Market data unavailable for Sunday charts: %s", exc)
                chart_paths = []

            # ── Audio ──────────────────────────────────────────────────────
            from generators.audio_gen import generate_audio
            audio = generate_audio(script.segments, "sunday")

            # ── Titles ─────────────────────────────────────────────────────
            from generators.title_gen import generate_title_set
            title_set = generate_title_set(
                topic=topic,
                key_stat=current_relevance[:80],
                video_type="sunday_educational",
                script_summary=script.script[:500],
            )
            title = title_set.winner.title

            # ── Thumbnail ──────────────────────────────────────────────────
            from generators.thumbnail_gen import generate_thumbnail_from_claude
            thumbnail = generate_thumbnail_from_claude(
                video_title=title,
                key_stat=current_relevance[:50],
                sentiment="neutral",
                chart_path=chart_paths[0] if chart_paths else None,
            )

            # ── Build video ────────────────────────────────────────────────
            if audio.merged_path:
                from builders.video_builder import build_video, VideoAssets
                video_assets = VideoAssets(
                    audio_path=audio.merged_path,
                    chart_paths=chart_paths,
                    thumbnail_path=thumbnail.path,
                    script_segments=script.segments,
                    video_type="sunday",
                    title=title,
                    duration_seconds=audio.total_duration_seconds,
                )
                built_video = build_video(video_assets)
                video_path = built_video.path

            # ── Upload ─────────────────────────────────────────────────────
            if video_path and video_path.exists():
                from uploader.quota_tracker import QuotaTracker
                from uploader.uploader import upload_full, UploadConfig
                from zoneinfo import ZoneInfo

                quota = QuotaTracker()
                if quota.can_upload():
                    # Publish Sunday at 11AM ET
                    now = datetime.now(ZoneInfo(settings.timezone))
                    publish_at = now.replace(hour=11, minute=0, second=0, microsecond=0)
                    if now.hour >= 11:
                        publish_at += timedelta(days=7)  # next Sunday
                    publish_at_utc = publish_at.astimezone(timezone.utc)

                    tags = tags_base + [
                        "investing education", "financial literacy", "DriftWire326",
                        "stock market explained", "finance for beginners", "2026 investing",
                    ]
                    config = UploadConfig(
                        title=title,
                        description=title_set.description or (
                            f"{topic}\n\n{current_relevance}\n\n"
                            f"⚠️ {settings.disclaimer_text}\n\n" +
                            " ".join(f"#{t.replace(' ', '')}" for t in tags[:10])
                        ),
                        tags=tags[:30],
                        category="Education",
                        publish_at=publish_at_utc,
                    )
                    upload_result = upload_full(
                        video_path=video_path,
                        config=config,
                        thumbnail_path=thumbnail.path,
                        quota_tracker=quota,
                    )
                    video_id = upload_result.video_id
                else:
                    logger.warning("Sunday upload skipped — quota exceeded")

        except Exception as exc:
            logger.exception("Sunday pipeline error: %s", exc)
            self.errors.append(str(exc))

        duration = (datetime.now() - start).total_seconds()
        from scheduler.weekday_scheduler import PipelineResult
        return PipelineResult(
            success=len(self.errors) == 0 and video_path is not None,
            video_id=video_id,
            title=title,
            script_path=None,
            video_path=video_path,
            upload_result={"video_id": video_id} if video_id else None,
            duration_seconds=duration,
            errors=self.errors,
        )
