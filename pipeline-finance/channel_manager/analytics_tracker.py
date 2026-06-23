"""
channel_manager/analytics_tracker.py — DriftWire326 Module 23
Pulls YouTube Analytics API data daily and generates weekly performance reports.
Flags videos under 2% CTR for title A/B swap via videos.update.

Data saved to:
  logs/analytics/YYYY-MM-DD.json        — daily raw stats
  logs/analytics/weekly_report_YYYY-Www.json — weekly summary reports

Requires additional OAuth scope: yt-analytics.readonly
This scope is added at first authentication.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

ANALYTICS_DIR = settings.logs_dir / "analytics"
ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)

LOW_CTR_THRESHOLD = 0.02   # 2% — flag for title A/B testing
MIN_IMPRESSIONS_FOR_FLAG = 500  # only flag if video has enough impressions

ANALYTICS_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


@dataclass
class VideoStats:
    video_id: str
    date: str
    views: int = 0
    impressions: int = 0
    ctr: float = 0.0          # click-through rate (decimal, e.g. 0.045 = 4.5%)
    watch_time_minutes: float = 0.0
    avg_view_duration_seconds: float = 0.0
    subscribers_gained: int = 0
    likes: int = 0
    comments: int = 0

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "date": self.date,
            "views": self.views,
            "impressions": self.impressions,
            "ctr": round(self.ctr, 4),
            "watch_time_minutes": round(self.watch_time_minutes, 1),
            "avg_view_duration_seconds": round(self.avg_view_duration_seconds, 1),
            "subscribers_gained": self.subscribers_gained,
            "likes": self.likes,
            "comments": self.comments,
        }

    @property
    def ctr_pct(self) -> str:
        return f"{self.ctr * 100:.1f}%"


@dataclass
class WeeklyReport:
    week_of: str           # ISO week string e.g. "2026-W26"
    start_date: str
    end_date: str
    total_views: int = 0
    total_watch_time_minutes: float = 0.0
    avg_ctr: float = 0.0
    top_video_id: Optional[str] = None
    top_video_views: int = 0
    low_ctr_videos: list[str] = field(default_factory=list)
    subscribers_gained: int = 0
    total_impressions: int = 0
    video_count: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "week_of": self.week_of,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_views": self.total_views,
            "total_watch_time_minutes": round(self.total_watch_time_minutes, 1),
            "avg_ctr": round(self.avg_ctr, 4),
            "avg_ctr_pct": f"{self.avg_ctr * 100:.1f}%",
            "top_video_id": self.top_video_id,
            "top_video_views": self.top_video_views,
            "low_ctr_videos": self.low_ctr_videos,
            "subscribers_gained": self.subscribers_gained,
            "total_impressions": self.total_impressions,
            "video_count": self.video_count,
            "generated_at": self.generated_at,
        }


def _get_youtube_service():
    """YouTube Data API v3 service (for videos.update, videos.list)."""
    from uploader.uploader import _get_authenticated_service
    return _get_authenticated_service()


def _get_analytics_service():
    """
    YouTube Analytics API v2 service.
    Requires yt-analytics.readonly scope in addition to Data API scopes.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        all_scopes = [
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ]
        creds_path = settings.root_dir / "config" / "analytics_token.json"
        creds = None

        if creds_path.exists():
            creds = Credentials.from_authorized_user_file(str(creds_path), all_scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not settings.oauth_file.exists():
                    raise FileNotFoundError(
                        f"YouTube OAuth client secrets not found: {settings.oauth_file}"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(settings.oauth_file), all_scopes
                )
                creds = flow.run_local_server(port=0)
            creds_path.write_text(creds.to_json())

        return build("youtubeAnalytics", "v2", credentials=creds)

    except ImportError as exc:
        raise ImportError(
            "Google API client libraries not installed.\n"
            "Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc


class AnalyticsTracker:
    """Pull YouTube Analytics and generate performance reports."""

    def __init__(self, youtube_service=None, analytics_service=None):
        self._svc = youtube_service
        self._analytics_svc = analytics_service

    def _service(self):
        if self._svc is None:
            self._svc = _get_youtube_service()
        return self._svc

    def _analytics(self):
        if self._analytics_svc is None:
            self._analytics_svc = _get_analytics_service()
        return self._analytics_svc

    # ── Data fetching ────────────────────────────────────────────────────────

    def fetch_video_stats(
        self,
        video_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[VideoStats]:
        """
        Fetch analytics for a single video over a date range.
        Defaults to yesterday (most complete data available).
        Returns VideoStats dataclass or None on failure.
        """
        if start_date is None:
            start_date = (date.today() - timedelta(days=1)).isoformat()
        if end_date is None:
            end_date = start_date

        try:
            channel_id = settings.channel_id or "mine"
            response = self._analytics().reports().query(
                ids=f"channel=={channel_id}",
                startDate=start_date,
                endDate=end_date,
                metrics="views,estimatedMinutesWatched,impressions,impressionClickThroughRate,"
                        "averageViewDuration,subscribersGained,likes,comments",
                dimensions="video",
                filters=f"video=={video_id}",
            ).execute()

            rows = response.get("rows", [])
            if not rows:
                logger.debug("No analytics data for video %s on %s", video_id, start_date)
                return VideoStats(video_id=video_id, date=start_date)

            row = rows[0]
            # columns: video, views, estimatedMinutesWatched, impressions, ctr,
            #          avgViewDuration, subscribersGained, likes, comments
            _, views, watch_min, impressions, ctr, avg_dur, subs, likes, comments = row

            return VideoStats(
                video_id=video_id,
                date=start_date,
                views=int(views or 0),
                impressions=int(impressions or 0),
                ctr=float(ctr or 0.0),
                watch_time_minutes=float(watch_min or 0.0),
                avg_view_duration_seconds=float(avg_dur or 0.0),
                subscribers_gained=int(subs or 0),
                likes=int(likes or 0),
                comments=int(comments or 0),
            )

        except Exception as exc:
            logger.error("Failed to fetch stats for video %s: %s", video_id, exc)
            return None

    def fetch_channel_stats(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """
        Fetch aggregate channel-level analytics for a date range.
        Returns a dict with totals.
        """
        if start_date is None:
            start_date = (date.today() - timedelta(days=7)).isoformat()
        if end_date is None:
            end_date = date.today().isoformat()

        try:
            channel_id = settings.channel_id or "mine"
            response = self._analytics().reports().query(
                ids=f"channel=={channel_id}",
                startDate=start_date,
                endDate=end_date,
                metrics="views,estimatedMinutesWatched,impressions,"
                        "impressionClickThroughRate,subscribersGained",
            ).execute()

            rows = response.get("rows", [])
            if not rows:
                return {"start_date": start_date, "end_date": end_date}

            row = rows[0]
            views, watch_min, impressions, ctr, subs = row
            return {
                "start_date": start_date,
                "end_date": end_date,
                "total_views": int(views or 0),
                "total_watch_time_minutes": float(watch_min or 0.0),
                "total_impressions": int(impressions or 0),
                "avg_ctr": float(ctr or 0.0),
                "subscribers_gained": int(subs or 0),
            }

        except Exception as exc:
            logger.error("Failed to fetch channel stats: %s", exc)
            return {"start_date": start_date, "end_date": end_date, "error": str(exc)}

    def _list_recent_video_ids(self, max_results: int = 20) -> list[str]:
        """List the channel's most recent video IDs via Data API."""
        try:
            channel_id = settings.channel_id
            request = self._service().search().list(
                part="id",
                channelId=channel_id,
                type="video",
                order="date",
                maxResults=max_results,
            )
            response = request.execute()
            return [item["id"]["videoId"] for item in response.get("items", [])]
        except Exception as exc:
            logger.error("Failed to list recent video IDs: %s", exc)
            return []

    # ── Reporting ────────────────────────────────────────────────────────────

    def generate_weekly_report(self, week_of: Optional[str] = None) -> WeeklyReport:
        """
        Generate a weekly performance report.
        week_of: ISO week string e.g. "2026-W26". Defaults to last complete week.
        Fetches stats for all recent videos and aggregates.
        """
        today = date.today()
        if week_of is None:
            # Last complete week (Mon–Sun)
            last_monday = today - timedelta(days=today.weekday() + 7)
            last_sunday = last_monday + timedelta(days=6)
            week_of = f"{last_monday.year}-W{last_monday.isocalendar()[1]:02d}"
            start_date = last_monday.isoformat()
            end_date = last_sunday.isoformat()
        else:
            # Parse "2026-W26" format
            year, week_num = week_of.split("-W")
            # Find Monday of that ISO week
            from datetime import date as dt_date
            jan4 = dt_date(int(year), 1, 4)  # always in week 1
            week_start = jan4 + timedelta(weeks=int(week_num) - 1 - jan4.isocalendar()[1] + 1,
                                          days=-jan4.weekday())
            start_date = week_start.isoformat()
            end_date = (week_start + timedelta(days=6)).isoformat()

        logger.info("Generating weekly report for %s (%s → %s)", week_of, start_date, end_date)

        video_ids = self._list_recent_video_ids(max_results=25)
        all_stats: list[VideoStats] = []

        for vid_id in video_ids:
            stats = self.fetch_video_stats(vid_id, start_date, end_date)
            if stats and stats.views > 0:
                all_stats.append(stats)

        # Aggregate
        total_views = sum(s.views for s in all_stats)
        total_watch = sum(s.watch_time_minutes for s in all_stats)
        total_impressions = sum(s.impressions for s in all_stats)
        total_subs = sum(s.subscribers_gained for s in all_stats)
        avg_ctr = (
            sum(s.ctr for s in all_stats) / len(all_stats) if all_stats else 0.0
        )
        top = max(all_stats, key=lambda s: s.views, default=None)
        low_ctr = [
            s.video_id for s in all_stats
            if s.ctr < LOW_CTR_THRESHOLD and s.impressions >= MIN_IMPRESSIONS_FOR_FLAG
        ]

        report = WeeklyReport(
            week_of=week_of,
            start_date=start_date,
            end_date=end_date,
            total_views=total_views,
            total_watch_time_minutes=total_watch,
            avg_ctr=avg_ctr,
            top_video_id=top.video_id if top else None,
            top_video_views=top.views if top else 0,
            low_ctr_videos=low_ctr,
            subscribers_gained=total_subs,
            total_impressions=total_impressions,
            video_count=len(all_stats),
        )

        # Save report
        report_path = ANALYTICS_DIR / f"weekly_report_{week_of.replace(':', '-')}.json"
        report_path.write_text(json.dumps(report.to_dict(), indent=2))
        logger.info("Weekly report saved → %s | %d videos | %.1f%% avg CTR",
                    report_path.name, len(all_stats), avg_ctr * 100)

        # Flag low-CTR videos
        if low_ctr:
            logger.warning(
                "%d video(s) under %.0f%% CTR flagged for title A/B testing: %s",
                len(low_ctr), LOW_CTR_THRESHOLD * 100, low_ctr,
            )

        return report

    def flag_low_ctr_videos(self, threshold: float = LOW_CTR_THRESHOLD) -> list[str]:
        """
        Return list of video IDs with CTR below threshold from the most recent
        analytics data file. Also logs the flagged videos for operator review.
        """
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        data_file = ANALYTICS_DIR / f"{yesterday}.json"

        if not data_file.exists():
            logger.info("No analytics file for %s — running daily pull first", yesterday)
            self.run_daily_pull()

        if not data_file.exists():
            return []

        try:
            entries = json.loads(data_file.read_text())
            flagged = [
                e["video_id"] for e in entries
                if e.get("ctr", 1.0) < threshold
                and e.get("impressions", 0) >= MIN_IMPRESSIONS_FOR_FLAG
            ]
            if flagged:
                logger.warning(
                    "Low CTR videos (<%d%%): %s",
                    int(threshold * 100),
                    flagged,
                )
            return flagged
        except Exception as exc:
            logger.error("Failed to flag low CTR videos: %s", exc)
            return []

    def swap_title_ab(self, video_id: str, new_title: str) -> bool:
        """
        Update a video's title for A/B testing via videos.update.
        Returns True on success.
        """
        try:
            # Fetch current snippet first (required to preserve other fields)
            response = self._service().videos().list(
                part="snippet", id=video_id
            ).execute()

            items = response.get("items", [])
            if not items:
                logger.error("Video %s not found for title swap", video_id)
                return False

            snippet = items[0]["snippet"]
            snippet["title"] = new_title[:100]  # YouTube title limit

            self._service().videos().update(
                part="snippet",
                body={"id": video_id, "snippet": snippet},
            ).execute()

            logger.info("Title A/B swap: video %s → '%s'", video_id, new_title[:60])
            return True

        except Exception as exc:
            logger.error("Title swap failed for video %s: %s", video_id, exc)
            return False

    # ── Persistence ──────────────────────────────────────────────────────────

    def save_daily_stats(
        self,
        stats: list[VideoStats],
        date_str: Optional[str] = None,
    ) -> Path:
        """Save daily stats list to logs/analytics/YYYY-MM-DD.json."""
        if date_str is None:
            date_str = date.today().isoformat()
        out_path = ANALYTICS_DIR / f"{date_str}.json"
        out_path.write_text(
            json.dumps([s.to_dict() for s in stats], indent=2)
        )
        logger.info("Daily stats saved → %s (%d videos)", out_path.name, len(stats))
        return out_path

    # ── Scheduled entrypoints ────────────────────────────────────────────────

    def run_daily_pull(self) -> list[VideoStats]:
        """
        Pull yesterday's analytics for all recent videos.
        Called daily by the scheduler.
        Saves to logs/analytics/YYYY-MM-DD.json.
        """
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        logger.info("Running daily analytics pull for %s", yesterday)

        video_ids = self._list_recent_video_ids(max_results=30)
        stats: list[VideoStats] = []
        for vid_id in video_ids:
            s = self.fetch_video_stats(vid_id, yesterday, yesterday)
            if s:
                stats.append(s)

        self.save_daily_stats(stats, yesterday)
        logger.info("Daily pull complete: %d video stats collected", len(stats))
        return stats

    def run_weekly_report(self) -> WeeklyReport:
        """
        Generate and save this week's performance report.
        Called every Monday by the scheduler.
        """
        return self.generate_weekly_report()
