"""
DriftWire326 — YouTube Finance Automation Pipeline
Entry point supporting manual runs, dry-runs, and scheduler launch.

Usage:
  python main.py                         # Start the full scheduler (runs forever)
  python main.py --mode full             # Full pipeline: scrape+build+upload
  python main.py --mode scrape           # Scrape only (no generation)
  python main.py --mode build            # Build assets only (no upload)
  python main.py --mode upload           # Upload previously built videos
  python main.py --mode test             # Run test suite
  python main.py --mode sunday           # Sunday pipeline once
  python main.py --run weekday           # Run weekday pipeline once now
  python main.py --run sunday            # Run Sunday pipeline once now
  python main.py --run monitor           # Run monitor check once
  python main.py --dry-run weekday       # Dry-run: scrape + generate only (no upload)
  python main.py --quota                 # Show current API quota status
  python main.py --test                  # Run test suite
  python main.py --mode full --date 2026-06-23 --topic "AAPL earnings"
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# ── Bootstrap path so all modules import cleanly ───────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import settings


def _setup_logging(level: str = "INFO") -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    log_file = settings.logs_dir / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)

    # Quiet noisy third-party loggers
    for noisy in ["urllib3", "googleapiclient", "yfinance", "PIL", "matplotlib"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print_banner() -> None:
    print("""
