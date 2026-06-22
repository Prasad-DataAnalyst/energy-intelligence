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
