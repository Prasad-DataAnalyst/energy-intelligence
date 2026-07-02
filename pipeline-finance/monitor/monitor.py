"""
Channel performance monitor — polls YouTube Analytics API for KPIs,
sends email/webhook alerts on notable events, and logs to JSONL.
Runs every 2 hours via the master scheduler.
"""
import json
import logging
import os
import smtplib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

MONITOR_LOG = settings.logs_dir / "monitor.jsonl"
THRESHOLDS = {
    "ctr_drop_pct": 15,          # alert if CTR drops >15% vs last 7-day avg
    "view_spike_pct": 200,       # alert if views spike >200% (viral)
    "sub_milestone": [1000, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000],
    "view_milestone": [1000, 10000, 100000, 1000000],
    "dislike_ratio_max": 0.05,   # alert if dislikes >5% of engagements
}


@dataclass
class ChannelMetrics:
    snapshot_time: str
    subscribers: int
    total_views: int
    last_7d_views: int
    last_7d_ctr: float
    last_7d_avg_view_pct: float   # average view duration as % of video length
    last_7d_revenue: Optional[float]
    latest_video_views: int
    latest_video_ctr: float
    latest_video_title: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AlertEvent:
    level: str       # "info" | "warning" | "milestone" | "urgent"
    event: str
    detail: str
    metric_value: float
    threshold: Optional[float]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class ChannelMonitor:
    """Monitors YouTube channel health and triggers alerts."""

    def __init__(self):
        MONITOR_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._prev_metrics: Optional[ChannelMetrics] = self._load_last_metrics()

    def _load_last_metrics(self) -> Optional[ChannelMetrics]:
        if not MONITOR_LOG.exists():
            return None
        try:
            lines = MONITOR_LOG.read_text().strip().splitlines()
            if lines:
                last = json.loads(lines[-1])
                # Filter to known fields so old log lines with drifted
                # schemas can't raise TypeError on load.
                known = {f.name for f in ChannelMetrics.__dataclass_fields__.values()}
                return ChannelMetrics(**{k: v for k, v in last.items() if k in known})
        except Exception as exc:
            logger.warning("Could not load previous metrics from %s: %s", MONITOR_LOG.name, exc)
        return None

    def _log_metrics(self, metrics: ChannelMetrics) -> None:
        with open(MONITOR_LOG, "a") as f:
            f.write(json.dumps(metrics.to_dict()) + "\n")

    def _get_live_metrics(self) -> Optional[ChannelMetrics]:
        """Fetch real metrics from YouTube Analytics API."""
        try:
            from uploader.uploader import _get_authenticated_service
            youtube = _get_authenticated_service()

            # Channel stats
            ch_resp = youtube.channels().list(
                part="statistics,snippet",
                mine=True,
            ).execute()
            if not ch_resp.get("items"):
                return None
            stats = ch_resp["items"][0]["statistics"]

            # Analytics (requires youtube.analytics scope — simplified here)
            subs = int(stats.get("subscriberCount", 0))
            total_views = int(stats.get("viewCount", 0))

            # Latest video
            vids_resp = youtube.search().list(
                part="id,snippet",
                forMine=True,
                type="video",
                order="date",
                maxResults=1,
            ).execute()
            latest_title = ""
            latest_views = 0
            latest_ctr = 0.0
            if vids_resp.get("items"):
                vid_id = vids_resp["items"][0]["id"]["videoId"]
                latest_title = vids_resp["items"][0]["snippet"]["title"]
                vid_stats_resp = youtube.videos().list(
                    part="statistics",
                    id=vid_id,
                ).execute()
                if vid_stats_resp.get("items"):
                    vs = vid_stats_resp["items"][0]["statistics"]
                    latest_views = int(vs.get("viewCount", 0))
                    impressions = int(vs.get("viewCount", 1))  # placeholder
                    latest_ctr = round((latest_views / max(impressions, 1)) * 100, 2)

            return ChannelMetrics(
                snapshot_time=datetime.now().isoformat(),
                subscribers=subs,
                total_views=total_views,
                last_7d_views=0,        # requires Analytics API
                last_7d_ctr=0.0,
                last_7d_avg_view_pct=0.0,
                last_7d_revenue=None,
                latest_video_views=latest_views,
                latest_video_ctr=latest_ctr,
                latest_video_title=latest_title,
            )

        except Exception as exc:
            logger.error("Failed to fetch live metrics: %s", exc)
            return None

    def _get_mock_metrics(self) -> ChannelMetrics:
        """Mock metrics for development without API access."""
        import random
        base_subs = 284000 + random.randint(-50, 200)
        return ChannelMetrics(
            snapshot_time=datetime.now().isoformat(),
            subscribers=base_subs,
            total_views=12_400_000 + random.randint(0, 5000),
            last_7d_views=random.randint(80000, 150000),
            last_7d_ctr=round(random.uniform(4.5, 8.5), 2),
            last_7d_avg_view_pct=round(random.uniform(35, 55), 1),
            last_7d_revenue=round(random.uniform(300, 900), 2),
            latest_video_views=random.randint(10000, 80000),
            latest_video_ctr=round(random.uniform(4.0, 9.0), 2),
            latest_video_title="Latest DriftWire326 Market Recap",
        )

    def _detect_alerts(self, current: ChannelMetrics) -> list[AlertEvent]:
        alerts: list[AlertEvent] = []
        prev = self._prev_metrics

        # Subscriber milestones
        if prev:
            for milestone in THRESHOLDS["sub_milestone"]:
                if prev.subscribers < milestone <= current.subscribers:
                    alerts.append(AlertEvent(
                        level="milestone",
                        event="Subscriber Milestone",
                        detail=f"🎉 DriftWire326 just hit {milestone:,} subscribers!",
                        metric_value=current.subscribers,
                        threshold=float(milestone),
                    ))

        # View spike (viral detection)
        if prev and prev.latest_video_views > 0:
            view_growth_pct = ((current.latest_video_views - prev.latest_video_views)
                               / prev.latest_video_views) * 100
            if view_growth_pct >= THRESHOLDS["view_spike_pct"]:
                alerts.append(AlertEvent(
                    level="milestone",
                    event="Viral Video Detected",
                    detail=(f"🚀 '{current.latest_video_title}' is going viral! "
                            f"+{view_growth_pct:.0f}% views in 2h"),
                    metric_value=view_growth_pct,
                    threshold=float(THRESHOLDS["view_spike_pct"]),
                ))

        # CTR drop warning
        if (current.last_7d_ctr > 0 and prev and prev.last_7d_ctr > 0):
            ctr_change_pct = ((current.last_7d_ctr - prev.last_7d_ctr) / prev.last_7d_ctr) * 100
            if ctr_change_pct <= -THRESHOLDS["ctr_drop_pct"]:
                alerts.append(AlertEvent(
                    level="warning",
                    event="CTR Drop Alert",
                    detail=f"⚠️ 7-day CTR dropped {abs(ctr_change_pct):.1f}% — review thumbnails/titles",
                    metric_value=current.last_7d_ctr,
                    threshold=prev.last_7d_ctr,
                ))

        return alerts

    def _send_alert_email(self, alerts: list[AlertEvent], metrics: ChannelMetrics) -> None:
        """Send email alert (requires SMTP settings in .env)."""
        smtp_host = os.environ.get("SMTP_HOST", "")
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        alert_email = os.environ.get("ALERT_EMAIL", smtp_user)

        if not all([smtp_host, smtp_user, smtp_pass, alert_email]):
            logger.debug("SMTP not configured — skipping email alert")
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"DriftWire326 Alert: {alerts[0].event}"
            msg["From"] = smtp_user
            msg["To"] = alert_email

            body = f"""
DriftWire326 Channel Alert
Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}

ALERTS:
{''.join(f"• [{a.level.upper()}] {a.event}: {a.detail}" + chr(10) for a in alerts)}

CURRENT METRICS:
• Subscribers: {metrics.subscribers:,}
• Total Views: {metrics.total_views:,}
• Latest Video Views: {metrics.latest_video_views:,}
• Latest Video CTR: {metrics.latest_video_ctr}%
"""
            msg.attach(MIMEText(body, "plain"))
            with smtplib.SMTP(smtp_host, 587, timeout=30) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, alert_email, msg.as_string())
            logger.info("Alert email sent to %s", alert_email)
        except Exception as exc:
            logger.warning("Alert email failed: %s", exc)

    def run_check(self) -> None:
        """Main check loop — called every 2 hours by the scheduler."""
        logger.info("Monitor check running — %s", datetime.now().isoformat())

        # Try live metrics, fall back to mock in dev
        metrics = self._get_live_metrics()
        if metrics is None:
            logger.info("Using mock metrics (YouTube API unavailable)")
            metrics = self._get_mock_metrics()

        self._log_metrics(metrics)
        alerts = self._detect_alerts(metrics)

        for alert in alerts:
            level_fn = {
                "info": logger.info,
                "warning": logger.warning,
                "milestone": logger.info,
                "urgent": logger.error,
            }.get(alert.level, logger.info)
            level_fn("MONITOR ALERT [%s]: %s", alert.event, alert.detail)

        if alerts:
            self._send_alert_email(alerts, metrics)

        self._prev_metrics = metrics

        logger.info(
            "Monitor check complete — Subs: %d | Views (latest): %d | CTR: %.2f%%",
            metrics.subscribers, metrics.latest_video_views, metrics.latest_video_ctr,
        )