╔══════════════════════════════════════════════════════════╗
║         DriftWire326 — YouTube Finance Automation        ║
║         Channel: @DriftWire326 | Niche: US Markets       ║
╚══════════════════════════════════════════════════════════╝
""")


def cmd_run_weekday(dry_run: bool = False) -> int:
    logger = logging.getLogger("main")
    logger.info("Running WEEKDAY pipeline (dry_run=%s)", dry_run)

    if dry_run:
        logger.info("[DRY RUN] Scraping and generating — no upload will occur")
        try:
            from scrapers.market_scraper import scrape_market
            market = scrape_market()
            print(market.to_narrative())

            from scrapers.earnings_scraper import scrape_earnings
            earnings = scrape_earnings()
            print(earnings.to_narrative())

            from scrapers.economic_scraper import scrape_economic_data
            economic = scrape_economic_data()
            print(economic.to_narrative())

            from generators.script_gen import generate_weekday_script
            script = generate_weekday_script(
                market.to_narrative(), earnings.to_narrative(), economic.to_narrative()
            )
            path = script.save(settings.output_dir / "scripts")
            print(f"\n✅ Script saved: {path}")
            print(f"   Words: {script.word_count} | Est. duration: {script.estimated_duration_seconds}s")

            from generators.compliance_filter import check_compliance
            result = check_compliance(script.script)
            print(f"   Compliance: {result.summary}")
            return 0
        except Exception as exc:
            logger.exception("Dry-run failed: %s", exc)
            return 1

    from scheduler.weekday_scheduler import WeekdayScheduler
    scheduler = WeekdayScheduler()
    result = scheduler.run()
    print(f"\n{'✅' if result.success else '❌'} {result}")
    if result.errors:
        for err in result.errors:
            print(f"   ERROR: {err}")
    return 0 if result.success else 1


def cmd_run_sunday(dry_run: bool = False) -> int:
    logger = logging.getLogger("main")
    logger.info("Running SUNDAY pipeline (dry_run=%s)", dry_run)
    from scheduler.sunday_scheduler import SundayScheduler
    scheduler = SundayScheduler()
    result = scheduler.run()
    if result:
        print(f"\n{'✅' if result.success else '❌'} {result}")
    else:
        print("❌ Sunday pipeline returned no result (compliance failure likely)")
    return 0 if (result and result.success) else 1


def cmd_run_monitor() -> int:
    from monitor.monitor import ChannelMonitor
    monitor = ChannelMonitor()
    monitor.run_check()
    return 0


def cmd_mode_scrape(date_str: str = "") -> int:
    """
    Scrape every data source and report what each one returned.

    Trends was missing from a command whose docstring says "all data
    sources", which is how it went a long time with every query failing and
    nobody looking: the only place it ran was inside a pipeline that treats
    it as optional and swallows the result.

    Each source is reported separately and a failure in one does not stop
    the others, because the useful answer here is which sources are working,
    not whether all of them are.
    """
    logger = logging.getLogger("main")
    logger.info("MODE: scrape (date=%s)", date_str or "today")

    def _try(label, fn):
        try:
            value = fn()
            print(f"  ✅ {label:9} {value}")
            return True
        except Exception as exc:
            print(f"  ❌ {label:9} {type(exc).__name__}: {exc}")
            logger.warning("%s scrape failed: %s", label, exc)
            return False

    print()
    results = [
        _try("Market", lambda: __import__(
            "scrapers.market_scraper", fromlist=["scrape_market"]
        ).scrape_market().to_narrative()[:110] + "..."),
        _try("Earnings", lambda: __import__(
            "scrapers.earnings_scraper", fromlist=["scrape_earnings"]
        ).scrape_earnings().to_narrative()[:110] + "..."),
        _try("Economic", lambda: __import__(
            "scrapers.economic_scraper", fromlist=["scrape_economic_data"]
        ).scrape_economic_data().to_narrative()[:110] + "..."),
        _try("Trends", _trends_summary),
    ]
    print(f"\n{sum(results)} of {len(results)} sources returned data")
    if not all(results):
        print("A failed source degrades the video rather than stopping it — "
              "the run still publishes.")
    return 0 if any(results) else 1


def _trends_summary() -> str:
    """What Google Trends returned, or why nothing came back."""
    from scrapers.trends_scraper import TrendsScraper
    rising = TrendsScraper().get_rising_queries()
    if not rising:
        return "no rising queries (rate limited, or none above threshold)"
    top = ", ".join(q["query"] for q in rising[:3])
    return f"{len(rising)} rising queries — {top}"


def cmd_mode_build(topic: str = "") -> int:
    """Generate script + build assets (no upload)."""
    logger = logging.getLogger("main")
    logger.info("MODE: build (topic=%s)", topic or "auto")
    try:
        from scrapers.market_scraper import scrape_market
        from scrapers.earnings_scraper import scrape_earnings
        from scrapers.economic_scraper import scrape_economic_data
        from generators.script_gen import generate_weekday_script

        market = scrape_market()
        earnings = scrape_earnings()
        economic = scrape_economic_data()

        script = generate_weekday_script(
            market_narrative=market.to_narrative(),
            earnings_narrative=earnings.to_narrative(),
            economic_narrative=economic.to_narrative(),
            topic=topic or "",
        )
        path = script.save(settings.output_dir / "scripts")
        from generators.compliance_filter import check_compliance
        result = check_compliance(script.script)
        print(f"✅ Script saved: {path}")
        print(f"   Compliance: {result.summary}")
        return 0
    except Exception as exc:
        logger.exception("Build failed: %s", exc)
        return 1


def cmd_mode_upload() -> int:
    """Upload most recent pre-built video from output/videos/."""
    logger = logging.getLogger("main")
    logger.info("MODE: upload")
    try:
        from uploader.quota_tracker import QuotaTracker
        from uploader.uploader import YouTubeUploader
        qt = QuotaTracker()
        if not qt.can_upload():
            print(f"❌ Quota too low:\n{qt.report()}")
            return 1
        uploader = YouTubeUploader(qt)
        uploader.process_failed_queue()
        print("✅ Upload mode: processed failed queue")
        return 0
    except Exception as exc:
        logger.exception("Upload failed: %s", exc)
        return 1


def cmd_quota() -> int:
    from uploader.quota_tracker import QuotaTracker
    tracker = QuotaTracker()
    print(tracker.report())
    return 0


def cmd_health() -> int:
    from monitor.health_report import run_health_report
    return run_health_report()


def cmd_test() -> int:
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=str(settings.root_dir),
    )
    return result.returncode


def cmd_pull_analytics() -> int:
    """
    Run the nightly analytics pull now and say what it found.

    The pull only ever ran at 21:30 ET, so a fix to it could not be checked
    until the next morning — and it had silently walked an empty video list
    for its whole life without anyone being able to see that. Reports the
    video count first, because zero videos is the failure that looks
    identical to zero views.
    """
    from channel_manager.analytics_tracker import AnalyticsTracker

    tracker = AnalyticsTracker()
    video_ids = tracker._list_recent_video_ids(max_results=30)
    print(f"\nVideos found: {len(video_ids)}")
    if not video_ids:
        print("  ❌ No videos listed — the pull has nothing to walk.")
        print("     Check CHANNEL_ID in .env and that the uploads playlist")
        print("     is reachable for this account.")
        return 1

    stats = tracker.run_daily_pull()
    print(f"Stats collected: {len(stats)} of {len(video_ids)}")
    if not stats:
        print("  ⚠️  Videos listed but no stats returned — YouTube lags 24–48h,")
        print("      so a channel this young may genuinely have none yet.")
        return 0

    with_views = [s for s in stats if s.views]
    print(f"With views yesterday: {len(with_views)}\n")
    for stat in sorted(stats, key=lambda s: -s.views)[:10]:
        print(f"  {stat.video_id}  {stat.views:>4} views  "
              f"{stat.watch_time_minutes:>6.1f} min  CTR {stat.ctr_pct}")
    print()
    return 0


def cmd_start_scheduler() -> int:
    from scheduler.master_scheduler import start_scheduler
    try:
        start_scheduler()
    except Exception:
        # A startup crash under systemd is otherwise invisible: the unit just
        # restarts and sits in "activating". Record the traceback where both a
        # human and `main.py --health` will find it.
        import traceback
        detail = traceback.format_exc()
        logging.getLogger("main").error("Scheduler startup FAILED:\n%s", detail)
        try:
            path = settings.logs_dir / "STARTUP_ERROR.txt"
            settings.logs_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"{datetime.now().isoformat()}\n\n{detail}", encoding="utf-8"
            )
        except Exception:
            pass
        raise
    return 0


def main() -> int:
    _print_banner()

    parser = argparse.ArgumentParser(
        description="DriftWire326 YouTube Finance Automation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # New --mode flag (primary interface)
    parser.add_argument(
        "--mode",
        choices=["full", "scrape", "build", "upload", "test", "sunday"],
        help="Pipeline mode to run",
    )
    parser.add_argument("--date", default="", help="Override date (YYYY-MM-DD)")
    parser.add_argument("--topic", default="", help="Override topic for script generation")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Scrape and generate only — no upload")

    # Legacy flags (preserved for backward compatibility)
    parser.add_argument("--run", choices=["weekday", "sunday", "monitor"],
                        help="(Legacy) Run a specific pipeline once")
    parser.add_argument("--quota", action="store_true", help="Show API quota status")
    parser.add_argument("--verify-uploads", action="store_true",
                        dest="verify_uploads",
                        help="Ask YouTube whether each uploaded video is actually public")
    parser.add_argument("--publish-private", action="store_true",
                        dest="publish_private",
                        help="Publish any uploaded video still sitting private")
    parser.add_argument("--pull-analytics", action="store_true",
                        dest="pull_analytics",
                        help="Run the nightly analytics pull now")
    parser.add_argument("--diagnose", action="store_true",
                        help="Why the videos are or are not being watched")
    parser.add_argument("--diagnose-days", type=int, default=28,
                        help="Window for --diagnose (default 28)")
    parser.add_argument("--health", action="store_true",
                        help="Full diagnosis: why is nothing publishing?")
    parser.add_argument("--test", action="store_true", help="Run test suite")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    _setup_logging(args.log_level)
    logger = logging.getLogger("main")
    logger.info("DriftWire326 pipeline starting — channel: %s", settings.channel_handle)

    # ── --mode routing ────────────────────────────────────────────────────
    if args.mode == "full":
        return cmd_run_weekday(dry_run=args.dry_run)
    if args.mode == "scrape":
        return cmd_mode_scrape(args.date)
    if args.mode == "build":
        return cmd_mode_build(args.topic)
    if args.mode == "upload":
        return cmd_mode_upload()
    if args.mode == "test":
        return cmd_test()
    if args.mode == "sunday":
        return cmd_run_sunday(dry_run=args.dry_run)

    # ── Legacy --run/--quota/--test routing ───────────────────────────────
    if args.publish_private:
        from monitor.health_report import release_private_uploads
        release_private_uploads()
        return 0

    if args.verify_uploads:
        from monitor.health_report import verify_uploads
        return 1 if verify_uploads() else 0

    if args.pull_analytics:
        return cmd_pull_analytics()

    if args.diagnose:
        from monitor.discovery_report import run as run_diagnosis
        run_diagnosis(days=args.diagnose_days)
        return 0

    if args.health:
        return cmd_health()
    if args.quota:
        return cmd_quota()
    if args.test:
        return cmd_test()
    if args.run == "weekday":
        return cmd_run_weekday(dry_run=args.dry_run)
    if args.run == "sunday":
        return cmd_run_sunday(dry_run=args.dry_run)
    if args.run == "monitor":
        return cmd_run_monitor()

    # Default: start the full scheduler
    logger.info("No specific command — starting full scheduler")
    return cmd_start_scheduler()


if __name__ == "__main__":
    sys.exit(main())
