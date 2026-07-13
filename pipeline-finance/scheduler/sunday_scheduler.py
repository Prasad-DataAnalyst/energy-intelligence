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
    """Atomic write (temp file + rename) — the topic pool must never be truncated."""
    import os
    tmp = TOPIC_LIBRARY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, TOPIC_LIBRARY_PATH)


def _mark_topic_used(topic_id: str) -> None:
    """
    Record a topic as used today. Re-loads the FULL on-disk library before
    saving so a caller working with a filtered subset can never overwrite
    (and thereby lose) the rest of the topic pool.
    """
    try:
        library = _load_topic_library()
        library.setdefault("last_used", {})[topic_id] = date.today().isoformat()
        _save_topic_library(library)
    except Exception as exc:
        logger.warning("Could not persist last_used for topic '%s': %s", topic_id, exc)


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
    eligible = [
        t for t in topics
        if last_used.get(t.get("id", t.get("title", "")), "1970-01-01") < cutoff
    ]

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

    # Mark as used (writes last_used against the full on-disk library)
    topic_id = chosen.get("id")
    if topic_id:
        _mark_topic_used(topic_id)

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
        except Exception as exc:
            logger.debug("Week-cycle file unreadable (%s) — deriving from ISO week", exc)
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

        from scheduler.pipeline_state import PipelineState
        state = PipelineState("sunday")
        if state.outcome == "success":
            logger.info("Sunday pipeline already succeeded today — skipping re-run")
            from scheduler.weekday_scheduler import PipelineResult
            return PipelineResult(
                success=True, video_id=state.video_id, title=None,
                script_path=None, video_path=None, upload_result=None,
                duration_seconds=0.0, errors=[],
            )

        video_path = None
        video_id = None
        title = None

        try:
            # ── Pick topic (with 14-day dedup gate) ────────────────────────
            state.mark_started("pick_topic")
            from scheduler.post_upload import check_topic_duplicate
            library = _load_topic_library()
            topic_data = _pick_topic(library)
            for _attempt in range(3):
                if not check_topic_duplicate(topic_data.get("title", "")):
                    break
                logger.info(
                    "Topic '%s' duplicates recent content — re-picking",
                    topic_data.get("title", "")[:60],
                )
                topic_data = _pick_topic(library)
            state.mark_done("pick_topic", artifacts={"topic": topic_data.get("title", "")})

            topic = topic_data["title"]
            current_relevance = topic_data.get("current_relevance", "")
            tags_base = topic_data.get("tags", [])

            # Determine theme from week cycle
            theme = topic_data.get("theme") or self.get_sunday_theme()
            week_context = current_relevance or "Markets were active this week."
            bonus_theme = topic_data.get("bonus_theme", "macro_finance")

            # ── Generate script (day-scoped cache avoids duplicate Claude calls)
            logger.info("Generating Sunday script for: %s | theme=%s", topic, theme)
            state.mark_started("generate_script")
            from generators.script_gen import generate_sunday_script, ScriptGenerator
            _sg = ScriptGenerator()
            script = _sg.get_cached_script("sunday", topic, tier=0)
            if script is not None:
                logger.info("Using cached Sunday script (Claude call skipped)")
            else:
                script = generate_sunday_script(
                    topic=topic,
                    theme=theme,
                    week_context=week_context,
                    bonus_theme=bonus_theme,
                )
                _sg.cache_script(script, topic=topic, tier=0)
            script.save(settings.output_dir / "scripts")
            state.mark_done("generate_script")

            # ── Compliance ─────────────────────────────────────────────────
            from generators.compliance_filter import check_compliance, auto_fix_script
            compliance = check_compliance(script.script)
            if not compliance.passed:
                script.script = auto_fix_script(script.script, compliance)
                if compliance.risk_level == "high":
                    self.errors.append(f"High compliance risk: {compliance.issues}")
                    state.mark_failed("compliance_check", str(compliance.issues))
                    state.finish(success=False)
                    return None
            state.mark_done("compliance_check")

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
            from generators.audio_gen import generate_audio, normalize_loudness
            audio = generate_audio(script.segments, "sunday")
            if audio.merged_path:
                normalize_loudness(audio.merged_path)   # -16 LUFS, non-fatal

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
                    description = title_set.description or (
                        f"{topic}\n\n{current_relevance}\n\n"
                        f"⚠️ {settings.disclaimer_text}\n\n" +
                        " ".join(f"#{t.replace(' ', '')}" for t in tags[:10])
                    )
                    try:
                        from generators.title_gen import generate_chapter_markers
                        chapters = generate_chapter_markers(
                            script.script, audio.total_duration_seconds
                        )
                        if chapters:
                            description += f"\n\n⏱️ Chapters:\n{chapters}"
                    except Exception as exc:
                        logger.warning("Chapter enrichment skipped: %s", exc)

                    config = UploadConfig(
                        title=title,
                        description=description,
                        tags=tags[:30],
                        category="Education",
                        publish_at=publish_at_utc,
                        video_type="sunday",
                    )

                    # Preflight gate — never spend quota on broken artifacts
                    from uploader.preflight import PreflightChecker
                    preflight = PreflightChecker(quota_tracker=quota).run(
                        video_path=video_path,
                        thumbnail_path=thumbnail.path,
                        title=config.title,
                        description=config._compliance_description(),
                        audio_path=audio.merged_path,
                    )
                    if not preflight.passed:
                        self.errors.append(f"Preflight failed: {preflight.errors}")
                        logger.error("Sunday upload blocked by preflight: %s", preflight.summary())
                        state.mark_failed("upload", f"preflight: {preflight.errors}")
                    else:
                        state.mark_started("upload")
                        upload_result = upload_full(
                            video_path=video_path,
                            config=config,
                            thumbnail_path=thumbnail.path,
                            quota_tracker=quota,
                        )
                        video_id = upload_result.video_id
                        if video_id:
                            state.mark_done("upload", artifacts={"video_id": video_id})
                            state.mark_started("post_upload")
                            from scheduler.post_upload import finalize_upload
                            finalize_upload(
                                video_id=video_id,
                                video_type="sunday",
                                title=title,
                                upload_result=upload_result,
                                upload_config=config,
                                script_text=script.script,
                                topic=topic,
                                video_duration_seconds=audio.total_duration_seconds,
                                sunday_theme=theme,
                            )
                            state.mark_done("post_upload")
                        else:
                            state.mark_failed("upload", upload_result.error or "unknown")
                else:
                    logger.warning("Sunday upload skipped — quota exceeded")

        except Exception as exc:
            logger.exception("Sunday pipeline error: %s", exc)
            self.errors.append(str(exc))
            current = state.next_step()
            if current:
                state.mark_failed(current, str(exc))

        duration = (datetime.now() - start).total_seconds()
        success = len(self.errors) == 0 and video_path is not None
        state.finish(success=success, video_id=video_id)
        from scheduler.weekday_scheduler import PipelineResult
        return PipelineResult(
            success=success,
            video_id=video_id,
            title=title,
            script_path=None,
            video_path=video_path,
            upload_result={"video_id": video_id} if video_id else None,
            duration_seconds=duration,
            errors=self.errors,
        )