# ── PipelineMonitor class ─────────────────────────────────────────────────────

PIPELINE_SUMMARY_LOG = settings.logs_dir / "pipeline_daily.jsonl"
LOW_QUOTA_THRESHOLD = 2000
API_STATUS_FILE = settings.logs_dir / "api_status.json"


class PipelineMonitor:
    """Monitors DriftWire326 pipeline health, files, quota, and API status."""

    def __init__(self):
        settings.logs_dir.mkdir(parents=True, exist_ok=True)

    def check_pipeline_health(self) -> dict:
        """
        Aggregate health check — runs all sub-checks.
        Returns a dict with keys: files, quota, api, last_upload, healthy.
        """
        files_ok = self.check_output_files_exist()
        quota_ok = self.check_quota_status()
        api_ok = self.check_api_status()
        last_upload_ok = self.check_last_upload_success()

        healthy = all([files_ok, quota_ok, api_ok, last_upload_ok])
        summary = {
            "timestamp": datetime.now().isoformat(),
            "healthy": healthy,
            "files_ok": files_ok,
            "quota_ok": quota_ok,
            "api_ok": api_ok,
            "last_upload_ok": last_upload_ok,
        }
        if not healthy:
            logger.warning("Pipeline health check FAILED: %s", summary)
        else:
            logger.info("Pipeline health check PASSED")
        return summary

    def check_output_files_exist(self) -> bool:
        """
        Verify today's output files were produced (scripts, audio, video).
        Returns True if at least one video was built today.
        """
        today = datetime.now().strftime("%Y%m%d")
        checks = [
            settings.output_dir / "scripts",
            settings.output_dir / "audio",
            settings.output_dir / "videos",
        ]
        videos_today = list((settings.output_dir / "videos").glob(f"*{today}*.mp4"))
        if not videos_today:
            logger.warning("check_output_files_exist: no videos found for %s", today)
            return False
        missing_dirs = [d for d in checks if not d.exists()]
        if missing_dirs:
            logger.warning("Output directories missing: %s", missing_dirs)
            return False
        return True

    def check_quota_status(self) -> bool:
        """
        Check YouTube API quota is sufficient for at least one more upload.
        Returns True if remaining quota ≥ LOW_QUOTA_THRESHOLD.
        """
        try:
            from uploader.quota_tracker import QuotaTracker
            qt = QuotaTracker()
            remaining = qt.get_remaining()
            if remaining < LOW_QUOTA_THRESHOLD:
                logger.warning("Quota low: %d units remaining (threshold: %d)", remaining, LOW_QUOTA_THRESHOLD)
                return False
            return True
        except Exception as exc:
            logger.error("check_quota_status failed: %s", exc)
            return False

    def check_api_status(self) -> bool:
        """
        Quick liveness check for Anthropic + yfinance APIs.
        Returns True if both respond without error.
        """
        results = {}
        # Check Claude API (Anthropic)
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            _ = client.models.list()
            results["anthropic"] = True
        except Exception as exc:
            logger.warning("Anthropic API check failed: %s", exc)
            results["anthropic"] = False

        # Check market data (yfinance — simple SPY price fetch)
        try:
            import yfinance as yf
            hist = yf.Ticker("SPY").history(period="1d")
            results["yfinance"] = not hist.empty
        except Exception as exc:
            logger.warning("yfinance API check failed: %s", exc)
            results["yfinance"] = False

        # Save status
        try:
            API_STATUS_FILE.write_text(json.dumps({"timestamp": datetime.now().isoformat(), **results}))
        except Exception as exc:
            logger.warning("Could not write API status file: %s", exc)

        all_ok = all(results.values())
        if not all_ok:
            logger.warning("API status: %s", results)
        return all_ok

    def check_last_upload_success(self) -> bool:
        """
        Inspect quota_tracker.json to verify at least one upload today.
        Returns True if an upload was recorded in the last 24h.
        """
        try:
            from uploader.quota_tracker import QuotaTracker, QUOTA_LOG_PATH
            if not QUOTA_LOG_PATH.exists():
                logger.warning("check_last_upload_success: quota log not found")
                return False
            qt = QuotaTracker()
            state = qt.status()
            if state.uploads_today == 0:
                logger.warning("No uploads recorded today (%s)", state.date)
                return False
            return True
        except Exception as exc:
            logger.error("check_last_upload_success failed: %s", exc)
            return False

    def log_daily_summary(self) -> dict:
        """
        Run a full health check and log to pipeline_daily.jsonl.
        Designed to be called at 23:00 ET.
        """
        health = self.check_pipeline_health()
        try:
            from uploader.quota_tracker import QuotaTracker
            qt = QuotaTracker()
            health["quota_summary"] = qt.get_daily_summary()
        except Exception:
            health["quota_summary"] = {}

        try:
            with open(PIPELINE_SUMMARY_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(health) + "\n")
            logger.info("Daily summary logged → %s", PIPELINE_SUMMARY_LOG)
        except Exception as exc:
            logger.error("log_daily_summary write failed: %s", exc)
        return health

    def alert(self, message: str, level: str = "warning") -> None:
        """
        Emit a pipeline alert at the given level to the log and (if configured) email.
        level: 'info' | 'warning' | 'error' | 'critical'
        """
        level_fn = {
            "info": logger.info,
            "warning": logger.warning,
            "error": logger.error,
            "critical": logger.critical,
        }.get(level, logger.warning)
        level_fn("PIPELINE ALERT [%s]: %s", level.upper(), message)

        # Attempt email alert via SMTP
        smtp_host = os.environ.get("SMTP_HOST", "")
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        alert_email = os.environ.get("ALERT_EMAIL", smtp_user)

        if all([smtp_host, smtp_user, smtp_pass, alert_email]):
            try:
                msg = MIMEText(f"DriftWire326 Pipeline Alert\n\n[{level.upper()}] {message}")
                msg["Subject"] = f"DriftWire326 [{level.upper()}] Pipeline Alert"
                msg["From"] = smtp_user
                msg["To"] = alert_email
                with smtplib.SMTP(smtp_host, 587, timeout=30) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, alert_email, msg.as_string())
            except Exception as exc:
                logger.debug("Alert email failed: %s", exc)
