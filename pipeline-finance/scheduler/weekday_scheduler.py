"""
Weekday pipeline orchestrator.
Runs the full scrape → generate → compile → upload workflow
for market recap videos (Mon–Fri, 8AM and 5PM ET).
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    success: bool
    video_id: Optional[str]
    title: Optional[str]
    script_path: Optional[Path]
    video_path: Optional[Path]
    upload_result: Optional[dict]
    duration_seconds: float
    errors: list[str] = field(default_factory=list)
    run_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __str__(self) -> str:
        status = "SUCCESS ✅" if self.success else "FAILED ❌"
        return (
            f"[{status}] {self.title or 'Unknown'} | "
            f"VideoID: {self.video_id or 'N/A'} | "
            f"Duration: {self.duration_seconds:.1f}s"
        )


class WeekdayScheduler:
    """Orchestrates the weekday market recap pipeline end-to-end."""

    def __init__(self):
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.errors: list[str] = []

    def _log_step(self, step: str, detail: str = "") -> None:
        logger.info("[%s] STEP: %s %s", self.run_id, step, f"— {detail}" if detail else "")

    def is_market_day(self, dt: Optional[datetime] = None) -> bool:
        """Return True if dt (default: now) is a US market trading day (Mon–Fri, not a holiday)."""
        US_MARKET_HOLIDAYS = {
            # NYSE 2026 observed holidays (yyyy-mm-dd)
            "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
            "2026-05-25", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        }
        from zoneinfo import ZoneInfo
        now = dt or datetime.now(ZoneInfo(settings.timezone))
        if now.weekday() >= 5:   # Saturday=5, Sunday=6
            return False
        if now.strftime("%Y-%m-%d") in US_MARKET_HOLIDAYS:
            return False
        return True

    def run_morning_pipeline(self) -> PipelineResult:
        """
        06:00 ET — scrape, generate, and build all assets.
        Does NOT upload — assets are stored for the scheduled upload windows.
        """
        logger.info("[%s] run_morning_pipeline starting (06:00 slot)", self.run_id)
        return self.run()

    def upload_morning(self) -> None:
        """07:00 ET upload slot — publish overnight-generated content."""
        logger.info("[%s] upload_morning slot (07:00 ET)", self.run_id)
        try:
            from uploader.uploader import YouTubeUploader
            from uploader.quota_tracker import QuotaTracker
            qt = QuotaTracker()
            uploader = YouTubeUploader(qt)
            uploader.process_failed_queue()
        except Exception as exc:
            logger.error("upload_morning failed: %s", exc)
            self.errors.append(f"upload_morning: {exc}")

    def upload_midday(self) -> None:
        """12:30 ET upload slot — publish Shorts generated during morning run."""
        logger.info("[%s] upload_midday slot (12:30 ET)", self.run_id)
        try:
            from pathlib import Path as _Path
            from uploader.quota_tracker import QuotaTracker
            from uploader.uploader import YouTubeUploader, UploadConfig
            qt = QuotaTracker()
            if not qt.can_upload():
                logger.warning("Midday upload skipped — quota exceeded")
                return
            shorts_dir = settings.output_dir / "shorts"
            today_str = datetime.now().strftime("%Y%m%d")
            shorts = sorted(shorts_dir.glob(f"short_*{today_str}*.mp4"), reverse=True)
            if not shorts:
                logger.info("No Shorts to upload at midday")
                return
            uploader = YouTubeUploader(qt)
            if not uploader.authenticate():
                return
            for short_path in shorts[:1]:  # only upload 1 midday short
                uploader.upload_short(
                    video_path=short_path,
                    title=f"Market Snapshot #{today_str} #Shorts",
                    description=settings.disclaimer_text,
                    tags=["Shorts", "stocks", "market", "DriftWire326"],
                )
        except Exception as exc:
            logger.error("upload_midday failed: %s", exc)
            self.errors.append(f"upload_midday: {exc}")

    def upload_afternoon(self) -> None:
        """16:30 ET upload slot — publish the main market recap video."""
        logger.info("[%s] upload_afternoon slot (16:30 ET)", self.run_id)
        try:
            from uploader.quota_tracker import QuotaTracker
            from uploader.uploader import YouTubeUploader
            qt = QuotaTracker()
            if not qt.can_upload():
                logger.warning("Afternoon upload skipped — quota exceeded")
                return
            videos_dir = settings.output_dir / "videos"
            today_str = datetime.now().strftime("%Y%m%d")
            videos = sorted(videos_dir.glob(f"weekday_*{today_str}*.mp4"), reverse=True)
            if not videos:
                logger.info("No weekday videos to upload at 16:30")
                return
            uploader = YouTubeUploader(qt)
            if not uploader.authenticate():
                return
            uploader.upload_main_video(
                video_path=videos[0],
                title=f"Stock Market Recap {today_str}",
                description=settings.disclaimer_text,
                tags=["stocks", "market recap", "DriftWire326"],
            )
        except Exception as exc:
            logger.error("upload_afternoon failed: %s", exc)
            self.errors.append(f"upload_afternoon: {exc}")

    def run(self) -> PipelineResult:
        start = datetime.now()
        logger.info("WeekdayScheduler run started: %s", self.run_id)

        script_path = None
        video_path = None
        video_id = None
        title = None

        try:
            # ── Step 1: Scrape market data ─────────────────────────────────
            self._log_step("1/7", "Scraping market data")
            from scrapers.market_scraper import scrape_market
            market = scrape_market()
            market_narrative = market.to_narrative()

            # ── Step 2: Scrape earnings ────────────────────────────────────
            self._log_step("2/7", "Scraping earnings calendar")
            from scrapers.earnings_scraper import scrape_earnings
            earnings = scrape_earnings()
            earnings_narrative = earnings.to_narrative()

            # ── Step 3: Scrape economic data ───────────────────────────────
            self._log_step("3/7", "Scraping economic indicators")
            from scrapers.economic_scraper import scrape_economic_data
            economic = scrape_economic_data()
            economic_narrative = economic.to_narrative()

            # ── Step 4: Generate script ────────────────────────────────────
            self._log_step("4/7", "Generating script via Claude AI")
            from generators.script_gen import generate_weekday_script
            script = generate_weekday_script(
                market_narrative=market_narrative,
                earnings_narrative=earnings_narrative,
                economic_narrative=economic_narrative,
            )
            script_path = script.save(settings.output_dir / "scripts")

            # ── Step 5: Compliance check ───────────────────────────────────
            self._log_step("5/7", "Running compliance filter")
            from generators.compliance_filter import check_compliance, auto_fix_script
            compliance = check_compliance(script.script)
            if not compliance.passed:
                logger.warning("Compliance issues found: %s", compliance.issues)
                fixed_script_text = auto_fix_script(script.script, compliance)
                script.script = fixed_script_text
                if compliance.risk_level == "high":
                    self.errors.append(f"HIGH compliance risk — manual review required: {compliance.issues}")
                    logger.error("Script has HIGH compliance risk — aborting upload")
                    return PipelineResult(
                        success=False, video_id=None, title=script.title_draft,
                        script_path=script_path, video_path=None, upload_result=None,
                        duration_seconds=(datetime.now() - start).total_seconds(),
                        errors=self.errors,
                    )

            # ── Step 6: Generate supporting assets ────────────────────────
            self._log_step("6/7", "Generating charts, audio, thumbnail, titles")

            from generators.chart_generator import generate_all_charts
            charts = generate_all_charts(market)
            chart_paths = [c.path for c in charts]

            from generators.audio_gen import generate_audio
            audio = generate_audio(script.segments, "weekday")

            sentiment = market.sp500.sentiment.replace("strongly_", "")
            from generators.title_gen import generate_title_set
            title_set = generate_title_set(
                topic=script.title_draft,
                key_stat=f"S&P {market.sp500.change_pct:+.2f}%",
                video_type="weekday_recap",
                script_summary=script.script[:500],
            )
            title = title_set.winner.title

            from generators.thumbnail_gen import generate_thumbnail_from_claude
            thumbnail = generate_thumbnail_from_claude(
                video_title=title,
                key_stat=f"S&P {market.sp500.change_pct:+.2f}%",
                sentiment=sentiment,
                chart_path=chart_paths[0] if chart_paths else None,
            )

            # ── Step 7: Build video ────────────────────────────────────────
            self._log_step("7a/7", "Building main video")
            from builders.video_builder import build_video, VideoAssets
            if audio.merged_path:
                video_assets = VideoAssets(
                    audio_path=audio.merged_path,
                    chart_paths=chart_paths,
                    thumbnail_path=thumbnail.path,
                    script_segments=script.segments,
                    video_type="weekday",
                    title=title,
                    duration_seconds=audio.total_duration_seconds,
                )
                built_video = build_video(video_assets)
                video_path = built_video.path

                # Build Shorts from hook segment
                self._log_step("7b/7", "Building Short")
                from builders.shorts_builder import build_short, ShortsAssets
                hook_text = script.segments.get("HOOK", market_narrative[:100])
                shorts_assets = ShortsAssets(
                    audio_path=audio.merged_path,
                    chart_paths=chart_paths,
                    thumbnail_path=thumbnail.path,
                    script=script.script,
                    title=f"WATCH THIS → {market.sp500.change_pct:+.2f}% | Market Recap",
                    hook_text=hook_text[:80],
                    key_stat=f"S&P {market.sp500.change_pct:+.2f}%",
                    ticker="SPY",
                    sentiment=sentiment,
                )
                try:
                    short = build_short(shorts_assets)
                    logger.info("Short built: %s", short.path.name)
                except Exception as exc:
                    logger.warning("Short build failed (non-fatal): %s", exc)

            # ── Upload ─────────────────────────────────────────────────────
            if video_path and video_path.exists():
                from uploader.quota_tracker import QuotaTracker
                from uploader.uploader import upload_full, UploadConfig

                quota = QuotaTracker()
                if quota.can_upload():
                    publish_at = _next_publish_time()
                    config = UploadConfig(
                        title=title,
                        description=title_set.description or _fallback_description(market, earnings),
                        tags=_build_tags(market),
                        publish_at=publish_at,
                    )
                    upload_result = upload_full(
                        video_path=video_path,
                        config=config,
                        thumbnail_path=thumbnail.path,
                        quota_tracker=quota,
                    )
                    video_id = upload_result.video_id
                    logger.info("Upload result: %s", upload_result)
                else:
                    logger.warning("Upload skipped — quota exceeded. %s", quota.report())

        except Exception as exc:
            logger.exception("Pipeline error: %s", exc)
            self.errors.append(str(exc))

        duration = (datetime.now() - start).total_seconds()
        return PipelineResult(
            success=len(self.errors) == 0 and video_path is not None,
            video_id=video_id,
            title=title,
            script_path=script_path,
            video_path=video_path,
            upload_result={"video_id": video_id} if video_id else None,
            duration_seconds=duration,
            errors=self.errors,
        )


def _next_publish_time() -> datetime:
    """Return the next optimal publish slot (5 PM ET same day or next weekday)."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo(settings.timezone))
    target_hour = 17
    candidate = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if now.hour >= target_hour:
        # Already past 5PM — push to 8AM next day
        candidate = (candidate + timedelta(days=1)).replace(hour=8)
        # Skip weekends
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _build_tags(market) -> list[str]:
    base = [
        "stock market today", "market recap", "investing", "S&P 500",
        "Nasdaq", "Wall Street", "DriftWire326", "finance news",
        "day trading", "stock market news", "market analysis 2026",
    ]
    if market.sp500.change_pct < -1:
        base += ["market crash", "stocks falling", "bear market"]
    elif market.sp500.change_pct > 1:
        base += ["stocks rising", "bull market", "market rally"]
    return base[:30]


def _fallback_description(market, earnings) -> str:
    return (
        f"{market.sp500.change_pct:+.2f}% | Daily market recap by Drift Wire326.\n\n"
        f"{market.to_narrative()}\n\n"
        f"{earnings.to_narrative()}\n\n"
        f"⚠️ {settings.disclaimer_text}\n\n"
        "#StockMarket #Investing #MarketRecap #DriftWire326"
    )
