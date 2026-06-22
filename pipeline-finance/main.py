"""
DriftWire326 — YouTube Finance Automation Pipeline
Entry point supporting manual runs, dry-runs, and scheduler launch.

Usage:
  python main.py                    # Start the full scheduler (runs forever)
  python main.py --run weekday      # Run weekday pipeline once now
  python main.py --run sunday       # Run Sunday pipeline once now
  python main.py --run monitor      # Run monitor check once
  python main.py --dry-run weekday  # Dry-run: scrape + generate only (no upload)
  python main.py --quota            # Show current API quota status
  python main.py --test             # Run test suite
"""
import argparse
import logging
import sys
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


def cmd_quota() -> int:
    from uploader.quota_tracker import QuotaTracker
    tracker = QuotaTracker()
    print(tracker.report())
    return 0


def cmd_test() -> int:
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=str(settings.root_dir),
    )
    return result.returncode


def cmd_start_scheduler() -> int:
    from scheduler.master_scheduler import start_scheduler
    start_scheduler()
    return 0


def main() -> int:
    _print_banner()

    parser = argparse.ArgumentParser(
        description="DriftWire326 YouTube Finance Automation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--run", choices=["weekday", "sunday", "monitor"],
                        help="Run a specific pipeline once")
    parser.add_argument("--dry-run", choices=["weekday", "sunday"],
                        dest="dry_run", help="Run without uploading")
    parser.add_argument("--quota", action="store_true", help="Show API quota status")
    parser.add_argument("--test", action="store_true", help="Run test suite")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    _setup_logging(args.log_level)
    logger = logging.getLogger("main")
    logger.info("DriftWire326 pipeline starting — channel: %s", settings.channel_handle)

    if args.quota:
        return cmd_quota()
    if args.test:
        return cmd_test()
    if args.dry_run == "weekday":
        return cmd_run_weekday(dry_run=True)
    if args.dry_run == "sunday":
        return cmd_run_sunday(dry_run=True)
    if args.run == "weekday":
        return cmd_run_weekday()
    if args.run == "sunday":
        return cmd_run_sunday()
    if args.run == "monitor":
        return cmd_run_monitor()

    # Default: start the full scheduler
    logger.info("No specific command — starting full scheduler")
    return cmd_start_scheduler()


if __name__ == "__main__":
    sys.exit(main())
