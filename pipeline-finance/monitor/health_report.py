"""
monitor/health_report.py — DriftWire326 one-command diagnosis
`python main.py --health` answers the only question that matters when the
channel goes quiet: WHY is nothing being published?

Checks, in the order failures actually happen:
  1. Anthropic credit / API reachable   (a dead key stops every run)
  2. Disk space                          (a full disk fails every render)
  3. The scheduler daemon                (is it even running?)
  4. Today's + recent pipeline states    (where did runs stop?)
  5. Upload manifest                     (what actually published, and when?)
  6. YouTube quota                       (exhausted?)
  7. Alerting itself                     (is SMTP configured? if not, silence
                                          means nothing — the #1 blind spot)
Prints a verdict and the single most likely cause.
"""
import json
import logging
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_OK, _WARN, _FAIL = "✅", "⚠️ ", "❌"


def _line(status: str, label: str, detail: str = "") -> str:
    return f"{status} {label}" + (f" — {detail}" if detail else "")


def _check_anthropic() -> tuple[str, str, str]:
    """Returns (status, label, detail). A dead key halts every pipeline run."""
    if not settings.anthropic_api_key:
        return _FAIL, "Anthropic API key", "not set in .env"
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        # A 1-token completion is the only way to prove credit actually remains;
        # models.list() succeeds even on an exhausted account.
        client.messages.create(
            model=settings.claude_model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return _OK, "Anthropic API", "key valid, credit available"
    except Exception as exc:
        text = str(exc)
        if "credit" in text.lower() or "balance" in text.lower() or "402" in text:
            return _FAIL, "Anthropic API", "OUT OF CREDIT — add funds at console.anthropic.com"
        if "authentication" in text.lower() or "401" in text:
            return _FAIL, "Anthropic API", "key rejected (invalid or revoked)"
        return _FAIL, "Anthropic API", text[:120]


def _check_disk() -> tuple[str, str, str]:
    try:
        usage = shutil.disk_usage(str(settings.root_dir))
        free_gb = usage.free / 1e9
        pct_used = usage.used / usage.total * 100
        detail = f"{free_gb:.1f} GB free ({pct_used:.0f}% used)"
        if free_gb < 1.0:
            return _FAIL, "Disk space", detail + " — renders WILL fail"
        if free_gb < 3.0:
            return _WARN, "Disk space", detail + " — clean output/ soon"
        return _OK, "Disk space", detail
    except Exception as exc:
        return _WARN, "Disk space", str(exc)[:80]


def _check_daemon() -> tuple[str, str, str]:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "driftwire326"],
            capture_output=True, text=True, timeout=15,
        )
        state = result.stdout.strip() or result.stderr.strip() or "not installed"
        if state == "active":
            return _OK, "Scheduler daemon", "active (running)"
        return _FAIL, "Scheduler daemon", f"{state} — run: sudo systemctl start driftwire326"
    except FileNotFoundError:
        return _WARN, "Scheduler daemon", "systemctl unavailable (not a systemd host)"
    except Exception as exc:
        return _WARN, "Scheduler daemon", str(exc)[:80]


def _check_heartbeat() -> tuple[str, str, str]:
    """The daemon writes a heartbeat every 30 min — proves it is alive AND working."""
    hb = settings.logs_dir / "heartbeat.log"
    if not hb.exists():
        return _FAIL, "Daemon heartbeat", "never written — daemon has not run its jobs"
    try:
        age_min = (datetime.now().timestamp() - hb.stat().st_mtime) / 60
        if age_min > 90:
            return _FAIL, "Daemon heartbeat", f"stale — last beat {age_min/60:.1f} h ago"
        return _OK, "Daemon heartbeat", f"last beat {age_min:.0f} min ago"
    except Exception as exc:
        return _WARN, "Daemon heartbeat", str(exc)[:80]


def _check_startup_error() -> tuple[str, str, str, list[str]]:
    """
    A scheduler startup crash writes its traceback here. Surfacing it turns a
    silent systemd restart loop into a named cause.
    """
    path = settings.logs_dir / "STARTUP_ERROR.txt"
    if not path.exists():
        return _OK, "Scheduler startup", "no recorded startup crash", []
    try:
        text = path.read_text(encoding="utf-8").strip()
        stamp = text.splitlines()[0] if text else "?"
        # The final traceback line is the exception itself — the useful part.
        last = [l for l in text.splitlines() if l.strip()][-1]
        age_note = ""
        try:
            age_h = (datetime.now() - datetime.fromisoformat(stamp)).total_seconds() / 3600
            age_note = f" ({age_h:.1f} h ago)"
            if age_h > 24:
                # Stale file from an already-fixed crash — inform, don't alarm.
                return (_WARN, "Scheduler startup",
                        f"old crash recorded{age_note} — delete logs/STARTUP_ERROR.txt "
                        f"once resolved", [f"   {last[:150]}"])
        except Exception:
            pass
        return (_FAIL, "Scheduler startup", f"crashed at boot{age_note}",
                [f"   {last[:150]}", f"   full traceback: {path}"])
    except Exception as exc:
        return _WARN, "Scheduler startup", str(exc)[:80], []


