"""
monitor/discovery_report.py — why the videos are or are not being watched.

The channel has uploads, a working pipeline, and almost no views. That has
three possible causes and they need completely different work:

  * YouTube is not showing the videos to anyone      → few impressions
  * YouTube is showing them and nobody clicks        → impressions, low CTR
  * People click and leave                           → CTR fine, no watch time

Guessing between those wastes weeks. The existing weekly report prints
totals, which cannot separate them — a channel with 200 impressions and one
with 200,000 both look like "low views". This reads the traffic-source
breakdown alongside impressions and click-through and says which of the
three it actually is.

It reports; it does not fix. Every threshold here labels a number, and none
of them gate anything.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_OK, _WARN, _FAIL = "✅", "⚠️ ", "❌"

# The surfaces where YouTube itself decides to show a video. Views from
# anywhere else are views someone was sent, not views the platform gave.
ALGORITHMIC_SOURCES = {
    "SUBSCRIBER",       # home and subscriptions feeds — "browse features"
    "RELATED_VIDEO",    # suggested alongside another video
    "SHORTS",           # the Shorts feed
    "YT_SEARCH",        # search: demand-driven, but YouTube still ranks it
    "NOTIFICATION",
    "HASHTAGS",
    "SOUND_PAGE",
    "VIDEO_REMIXES",
}

_SOURCE_NAMES = {
    "SUBSCRIBER": "Browse (home / subscriptions)",
    "RELATED_VIDEO": "Suggested videos",
    "SHORTS": "Shorts feed",
    "YT_SEARCH": "YouTube search",
    "NOTIFICATION": "Notifications",
    "PLAYLIST": "Playlists",
    "YT_CHANNEL": "Channel page",
    "EXT_URL": "External links",
    "NO_LINK_OTHER": "Direct or unknown",
    "NO_LINK_EMBEDDED": "Embedded players",
    "END_SCREEN": "End screens",
    "HASHTAGS": "Hashtag pages",
}

# Where a video stops being invisible. Below this, click-through is noise:
# 3% of 40 impressions is one click, and one click says nothing about a
# thumbnail. Chosen so a verdict about CTR rests on enough impressions to
# mean something, not as a target to hit.
MIN_IMPRESSIONS_FOR_CTR = 300

# YouTube's own guidance puts a healthy channel around 4%. Used to label,
# never to gate.
GOOD_CTR = 0.04
POOR_CTR = 0.02

# Long-form retention floor. A viewer who leaves inside thirty seconds did
# not bounce off the algorithm, they bounced off the video.
POOR_VIEW_SECONDS = 30

# Retention needs enough views to mean anything, for the same reason
# click-through needs enough impressions. Across 84 views a single long
# session moves the average by seconds.
MIN_VIEWS_FOR_RETENTION = 200


@dataclass
class Diagnosis:
    verdict: str                    # the one-line answer
    bottleneck: str                 # "distribution" | "packaging" | "retention" | "unknown"
    status: str = _WARN
    numbers: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def _algorithmic_share(sources: dict) -> tuple:
    """Views YouTube chose to give, over total views."""
    total = sum(sources.values())
    if not total:
        return 0, 0, 0.0
    served = sum(views for source, views in sources.items()
                 if source in ALGORITHMIC_SOURCES)
    return served, total, served / total


# Views per video, per window, below which the algorithm is testing the
# channel but only in very small batches. Labels a number; gates nothing.
LOW_VOLUME_VIEWS_PER_VIDEO = 25


def _top_algorithmic(sources: dict) -> tuple:
    """The YouTube surface already working hardest, and its share."""
    served = {source: views for source, views in sources.items()
              if source in ALGORITHMIC_SOURCES and views}
    if not served:
        return "", 0.0
    total = sum(sources.values()) or 1
    best = max(served.items(), key=lambda kv: kv[1])
    return best[0], best[1] / total


def diagnose(channel: dict, sources: dict, videos: int) -> Diagnosis:
    """
    Turn the analytics numbers into the one thing worth working on next.

    `channel` is fetch_channel_stats() output, `sources` is
    fetch_traffic_sources() output, `videos` the number of uploads.

    Missing data is never read as a finding. "No algorithmic traffic" and
    "the API call failed" look identical in the numbers, and an unreported
    impression count is not a zero impression count — so each says so in its
    own words instead.
    """
    impressions = int(channel.get("total_impressions") or 0)
    # A failed query reports zero impressions the same way a channel with no
    # impressions does. Trusting the number through an error would turn an
    # API fault into a confident "YouTube is barely showing these videos".
    have_impressions = (bool(channel.get("impressions_available"))
                        and not channel.get("error"))
    views = int(channel.get("total_views") or 0)
    ctr = float(channel.get("avg_ctr") or 0.0)
    watch_minutes = float(channel.get("total_watch_time_minutes") or 0.0)
    subs = int(channel.get("subscribers_gained") or 0)
    served, total_source_views, share = _algorithmic_share(sources)
    best_source, best_share = _top_algorithmic(sources)

    # Traffic sources are their own query. When channel stats fail, they
    # usually still arrive — and they answer the more important question.
    view_total = views or total_source_views

    numbers = {
        "videos": videos,
        "impressions": impressions,
        "impressions_available": have_impressions,
        "views": view_total,
        "ctr": ctr,
        "watch_minutes": round(watch_minutes, 1),
        "subscribers_gained": subs,
        "impressions_per_video": round(impressions / videos, 1) if videos else 0.0,
        "views_per_video": round(view_total / videos, 1) if videos else 0.0,
        "algorithmic_share": round(share, 3),
    }
    seconds_per_view = (watch_minutes * 60 / views) if views else 0.0
    numbers["seconds_per_view"] = round(seconds_per_view, 1)

    notes = []
    if channel.get("error"):
        notes.append(f"Channel totals unavailable: {channel['error']}")
        notes.append("If this is an auth failure, check config/analytics_token.json "
                     "and the youtube.analytics.readonly scope.")
    elif not have_impressions:
        notes.append("Impressions and click-through are not reported for this "
                     "channel — read those two in YouTube Studio.")
    if not sources:
        notes.append("Traffic sources unavailable — the split below is "
                     "missing, not empty.")

    if not sources and not view_total:
        return Diagnosis(
            verdict="No views and no traffic data in this window.",
            bottleneck="unknown", status=_FAIL, numbers=numbers, sources=sources,
            notes=notes + [
                "Either the videos are not public, or analytics has not "
                "caught up — YouTube lags 24–48 hours.",
                "Run --verify-uploads to confirm what is actually public.",
            ],
        )

    # Ordered by what has to be true first. Whether YouTube is showing the
    # videos at all comes before anything about thumbnails, and the traffic
    # split answers it without needing impressions.
    if sources and share < 0.25:
        return Diagnosis(
            verdict=(f"Only {share:.0%} of views came from YouTube's own "
                     f"surfaces — the channel is not being distributed."),
            bottleneck="distribution", status=_FAIL, numbers=numbers,
            sources=sources,
            notes=notes + [
                f"{served} of {total_source_views} views were browse, "
                "suggested, Shorts feed or search. The rest arrived from "
                "links, channel pages or direct visits.",
            ],
        )

    if have_impressions and impressions < MIN_IMPRESSIONS_FOR_CTR:
        return Diagnosis(
            verdict=(f"YouTube is barely showing these videos: "
                     f"{impressions} impressions across {videos} uploads."),
            bottleneck="distribution", status=_FAIL, numbers=numbers,
            sources=sources,
            notes=notes + [
                "Click-through cannot be judged at this volume — better "
                "thumbnails convert impressions you already have, and there "
                "are almost none to convert.",
            ],
        )

    if have_impressions and impressions >= MIN_IMPRESSIONS_FOR_CTR and ctr < POOR_CTR:
        return Diagnosis(
            verdict=(f"Videos are being shown ({impressions:,} impressions) "
                     f"and not clicked — {ctr:.1%} click-through."),
            bottleneck="packaging", status=_WARN, numbers=numbers,
            sources=sources,
            notes=notes + [
                f"A healthy channel sits near {GOOD_CTR:.0%}.",
                "This is the case where thumbnails and titles are the work.",
            ],
        )

    if views >= MIN_VIEWS_FOR_RETENTION and seconds_per_view < POOR_VIEW_SECONDS:
        return Diagnosis(
            verdict=(f"People click and leave — {seconds_per_view:.0f}s "
                     f"average across {views:,} views."),
            bottleneck="retention", status=_WARN, numbers=numbers,
            sources=sources,
            notes=notes + [
                "The packaging is working and the video is not holding "
                "them. The first fifteen seconds are the work.",
            ],
        )

    if sources and videos and numbers["views_per_video"] < LOW_VOLUME_VIEWS_PER_VIDEO:
        # The algorithm is testing the channel, in very small batches. This
        # is not the same as being suppressed, and the work is different:
        # feed the surface already winning rather than fix a blockage.
        return Diagnosis(
            verdict=(f"YouTube IS distributing this — {share:.0%} of "
                     f"{view_total} views came from its own surfaces. There "
                     f"is just very little of it: {numbers['views_per_video']:.0f} "
                     f"views per video."),
            bottleneck="volume", status=_WARN, numbers=numbers, sources=sources,
            notes=notes + [
                f"Biggest surface: {_SOURCE_NAMES.get(best_source, best_source)} "
                f"at {best_share:.0%} of all views. That is the one already "
                "working, so it is the one worth feeding.",
                "Nothing here says the videos are being suppressed.",
            ],
        )

    return Diagnosis(
        verdict=(f"{view_total:,} views, {share:.0%} from YouTube's own "
                 f"surfaces, {seconds_per_view:.0f}s average."),
        bottleneck="none", status=_OK, numbers=numbers, sources=sources,
        notes=notes + ["Nothing here is the obvious bottleneck. Keep going."],
    )


def _format(diagnosis: Diagnosis, days: int) -> list:
    lines = [
        f"\n{'=' * 62}",
        f"  DISCOVERY DIAGNOSIS — last {days} days",
        f"{'=' * 62}",
        "",
        f"{diagnosis.status} {diagnosis.verdict}",
        "",
    ]
    for note in diagnosis.notes:
        lines.append(f"   {note}")
    if diagnosis.notes:
        lines.append("")

    numbers = diagnosis.numbers
    impressions = (
        f"{numbers['impressions']:,} ({numbers['impressions_per_video']:.0f} per video)"
        if numbers.get("impressions_available") else "not reported — see Studio"
    )
    ctr = (f"{numbers['ctr']:.2%}" if numbers.get("impressions_available")
           else "not reported — see Studio")
    lines += [
        "  Numbers",
        f"    Videos on channel       {numbers['videos']}",
        f"    Views                   {numbers['views']:,} "
        f"({numbers['views_per_video']:.0f} per video)",
        f"    From YouTube itself     {numbers['algorithmic_share']:.0%}",
        f"    Impressions             {impressions}",
        f"    Click-through           {ctr}",
        f"    Average view            {numbers['seconds_per_view']:.0f}s",
        f"    Watch time              {numbers['watch_minutes']:.0f} min",
        f"    Subscribers gained      {numbers['subscribers_gained']}",
        "",
    ]

    if diagnosis.sources:
        total = sum(diagnosis.sources.values()) or 1
        lines.append("  Where the views came from")
        for source, count in sorted(diagnosis.sources.items(),
                                    key=lambda kv: -kv[1]):
            mark = "▲" if source in ALGORITHMIC_SOURCES else " "
            name = _SOURCE_NAMES.get(source, source)
            lines.append(f"    {mark} {name:<32} {count:>6,}  "
                         f"{count / total:>5.0%}")
        lines += ["", "    ▲ = YouTube chose to show it. Everything else is "
                      "traffic you sent yourself.", ""]
    return lines


def run(days: int = 28) -> Diagnosis:
    """Fetch, diagnose and print. Returns the Diagnosis for callers."""
    from channel_manager.analytics_tracker import AnalyticsTracker

    end = date.today()
    start = end - timedelta(days=days)
    tracker = AnalyticsTracker()

    channel = tracker.fetch_channel_stats(start.isoformat(), end.isoformat())
    sources = tracker.fetch_traffic_sources(start.isoformat(), end.isoformat())
    videos = tracker.recent_video_count(max_results=50)

    diagnosis = diagnose(channel, sources, videos)
    for line in _format(diagnosis, days):
        print(line)
    return diagnosis
