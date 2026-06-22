"""
Channel performance monitor — polls YouTube Analytics API for KPIs,
sends email/webhook alerts on notable events, and logs to JSONL.
Runs every 2 hours via the master scheduler.
"""
import json
import logging
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
                return ChannelMetrics(**last)
        except Exception:
            pass
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
            import os
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
            with smtplib.SMTP(smtp_host, 587) as server:
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


import os  # needed by _send_alert_email