def _check_claude_burn(days: int = 7) -> tuple[str, str, str, list[str]]:
    """
    How fast credit is draining, and whether that changed.

    The credit check above only answers "is there any left". Running out
    stops the channel exactly as completely as a dead daemon, and just as
    quietly — so the useful question is the rate, not the balance.
    """
    try:
        from monitor.usage_ledger import burn_summary, estimated_cost, BURN_ALERT_RATIO
        summary = burn_summary(days)
    except Exception as exc:
        return _WARN, "Claude burn rate", f"usage ledger unreadable: {exc}", []

    if not summary["calls"]:
        return (_OK, "Claude burn rate",
                f"no Claude calls recorded in {days} days "
                "(ledger starts at the next pipeline run)", [])

    headline = (f"{summary['tokens']:,} tokens over {days}d "
                f"(~{summary['tokens_per_day']:,}/day, {summary['calls']} calls)")
    cost = estimated_cost(summary)
    if cost is not None:
        headline += f" ≈ ${cost}"

    lines = [f"   {source}: {tokens:,} tokens"
             for source, tokens in list(summary["by_source"].items())[:5]]

    ratio = summary["ratio"]
    if ratio and ratio >= BURN_ALERT_RATIO:
        return (_WARN, "Claude burn rate",
                f"{headline} — {ratio}x the previous {days} days", lines)
    if ratio:
        lines.append(f"   vs previous {days}d: {ratio}x")
    return _OK, "Claude burn rate", headline, lines


def _check_optional_keys() -> tuple[str, str, str, list[str]]:
    """
    Optional API keys that features quietly rely on.

    Credentials that are *required* already fail loudly. These do not: with
    no key the feature simply disappears, the run still succeeds, and
    nothing says the video came out thinner than intended.
    """
    optional = [
        ("PEXELS_API_KEY", "B-roll photos",
         "visual sequence loses its ~3 photo slides"),
        ("MARKETSTACK_API_KEY", "backup EOD prices",
         "no fallback if yfinance is down on a publishing day"),
    ]
    missing = [(name, feature, effect) for name, feature, effect in optional
               if not (os.getenv(name) or "").strip()]
    if not missing:
        return _OK, "Optional API keys", f"all {len(optional)} present", []
    return (_WARN, "Optional API keys",
            f"{len(missing)} unset — features silently disabled",
            [f"   {name} missing → {feature}: {effect}"
             for name, feature, effect in missing])


# What YouTube's own guidance and the working benchmark for finance
# explainers put a healthy channel at. Used only to label numbers, never to
# gate anything — a young channel is below these and that is normal.
GOOD_CTR = 0.04            # 4% impressions-to-click
GOOD_RETENTION_SECONDS = 90


def _latest_weekly_report() -> "dict | None":
    import json as _json
    directory = settings.logs_dir / "analytics"
    if not directory.exists():
        return None
    reports = sorted(directory.glob("weekly_report_*.json"))
    if not reports:
        return None
    try:
        return _json.loads(reports[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def _check_performance() -> tuple[str, str, str, list[str]]:
    """
    The numbers that decide what to publish more of.

    Analytics were pulled daily and never aggregated, so nothing in this
    report ever said whether anyone was watching. Every other check here
    answers "is the machine running"; this is the only one that answers
    "is it working".
    """
    report = _latest_weekly_report()
    if report is None:
        return (_WARN, "Performance",
                "no weekly report yet — the first runs Monday 07:00 ET", [])

    views = report.get("total_views", 0)
    ctr = report.get("avg_ctr", 0.0) or 0.0
    watch_minutes = report.get("total_watch_time_minutes", 0.0) or 0.0
    videos = report.get("video_count", 0)
    gained = report.get("subscribers_gained", 0)

    detail = (f"week of {report.get('week_of', '?')}: {views:,} views, "
              f"{ctr * 100:.1f}% CTR, {watch_minutes:,.0f} watch-minutes, "
              f"{gained:+d} subs across {videos} videos")

    lines: list[str] = []
    if videos:
        lines.append(f"   {views / videos:,.0f} views per video")
    if views:
        lines.append(f"   {watch_minutes * 60 / views:,.0f}s average view duration")
    if report.get("top_video_id"):
        lines.append(f"   best: {report['top_video_id']} "
                     f"({report.get('top_video_views', 0):,} views)")
    weak = report.get("low_ctr_videos") or []
    if weak:
        lines.append(f"   {len(weak)} video(s) below the CTR floor — "
                     "candidates for a title swap")

    # A channel with no views is not broken, it is new. Say which it is
    # rather than colouring it red.
    if not views:
        return (_WARN, "Performance",
                f"{detail} — nothing is being watched yet", lines)
    if ctr < GOOD_CTR:
        return (_WARN, "Performance",
                f"{detail} — CTR below {GOOD_CTR * 100:.0f}%, "
                "titles and thumbnails are the lever", lines)
    return _OK, "Performance", detail, lines


def _check_format_performance(days: int = 60) -> tuple[str, str, str, list[str]]:
    """
    Views per video by format, which is the question the publishing mix
    turns on.

    Daily recaps expire in a day and compete with every finance channel
    there is; explainers accumulate search traffic for years. Whether to
    keep two recap slots or move that time to explainers is a real
    decision, and it should be made on these numbers rather than on
    anybody's argument. Joins the daily analytics to the upload manifest,
    which is the only place a video's format is recorded.
    """
    import json as _json
    from collections import defaultdict

    try:
        from uploader.uploader import load_upload_manifest
        manifest = load_upload_manifest() or []
    except Exception as exc:
        return _WARN, "Format performance", f"manifest unreadable: {exc}", []

    kind_of = {r.get("video_id"): (r.get("video_type") or "unknown")
               for r in manifest if r.get("video_id")}
    if not kind_of:
        return _OK, "Format performance", "no uploads recorded yet", []

    directory = settings.logs_dir / "analytics"
    if not directory.exists():
        return (_WARN, "Format performance",
                "no analytics pulled yet — the daily job populates this", [])

    # Latest observation per video: the daily pull writes one file per day
    # and a video appears in several of them.
    latest: dict[str, dict] = {}
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("weekly_report_") or path.stem < cutoff:
            continue
        try:
            for entry in _json.loads(path.read_text(encoding="utf-8")):
                if entry.get("video_id"):
                    latest[entry["video_id"]] = entry
        except Exception:
            continue

    if not latest:
        return (_WARN, "Format performance",
                f"no analytics in the last {days} days", [])

    grouped: dict[str, list[dict]] = defaultdict(list)
    for video_id, entry in latest.items():
        grouped[kind_of.get(video_id, "unknown")].append(entry)

    lines: list[str] = []
    for kind, entries in sorted(grouped.items(),
                                key=lambda kv: -_mean(kv[1], "views")):
        views = _mean(entries, "views")
        duration = _mean(entries, "avg_view_duration_seconds")
        ctr = _mean(entries, "ctr")
        lines.append(
            f"   {kind:<10} {len(entries):>3} videos | {views:>7,.0f} views avg "
            f"| {duration:>5.0f}s watched | {ctr * 100:4.1f}% CTR")

    return (_OK, "Format performance",
            f"{len(latest)} videos across {len(grouped)} format(s), "
            f"last {days} days", lines)


def _mean(entries: list, field: str) -> float:
    values = [float(e.get(field, 0) or 0) for e in entries]
    return sum(values) / len(values) if values else 0.0


def _check_learning() -> tuple[str, str, str, list[str]]:
    """
    What the channel has learned about its own hooks and styles.

    These were picked at random for the life of the project while a scoring
    system sat wired to nothing. Showing the scores here is also the only
    way to notice if the loop quietly stops feeding.
    """
    try:
        from channel_manager.performance_tracker import (
            PerformanceTracker, MIN_OBSERVATIONS)
        tracker = PerformanceTracker()
        styles = tracker.get_scores("styles")
        hooks = tracker.get_scores("hooks")
    except Exception as exc:
        return _WARN, "Learning", f"performance scores unreadable: {exc}", []

    if not styles and not hooks:
        return (_OK, "Learning",
                "no scores yet — selection is still random, which is correct "
                "until videos have results to compare", [])

    lines: list[str] = []
    for label, rows in (("style", styles), ("hook", hooks)):
        if not rows:
            continue
        best = rows[0]
        confident = best["observations"] >= MIN_OBSERVATIONS
        lines.append(
            f"   best {label}: {best['label'][:44]} "
            f"({best['score']:.2f} over {best['observations']} obs"
            f"{'' if confident else ', still provisional'})")
    total = sum(r["observations"] for r in styles + hooks)
    return (_OK, "Learning",
            f"{len(styles)} styles and {len(hooks)} hooks scored "
            f"from {total} observations", lines)


# Which upload type each content slot is supposed to produce, and how long
# to allow for the build before calling it missing. A long-form video takes
# a few minutes to render on this instance; ninety minutes is generous.
SLOT_PRODUCES = {
    "weekday_premarket": "weekday",
    "weekday_postmarket": "weekday",
    "midday_short": "shorts",
    "saturday_short": "shorts",
    "sunday_educational": "sunday",
}
SLOT_GRACE_MINUTES = 90


def _check_todays_slots() -> tuple[str, str, str, list[str]]:
    """
    Did every slot that fired today actually publish something?

    The Shorts pipeline keeps no checkpoint, so a Short that starts and dies
    leaves no trace at all — not in the pipeline runs, not in the manifest,
    nowhere. Nothing else in this report would notice. This compares what
    the schedule says should have run against what actually reached the
    manifest.
    """
    try:
        from apscheduler.triggers.cron import CronTrigger
        from scheduler.master_scheduler import CONTENT_SLOTS, CONTENT_SLOT_NAMES
        from uploader.uploader import load_upload_manifest
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(settings.timezone)
        except Exception:
            import pytz
            tz = pytz.timezone(settings.timezone)
    except Exception as exc:
        return _WARN, "Today's slots", f"schedule unavailable: {exc}", []

    now = datetime.now(tz)
    today = now.date().isoformat()

    published: set[str] = set()
    try:
        for record in load_upload_manifest() or []:
            if (record.get("uploaded_at") or "")[:10] == today:
                published.add(record.get("video_type") or "")
    except Exception as exc:
        return _WARN, "Today's slots", f"manifest unreadable: {exc}", []

    due: list[tuple[str, datetime]] = []
    for slot, cron in CONTENT_SLOTS.items():
        try:
            trigger = CronTrigger(timezone=tz, **cron)
            # Walk back from now to find today's fire time, if there was one.
            previous = trigger.get_next_fire_time(
                None, now.replace(hour=0, minute=0, second=0, microsecond=0))
        except Exception:
            continue
        if previous and previous.date() == now.date() and previous <= now:
            elapsed = (now - previous).total_seconds() / 60
            if elapsed >= SLOT_GRACE_MINUTES:
                due.append((slot, previous))

    if not due:
        return _OK, "Today's slots", "none due yet today", []

    missing = [(slot, fired) for slot, fired in due
               if SLOT_PRODUCES.get(slot) not in published]
    lines = [f"   {CONTENT_SLOT_NAMES.get(slot, slot)} fired {fired:%H:%M %Z} "
             f"— no {SLOT_PRODUCES.get(slot, '?')} upload recorded today"
             for slot, fired in missing]
    if missing:
        return (_FAIL, "Today's slots",
                f"{len(missing)} of {len(due)} slot(s) due today produced nothing",
                lines)
    return (_OK, "Today's slots",
            f"all {len(due)} slot(s) due today published", [])


def release_private_uploads(limit: int = 20) -> int:
    """
    Publish everything sitting private. Returns how many were released.

    Videos scheduled for the wrong time cannot free themselves — they wait
    until whenever they were told to appear, which for the Sunday videos
    was a week out. Skips anything YouTube no longer has.
    """
    try:
        from uploader.uploader import load_upload_manifest, YouTubeUploader
        from uploader.quota_tracker import QuotaTracker
    except Exception as exc:
        print(f"{_FAIL} Cannot load the uploader: {exc}")
        return 0

    uploader = YouTubeUploader(QuotaTracker())
    released = 0
    recent = list(reversed(load_upload_manifest() or []))[:limit]
    for record in recent:
        video_id = record.get("video_id")
        if not video_id:
            continue
        status = uploader.verify_upload_status(video_id)
        if status.get("privacy") != "private":
            continue
        title = (record.get("title") or "")[:44]
        if uploader.publish_now(video_id):
            released += 1
            print(f"{_OK} published  {video_id}  {title}")
            print(f"     https://youtu.be/{video_id}")
        else:
            print(f"{_FAIL} failed     {video_id}  {title}")
    print()
    print(f"Released {released} video(s)." if released
          else "Nothing was sitting private.")
    return released


def verify_uploads(limit: int = 20) -> int:
    """
    Ask YouTube what actually happened to each video we recorded uploading.

    The manifest only records that an upload call succeeded. It says nothing
    about whether the video went public, is still processing, was rejected,
    or is sitting private forever — which is the difference between "we
    published four videos" and "the channel is empty". verify_upload_status
    has existed since the beginning and was never called by anything.

    Returns the number of videos that are not publicly visible.
    """
    try:
        from uploader.uploader import load_upload_manifest, YouTubeUploader
        from uploader.quota_tracker import QuotaTracker
    except Exception as exc:
        print(f"{_FAIL} Cannot load the uploader: {exc}")
        return 0

    records = list(reversed(load_upload_manifest() or []))[:limit]
    if not records:
        print("No uploads recorded in the manifest.")
        return 0

    print("═" * 66)
    print(f"  UPLOAD VERIFICATION — {len(records)} most recent")
    print("═" * 66)
    print()

    uploader = YouTubeUploader(QuotaTracker())
    hidden = 0
    for record in records:
        video_id = record.get("video_id")
        if not video_id:
            continue
        status = uploader.verify_upload_status(video_id)
        privacy = status.get("privacy")
        mark = _OK if privacy == "public" else _FAIL
        if privacy != "public":
            hidden += 1
        title = (record.get("title") or "")[:44]
        print(f"{mark} {video_id}  {privacy or status.get('status', '?'):<10} "
              f"{status.get('processing_status') or status.get('upload_status') or '':<12} {title}")
        print(f"     https://youtu.be/{video_id}   uploaded {record.get('uploaded_at', '?')[:16]}")

    print()
    if hidden:
        print(f"{_FAIL} {hidden} of {len(records)} video(s) are NOT publicly visible.")
        print("     'private' with no scheduled publish time means it will stay")
        print("     that way — nobody can see it and it earns nothing.")
    else:
        print(f"{_OK} All {len(records)} video(s) are public.")
    print()
    return hidden


# How many recent uploads to confirm against YouTube. videos.list costs one
# quota unit each, so five is free in practice against a 10,000/day budget.
VISIBILITY_CHECK_COUNT = 5


def _check_video_visibility() -> tuple[str, str, str, list[str]]:
    """
    Are the videos we uploaded actually visible to anyone?

    This report said "healthy — publishing normally" for days while four of
    six videos sat private, because every check upstream of this one asks
    whether the machine ran, not whether anything reached an audience. An
    upload that succeeds and never becomes visible is indistinguishable
    from no upload at all, and it was the difference between a working
    channel and an empty one.
    """
    try:
        from uploader.uploader import load_upload_manifest, YouTubeUploader
        from uploader.quota_tracker import QuotaTracker
        records = list(reversed(load_upload_manifest() or []))[:VISIBILITY_CHECK_COUNT]
    except Exception as exc:
        return _WARN, "Video visibility", f"manifest unreadable: {exc}", []

    if not records:
        return _OK, "Video visibility", "no uploads recorded yet", []

    try:
        uploader = YouTubeUploader(QuotaTracker())
    except Exception as exc:
        return _WARN, "Video visibility", f"YouTube unavailable: {exc}", []

    hidden, missing, checked = [], [], 0
    for record in records:
        video_id = record.get("video_id")
        if not video_id:
            continue
        status = uploader.verify_upload_status(video_id)
        privacy = status.get("privacy")
        if privacy is None and status.get("status") == "error":
            return (_WARN, "Video visibility",
                    f"could not reach YouTube: {status.get('error', '')[:80]}", [])
        checked += 1
        title = (record.get("title") or "")[:40]
        if status.get("status") == "not_found":
            missing.append(f"   {video_id}  gone from YouTube — {title}")
        elif privacy != "public":
            hidden.append(f"   {video_id}  {privacy} — {title}")

    if hidden:
        return (_FAIL, "Video visibility",
                f"{len(hidden)} of {checked} recent video(s) are NOT public — "
                "nobody can see them",
                hidden + missing + [
                    "   fix: main.py --publish-private"])
    if missing:
        return (_WARN, "Video visibility",
                f"{len(missing)} of {checked} recent video(s) no longer exist "
                "on YouTube", missing)
    return _OK, "Video visibility", f"all {checked} recent video(s) are public", []


def _check_backups(stale_days: int = 10) -> tuple[str, str, str, list[str]]:
    """
    Is there a recent copy of the unrecoverable state, and is it anywhere
    other than the machine it protects?

    An archive sitting beside the thing it backs up survives a bad deploy
    but not a lost instance, which is the failure it exists for.
    """
    from datetime import datetime as _dt
    try:
        from scheduler.backup import backup_dir, REMOTE_CMD_ENV
    except Exception as exc:
        return _WARN, "State backup", f"backup module unavailable: {exc}", []

    archives = sorted(backup_dir().glob("driftwire326-state-*.tar.gz"))
    remote = (os.getenv(REMOTE_CMD_ENV) or "").strip()

    if not archives:
        return (_WARN, "State backup",
                "no archive yet — the first runs Sunday 06:00 ET", [])

    newest = archives[-1]
    age_days = (_dt.now() - _dt.fromtimestamp(newest.stat().st_mtime)).days
    size_kb = newest.stat().st_size / 1024
    detail = f"{len(archives)} archive(s), newest {age_days}d old ({size_kb:.0f} KB)"

    if age_days > stale_days:
        return (_FAIL, "State backup",
                f"{detail} — the weekly job has not run", [])
    if not remote:
        return (_WARN, "State backup",
                f"{detail}, but {REMOTE_CMD_ENV} is unset — the archive only "
                "exists on the instance it protects",
                [f"   set e.g. {REMOTE_CMD_ENV}=\"gsutil cp {{archive}} gs://bucket/\""])
    return _OK, "State backup", f"{detail}, shipped off-instance", []


def _check_pipeline_states(days: int = 5) -> tuple[str, str, str, list[str]]:
    """Read the last N days of checkpoint files: where did runs stop?"""
    from scheduler.pipeline_state import STATE_DIR
    detail_lines: list[str] = []
    unfinished: list[str] = []
    if not STATE_DIR.exists():
        return _FAIL, "Pipeline runs", "no state dir — no pipeline has ever started", []

    found_any = False
    for offset in range(days):
        day = (date.today() - timedelta(days=offset)).isoformat()
        # Glob rather than build the filename: the weekday pipeline writes one
        # checkpoint per slot now (weekday_premarket_<day>.json). Constructing
        # "weekday_<day>.json" would find nothing and report "no runs" — the
        # exact false all-clear this report exists to prevent.
        for path in sorted(STATE_DIR.glob(f"*_{day}.json")):
            pipeline = path.stem[:-len(day) - 1]
            found_any = True
            try:
                data = json.loads(path.read_text())
                outcome = data.get("outcome") or "incomplete"
                steps = [s for s, e in data.get("steps", {}).items() if e.get("done")]
                errors = data.get("errors", [])
                mark = "✅" if outcome == "success" else "❌"
                line = f"   {mark} {day} {pipeline}: {outcome}, {len(steps)} steps done"
                if errors:
                    line += f"\n      last error: {errors[-1][:150]}"
                detail_lines.append(line)
                # A run that failed, or one left incomplete on a day that is
                # over, is a run that did not publish. Today's incomplete run
                # may simply still be going.
                if outcome == "failed" or (outcome == "incomplete" and offset > 0):
                    unfinished.append(f"{day} {pipeline}")
            except Exception as exc:
                detail_lines.append(f"   ⚠️  {day} {pipeline}: unreadable ({exc})")
                unfinished.append(f"{day} {pipeline} (unreadable)")

    if not found_any:
        # A daemon that only just started has had no chance to fire yet —
        # that is "waiting", not "broken".
        if _daemon_uptime_hours() is not None and _daemon_uptime_hours() < 12:
            return (_WARN, "Pipeline runs",
                    "none yet — daemon started recently, waiting for the next slot", [])
        return (_FAIL, "Pipeline runs",
                f"NO runs attempted in {days} days — the scheduler never fired", [])
    if unfinished:
        # Never report OK over a failed run. Two weekday slots means one can
        # publish while the other does not, and a green headline above a red
        # line is how a partial outage goes unnoticed for weeks.
        return (_WARN, "Pipeline runs",
                f"{len(detail_lines)} run(s) recorded, "
                f"{len(unfinished)} did not publish: {', '.join(unfinished[:3])}",
                detail_lines)
    return _OK, "Pipeline runs", f"{len(detail_lines)} run(s) recorded", detail_lines


def _daemon_uptime_hours() -> "float | None":
    """
    Hours since the CURRENT scheduler process started.

    heartbeat.log is append-only across restarts, so the newest line is only
    ever minutes old. Taking that as "uptime" made the freshly-restarted
    grace period permanent, which would suppress the very outage warning this
    report exists to raise. Each beat records its PID, so the run of trailing
    lines sharing the newest PID belongs to the live process — its first beat
    is the real start time.
    """
    hb = settings.logs_dir / "heartbeat.log"
    if not hb.exists():
        return None
    try:
        beats: list[tuple[datetime, str]] = []
        for line in hb.read_text(encoding="utf-8").splitlines():
            if "] " not in line or "PID=" not in line:
                continue
            stamp = line.split("] ")[1].split(" |")[0].strip()
            pid = line.split("PID=")[1].split(" |")[0].strip()
            beats.append((datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S"), pid))
        if not beats:
            return None

        current_pid = beats[-1][1]
        started = beats[-1][0]
        for stamp, pid in reversed(beats):
            if pid != current_pid:
                break
            started = stamp
        return max((datetime.now() - started).total_seconds() / 3600, 0.0)
    except Exception:
        return None


def _next_content_slots() -> list[str]:
    """Human-readable 'when is the next video due' lines."""
    try:
        from scheduler.master_scheduler import next_content_runs
        # Five, not three: the weekday post-market slot is the fourth entry on
        # a Saturday, and a slot you cannot see is a slot you cannot confirm.
        return [
            f"   {label}: {fire.strftime('%a %b %d, %-I:%M %p %Z')}"
            for label, fire in next_content_runs(limit=5)
        ]
    except Exception:
        return []


def _read_registered_jobs() -> "dict | None":
    """The job table the running scheduler dumped when it started."""
    from scheduler.master_scheduler import REGISTERED_JOBS_FILE
    path = settings.logs_dir / REGISTERED_JOBS_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _check_registered_jobs() -> tuple[str, str, str, list[str]]:
    """
    Compare the jobs the daemon actually registered against the content slots
    it is supposed to run.

    Everything else about "next scheduled content" is derived from
    CONTENT_SLOTS — the same config the scheduler was built from — so it
    prints an identical list whether the daemon holds those jobs or none at
    all. This is the one check that reads what the process actually did.
    """
    from scheduler.master_scheduler import CONTENT_SLOTS, CONTENT_SLOT_NAMES

    data = _read_registered_jobs()
    if data is None:
        return (_WARN, "Registered jobs",
                "daemon has not reported a job table — running an older build, "
                "or it has not started since this check was added", [])

    live_pid = _current_daemon_pid()
    if live_pid and str(data.get("pid")) != str(live_pid):
        return (_WARN, "Registered jobs",
                f"job table was written by PID {data.get('pid')} but the live "
                f"daemon is PID {live_pid} — the table is from an earlier run", [])

    registered = {job.get("id"): job for job in data.get("jobs", [])}
    missing = [slot for slot in CONTENT_SLOTS if slot not in registered]
    if missing:
        names = ", ".join(CONTENT_SLOT_NAMES.get(slot, slot) for slot in missing)
        return (_FAIL, "Registered jobs",
                f"the daemon is NOT running {len(missing)} content slot(s): {names}",
                [])

    lines = [
        f"   {job['id']}: next {job.get('next_run') or 'unscheduled'}"
        + (f"  args={job['args']}" if job.get("args") else "")
        for slot, job in ((s, registered[s]) for s in CONTENT_SLOTS)
    ]
    return (_OK, "Registered jobs",
            f"{len(registered)} live in the daemon, all {len(CONTENT_SLOTS)} "
            "content slots covered", lines)


def _current_daemon_pid() -> "str | None":
    """PID of the process writing heartbeats right now."""
    hb = settings.logs_dir / "heartbeat.log"
    if not hb.exists():
        return None
    try:
        for line in reversed(hb.read_text(encoding="utf-8").splitlines()):
            if "PID=" in line:
                return line.split("PID=")[1].split(" |")[0].strip()
    except Exception:
        return None
    return None


def _check_uploads() -> tuple[str, str, str, list[str]]:
    try:
        from uploader.uploader import load_upload_manifest
        records = load_upload_manifest()
    except Exception as exc:
        return _WARN, "Uploads", f"manifest unreadable: {exc}", []

    if not records:
        return _FAIL, "Uploads", "manifest empty — nothing has ever published", []

    newest = records[0].get("uploaded_at", "")
    try:
        age_days = (datetime.now() - datetime.fromisoformat(newest)).days
    except Exception:
        age_days = -1

    lines = [
        f"   {r.get('uploaded_at', '?')[:16]}  {r.get('video_type', '?'):8}  "
        f"{str(r.get('title', ''))[:52]}"
        for r in records[:5]
    ]
    if age_days > 1:
        uptime = _daemon_uptime_hours()
        if uptime is not None and uptime < 12:
            return (_WARN, "Uploads",
                    f"last upload {age_days} days ago — daemon restarted "
                    f"{uptime:.1f} h ago, nothing due yet", lines)
        return (_FAIL, "Uploads",
                f"last upload was {age_days} days ago ({newest[:16]})", lines)
    return _OK, "Uploads", f"{len(records)} total, newest {newest[:16]}", lines


def _check_quota() -> tuple[str, str, str]:
    try:
        from uploader.quota_tracker import QuotaTracker
        qt = QuotaTracker()
        remaining = qt.get_remaining()
        if not qt.can_upload():
            return _FAIL, "YouTube quota", f"{remaining} units left — below upload threshold"
        return _OK, "YouTube quota", f"{remaining:,} units remaining today"
    except Exception as exc:
        return _WARN, "YouTube quota", str(exc)[:80]


def _check_alerting() -> tuple[str, str, str]:
    """
    THE blind spot: if SMTP is unset, the dead-man switch fires into a log
    and you learn about outages days later.
    """
    required = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        return (_FAIL, "Alerting (dead-man email)",
                f"NOT configured (missing {', '.join(missing)}) — outages stay silent")
    if not os.environ.get("ALERT_EMAIL", os.environ.get("SMTP_USER", "")):
        return _WARN, "Alerting (dead-man email)", "ALERT_EMAIL unset"
    return _OK, "Alerting (dead-man email)", f"to {os.environ.get('ALERT_EMAIL')}"


def _check_tokens() -> tuple[str, str, str]:
    cfg = settings.root_dir / "config"
    missing = [
        name for name in ("finance_oauth.json", "youtube_token.json", "analytics_token.json")
        if not (cfg / name).exists()
    ]
    if missing:
        return _FAIL, "YouTube credentials", f"missing {', '.join(missing)}"
    return _OK, "YouTube credentials", "all 3 files present"


def run_health_report() -> int:
    """Print the full report. Returns 0 if healthy, 1 if any FAIL."""
    print()
    print("═" * 66)
    print(f"  DriftWire326 HEALTH REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 66)
    print()

    failures: list[str] = []
    simple_checks = [
        _check_anthropic(), _check_disk(), _check_daemon(),
        _check_heartbeat(), _check_quota(), _check_tokens(), _check_alerting(),
    ]
    for status, label, detail in simple_checks:
        print(_line(status, label, detail))
        if status == _FAIL:
            failures.append(label)

    print()
    for status, label, detail, lines in (
        _check_startup_error(), _check_registered_jobs(),
        _check_pipeline_states(), _check_uploads(),
        _check_claude_burn(), _check_optional_keys(), _check_backups(),
        _check_performance(), _check_format_performance(), _check_learning(),
        _check_todays_slots(), _check_video_visibility()
    ):
        print(_line(status, label, detail))
        for line in lines:
            print(line)
        if status == _FAIL:
            failures.append(label)
        print()

    upcoming = _next_content_slots()
    if upcoming:
        print("Next scheduled content:")
        for line in upcoming:
            print(line)
        print()

    print("═" * 66)
    if not failures:
        print("  VERDICT: healthy — publishing normally.")
    else:
        print(f"  VERDICT: {len(failures)} problem(s): {', '.join(failures)}")
        print()
        # Order matters — name the upstream cause, not the symptom
        if "Scheduler startup" in failures:
            print("  ROOT CAUSE: the scheduler crashed during startup — see the")
            print("  traceback above and in logs/STARTUP_ERROR.txt. systemd keeps")
            print("  restarting it, which shows as 'activating', not 'failed'.")
        elif "Anthropic API" in failures or "Anthropic API key" in failures:
            print("  ROOT CAUSE: no Claude access — every run aborts at the API")
            print("  gate before spending anything. Top up credit, then:")
            print("    sudo systemctl restart driftwire326")
        elif "Disk space" in failures:
            print("  ROOT CAUSE: disk full — renders cannot write. Free space:")
            print("    sudo find /opt/driftwire326/pipeline-finance/output -type f "
                  "-mtime +3 -delete")
        elif "Scheduler daemon" in failures or "Daemon heartbeat" in failures:
            print("  ROOT CAUSE: the scheduler is not running its jobs. Start it and")
            print("  read why it stopped:")
            print("    sudo systemctl start driftwire326")
            print("    sudo journalctl -u driftwire326 -n 60 --no-pager")
        elif "Pipeline runs" in failures:
            print("  ROOT CAUSE: the daemon is up but no pipeline fired — check the")
            print("  job schedule and the service log:")
            print("    sudo journalctl -u driftwire326 -n 60 --no-pager")
        elif "YouTube quota" in failures:
            print("  ROOT CAUSE: today's API quota is spent; it resets at midnight PT.")
        elif "YouTube credentials" in failures:
            print("  ROOT CAUSE: OAuth files missing — re-run deploy/oauth_bootstrap.py")
        if "Alerting (dead-man email)" in failures:
            print()
            print("  ALSO: alerting is off, which is why nobody told you. Add SMTP_HOST,")
            print("  SMTP_USER, SMTP_PASS (Gmail app password) and ALERT_EMAIL to .env.")
    print("═" * 66)
    print()
    return 1 if failures else 0
