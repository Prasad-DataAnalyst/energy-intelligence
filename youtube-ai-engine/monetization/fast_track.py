"""
monetization/fast_track.py — YouTube Partner Program Sprint Engine
===================================================================
Automates the fastest path to YouTube monetization (YPP).
Tracks progress toward 1,000 subscribers / 4,000 watch hours,
generates sprint schedules, calculates revenue projections,
and activates additional revenue streams in priority order.

Phase 0 (0–500 subs):  3 Shorts/day
Phase 1 (500–1K subs): 1 long + 3 Shorts/day
Phase 2 (YPP pending): 1 long + 2 Shorts/day
Phase 3 (monetized):   Revenue-optimized schedule
"""
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.fast_track")

HERE = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

YPP_REQUIREMENTS = {
    "subscribers":  1_000,
    "watch_hours":  4_000,
    "min_videos":   10,
    "policy_clean": True,
}

PHASE_CONFIG = {
    0: {
        "name":          "Launch Sprint",
        "sub_range":     (0, 500),
        "strategy":      "3 Shorts/day, no long-form — optimize for discoverability",
        "shorts_per_day": 3,
        "longs_per_day":  0,
        "target_days":   30,
        "focus":         "viral reach",
        "key_action":    "Post Shorts at 08:00, 14:00, 20:00 local time",
    },
    1: {
        "name":          "Authority Building",
        "sub_range":     (500, 1_000),
        "strategy":      "1 long-form + 3 Shorts/day — build trust + watch hours",
        "shorts_per_day": 3,
        "longs_per_day":  1,
        "target_days":   45,
        "focus":         "watch hours",
        "key_action":    "10-15 min long-forms with strong retention hooks",
    },
    2: {
        "name":          "YPP Application Pending",
        "sub_range":     (1_000, 1_000),
        "strategy":      "1 long + 2 Shorts/day — maintain cadence while reviewing",
        "shorts_per_day": 2,
        "longs_per_day":  1,
        "target_days":   30,
        "focus":         "consistency",
        "key_action":    "Submit YPP application; respond to all comments",
    },
    3: {
        "name":          "Monetized Growth",
        "sub_range":     (1_000, 999_999),
        "strategy":      "Revenue-optimized: high-RPM topics, mid-roll ads, memberships",
        "shorts_per_day": 1,
        "longs_per_day":  1,
        "target_days":   365,
        "focus":         "revenue",
        "key_action":    "Enable all monetization features; test merchandise shelf",
    },
}

REVENUE_STREAMS = {
    "adsense": {
        "activation_month": 0,
        "description":     "YouTube AdSense (requires YPP approval)",
        "typical_rpm":     (3, 15),
        "requires_ypp":    True,
        "notes":           "Primary revenue until 10K views/month",
    },
    "affiliate": {
        "activation_month": 3,
        "description":     "Amazon Associates + niche affiliate programs",
        "typical_rpm":     (5, 30),
        "requires_ypp":    False,
        "notes":           "Add links in description from day 1; monetize from month 3",
    },
    "sponsorships": {
        "activation_month": 6,
        "description":     "Direct brand sponsorships ($500–$5000/video)",
        "typical_rpm":     (20, 100),
        "requires_ypp":    False,
        "notes":           "Contact brands at 5K subscribers; use Passionfroot or Grapevine",
    },
    "merchandise": {
        "activation_month": 6,
        "description":     "Print-on-demand merch shelf (Printful/Spring/Printify)",
        "typical_rpm":     (10, 50),
        "requires_ypp":    True,
        "notes":           "Enable merch shelf at 10K subscribers",
    },
    "memberships": {
        "activation_month": 12,
        "description":     "Channel memberships ($2.99–$24.99/month)",
        "typical_rpm":     (30, 150),
        "requires_ypp":    True,
        "notes":           "Requires 1K subscribers and YPP",
    },
    "super_thanks": {
        "activation_month": 0,
        "description":     "Super Thanks / Super Chat (live streams)",
        "typical_rpm":     (5, 25),
        "requires_ypp":    True,
        "notes":           "Enable with YPP; promote during live streams",
    },
    "courses": {
        "activation_month": 12,
        "description":     "Digital products — Gumroad / Teachable / Kajabi courses",
        "typical_rpm":     (50, 500),
        "requires_ypp":    False,
        "notes":           "High margin; one course can replace 10K AdSense views",
    },
    "licensing": {
        "activation_month": 24,
        "description":     "License viral clips to news / media outlets",
        "typical_rpm":     (100, 1000),
        "requires_ypp":    False,
        "notes":           "Use Jukin Media or Storyful to license; passive income",
    },
}

COMPILATION_IDEAS = [
    {
        "title":        "I Watched EVERY {niche} Video in 2024 — Here's What I Learned",
        "watch_hours":  8,
        "views_est":    15_000,
        "description":  "Annual summary compilation — high watch time, evergreen",
    },
    {
        "title":        "The Ultimate {niche} Masterclass (Everything in One Video)",
        "watch_hours":  12,
        "views_est":    25_000,
        "description":  "Long compilation designed to hit 4000h fast",
    },
    {
        "title":        "Every {expert} Tip Ranked — Best to Worst",
        "watch_hours":  10,
        "views_est":    20_000,
        "description":  "Ranking format keeps viewers watching for the reveal",
    },
    {
        "title":        "{n} Hours of {topic} Deep Dives",
        "watch_hours":  {2: 2, 4: 4, 8: 8},
        "views_est":    8_000,
        "description":  "Long-form compilations ideal for passive watch-time farming",
    },
    {
        "title":        "The COMPLETE {topic} Guide From Beginner to Expert",
        "watch_hours":  9,
        "views_est":    18_000,
        "description":  "Tutorial compilations have high AVD%",
    },
]

RPM_BY_CATEGORY = {
    "personal_finance":  25,
    "insurance":         30,
    "legal_law":         28,
    "real_estate":       22,
    "business_startup":  20,
    "technology":        18,
    "health_medical":    15,
    "career_education":  12,
    "science":           10,
    "psychology":        10,
    "history":            9,
    "true_crime":         9,
    "politics":           8,
    "self_improvement":   8,
    "entertainment":      5,
    "sports":             5,
    "gaming":             4,
    "general":            6,
}


# ---------------------------------------------------------------------------
# WatchHoursCalculator
# ---------------------------------------------------------------------------

class WatchHoursCalculator:
    """Projects watch hours needed and expected timeline to YPP."""

    def current_gap(
        self, current_hours: float, current_subs: int
    ) -> Dict:
        hours_needed = max(0.0, YPP_REQUIREMENTS["watch_hours"] - current_hours)
        subs_needed  = max(0, YPP_REQUIREMENTS["subscribers"] - current_subs)
        return {
            "watch_hours_needed":   hours_needed,
            "subscribers_needed":   subs_needed,
            "watch_hours_pct":      min(100, current_hours / YPP_REQUIREMENTS["watch_hours"] * 100),
            "subscribers_pct":      min(100, current_subs / YPP_REQUIREMENTS["subscribers"] * 100),
            "ypp_ready":            (hours_needed == 0 and subs_needed == 0),
        }

    def project_timeline(
        self,
        current_hours: float,
        current_subs:  int,
        videos_per_day: float = 1.0,
        avg_video_length_min: float = 10.0,
        avg_views_per_video: int = 500,
        avg_avd_pct: float = 0.40,
        sub_rate_per_video: float = 5.0,
    ) -> Dict:
        gap = self.current_gap(current_hours, current_subs)
        if gap["ypp_ready"]:
            return {
                "days_to_ypp":  0,
                "date_estimate": datetime.now(timezone.utc).date().isoformat(),
                "gap":          gap,
                "bottleneck":   "none",
            }

        # Hours earned per day
        avg_view_duration_h = avg_video_length_min * avg_avd_pct / 60
        hours_per_day       = (
            videos_per_day * avg_views_per_video * avg_view_duration_h
        )
        subs_per_day = videos_per_day * sub_rate_per_video

        days_for_hours = (
            math.ceil(gap["watch_hours_needed"] / hours_per_day)
            if hours_per_day > 0 else 9999
        )
        days_for_subs = (
            math.ceil(gap["subscribers_needed"] / subs_per_day)
            if subs_per_day > 0 else 9999
        )

        days_to_ypp = max(days_for_hours, days_for_subs)
        date_est    = (
            datetime.now(timezone.utc) + timedelta(days=days_to_ypp)
        ).date().isoformat()
        bottleneck = "watch_hours" if days_for_hours >= days_for_subs else "subscribers"

        return {
            "days_to_ypp":         days_to_ypp,
            "date_estimate":       date_est,
            "gap":                 gap,
            "bottleneck":          bottleneck,
            "hours_per_day":       round(hours_per_day, 2),
            "subs_per_day":        round(subs_per_day, 2),
            "days_for_hours":      days_for_hours,
            "days_for_subs":       days_for_subs,
        }

    def compilation_boost(
        self, current_hours: float, target_hours: float = 4_000.0
    ) -> List[Dict]:
        """How many compilations of each type needed to hit target."""
        remaining = max(0.0, target_hours - current_hours)
        recs      = []
        for idea in COMPILATION_IDEAS:
            watch_h = idea.get("watch_hours", 8)
            if isinstance(watch_h, dict):
                watch_h = max(watch_h.values())
            needed = math.ceil(remaining / watch_h) if watch_h > 0 else 9999
            recs.append({
                "title":         idea["title"],
                "videos_needed": needed,
                "hours_each":    watch_h,
                "description":   idea["description"],
            })
        recs.sort(key=lambda x: x["videos_needed"])
        return recs


# ---------------------------------------------------------------------------
# RevenueStreamActivator
# ---------------------------------------------------------------------------

class RevenueStreamActivator:
    """Tells the channel what revenue streams to activate and when."""

    def get_active_streams(self, channel_age_months: int, ypp_approved: bool) -> List[Dict]:
        active = []
        for stream_id, cfg in REVENUE_STREAMS.items():
            if cfg["activation_month"] > channel_age_months:
                continue
            if cfg["requires_ypp"] and not ypp_approved:
                continue
            active.append({
                "stream_id":    stream_id,
                "description":  cfg["description"],
                "typical_rpm":  cfg["typical_rpm"],
                "notes":        cfg["notes"],
                "months_active": channel_age_months - cfg["activation_month"],
            })
        return active

    def get_upcoming_streams(
        self, channel_age_months: int, ypp_approved: bool, horizon_months: int = 6
    ) -> List[Dict]:
        upcoming = []
        for stream_id, cfg in REVENUE_STREAMS.items():
            months_away = cfg["activation_month"] - channel_age_months
            if 0 < months_away <= horizon_months:
                upcoming.append({
                    "stream_id":   stream_id,
                    "description": cfg["description"],
                    "months_away": months_away,
                    "notes":       cfg["notes"],
                    "requires_ypp": cfg["requires_ypp"],
                })
        upcoming.sort(key=lambda x: x["months_away"])
        return upcoming

    def project_monthly_revenue(
        self,
        monthly_views: int,
        channel_age_months: int,
        ypp_approved: bool,
        category: str = "general",
    ) -> Dict:
        rpm = RPM_BY_CATEGORY.get(category, RPM_BY_CATEGORY["general"])

        adsense_rev = (monthly_views / 1_000 * rpm) if ypp_approved else 0.0

        # Affiliate: ~1% CTR, $2 avg commission
        affiliate_rev = 0.0
        if channel_age_months >= REVENUE_STREAMS["affiliate"]["activation_month"]:
            affiliate_rev = monthly_views * 0.01 * 2.0

        # Sponsorship: 1 deal/month at $500 base for 1K views/video (rough)
        sponsorship_rev = 0.0
        if channel_age_months >= REVENUE_STREAMS["sponsorships"]["activation_month"]:
            sponsorship_rev = max(0, (monthly_views / 10_000) * 500)

        total = adsense_rev + affiliate_rev + sponsorship_rev
        return {
            "monthly_views":    monthly_views,
            "category_rpm":     rpm,
            "adsense":          round(adsense_rev, 2),
            "affiliate":        round(affiliate_rev, 2),
            "sponsorships":     round(sponsorship_rev, 2),
            "total_monthly":    round(total, 2),
            "annual_est":       round(total * 12, 2),
            "per_1k_views":     round(total / (monthly_views / 1_000) if monthly_views else 0, 2),
        }


# ---------------------------------------------------------------------------
# SprintScheduler
# ---------------------------------------------------------------------------

class SprintScheduler:
    """Generates daily upload schedules for each YPP sprint phase."""

    def get_phase(self, subscribers: int, ypp_approved: bool) -> int:
        if ypp_approved:
            return 3
        cfg = PHASE_CONFIG
        for phase_id in sorted(cfg.keys()):
            lo, hi = cfg[phase_id]["sub_range"]
            if lo <= subscribers <= hi or (phase_id == max(cfg) and subscribers >= lo):
                return phase_id
        return 0

    def get_phase_config(self, phase: int) -> Dict:
        return PHASE_CONFIG.get(phase, PHASE_CONFIG[0])

    def generate_weekly_schedule(
        self, phase: int, niche: str = "general", start_date: str = None
    ) -> List[Dict]:
        cfg         = self.get_phase_config(phase)
        shorts_day  = cfg["shorts_per_day"]
        longs_day   = cfg["longs_per_day"]
        start       = (
            datetime.fromisoformat(start_date)
            if start_date
            else datetime.now(timezone.utc)
        )
        schedule = []
        for day_offset in range(7):
            day        = start + timedelta(days=day_offset)
            day_posts  = []
            short_times = ["08:00", "14:00", "20:00", "12:00", "17:00"][:shorts_day]
            for i, t in enumerate(short_times):
                day_posts.append({
                    "type":      "short",
                    "time_utc":  t,
                    "duration":  "< 60s",
                    "focus":     f"Short #{i+1}: trending angle in {niche}",
                    "priority":  "high",
                })
            if longs_day:
                day_posts.append({
                    "type":      "long",
                    "time_utc":  "16:00",
                    "duration":  "10–15 min",
                    "focus":     f"Main video: high-RPM {niche} topic",
                    "priority":  "critical",
                })
            schedule.append({
                "date":       day.date().isoformat(),
                "day_of_week": day.strftime("%A"),
                "posts":      day_posts,
                "total_posts": len(day_posts),
            })
        return schedule

    def build_sprint_plan(
        self,
        current_subs: int,
        current_watch_hours: float,
        ypp_approved: bool,
        niche: str = "general",
        target_days: int = 90,
    ) -> Dict:
        phase     = self.get_phase(current_subs, ypp_approved)
        phase_cfg = self.get_phase_config(phase)
        calc      = WatchHoursCalculator()

        shorts = phase_cfg["shorts_per_day"]
        longs  = phase_cfg["longs_per_day"]
        videos_per_day = shorts + longs

        timeline = calc.project_timeline(
            current_hours=current_watch_hours,
            current_subs=current_subs,
            videos_per_day=videos_per_day,
            avg_video_length_min=11 if longs else 0.8,
            avg_views_per_video=300 if phase == 0 else 600,
            sub_rate_per_video=3 if phase == 0 else 6,
        )

        milestones = self._build_milestones(
            current_subs, current_watch_hours, phase, timeline
        )

        return {
            "phase":         phase,
            "phase_name":    phase_cfg["name"],
            "strategy":      phase_cfg["strategy"],
            "key_action":    phase_cfg["key_action"],
            "videos_per_day": videos_per_day,
            "shorts_per_day": shorts,
            "longs_per_day":  longs,
            "niche":          niche,
            "timeline":       timeline,
            "milestones":     milestones,
            "target_days":    target_days,
            "weekly_schedule": self.generate_weekly_schedule(phase, niche),
        }

    def _build_milestones(
        self,
        current_subs:  int,
        current_hours: float,
        phase:         int,
        timeline:      Dict,
    ) -> List[Dict]:
        now = datetime.now(timezone.utc)
        milestones = []

        sub_checkpoints = [100, 500, 1_000, 5_000, 10_000, 50_000, 100_000]
        for checkpoint in sub_checkpoints:
            if checkpoint > current_subs:
                subs_needed = checkpoint - current_subs
                days_est    = math.ceil(subs_needed / max(1, timeline.get("subs_per_day", 5)))
                date_est    = (now + timedelta(days=days_est)).date().isoformat()
                milestones.append({
                    "type":       "subscribers",
                    "target":     checkpoint,
                    "current":    current_subs,
                    "remaining":  subs_needed,
                    "days_est":   days_est,
                    "date_est":   date_est,
                    "label":      f"{checkpoint:,} Subscribers",
                    "importance": "critical" if checkpoint == 1_000 else "major",
                })
                if len(milestones) >= 3:
                    break

        hour_checkpoints = [500, 1_000, 2_000, 4_000]
        for checkpoint in hour_checkpoints:
            if checkpoint > current_hours:
                hours_needed = checkpoint - current_hours
                days_est     = math.ceil(
                    hours_needed / max(0.1, timeline.get("hours_per_day", 1))
                )
                date_est     = (now + timedelta(days=days_est)).date().isoformat()
                milestones.append({
                    "type":       "watch_hours",
                    "target":     checkpoint,
                    "current":    current_hours,
                    "remaining":  hours_needed,
                    "days_est":   days_est,
                    "date_est":   date_est,
                    "label":      f"{checkpoint:,} Watch Hours",
                    "importance": "critical" if checkpoint == 4_000 else "major",
                })
                if len([m for m in milestones if m["type"] == "watch_hours"]) >= 2:
                    break

        milestones.sort(key=lambda x: x["days_est"])
        return milestones


# ---------------------------------------------------------------------------
# FastTrackEngine — orchestrator
# ---------------------------------------------------------------------------

class FastTrackEngine:
    """Orchestrates the full monetization fast-track system."""

    def __init__(self, memory=None):
        self.memory    = memory
        self.calc      = WatchHoursCalculator()
        self.activator = RevenueStreamActivator()
        self.scheduler = SprintScheduler()

    # ── Public API ────────────────────────────────────────────────────────

    def run_daily_sprint(self) -> Dict:
        """Morning sprint check: load channel stats, generate today's plan."""
        stats = self._load_channel_stats()

        subs        = stats.get("subscribers", 0)
        watch_hours = stats.get("watch_hours", 0.0)
        ypp         = stats.get("ypp_approved", False)
        age_months  = stats.get("channel_age_months", 0)
        niche       = stats.get("primary_niche", "general")
        monthly_v   = stats.get("monthly_views", 0)

        phase    = self.scheduler.get_phase(subs, ypp)
        gap      = self.calc.current_gap(watch_hours, subs)
        timeline = self.calc.project_timeline(
            current_hours=watch_hours,
            current_subs=subs,
            videos_per_day=PHASE_CONFIG[phase]["shorts_per_day"] + PHASE_CONFIG[phase]["longs_per_day"],
        )
        sprint_plan    = self.scheduler.build_sprint_plan(subs, watch_hours, ypp, niche)
        active_streams = self.activator.get_active_streams(age_months, ypp)
        upcoming       = self.activator.get_upcoming_streams(age_months, ypp)
        revenue_proj   = self.activator.project_monthly_revenue(monthly_v, age_months, ypp, niche)
        compilations   = self.calc.compilation_boost(watch_hours)

        result = {
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "channel_stats":      stats,
            "phase":              phase,
            "phase_name":         PHASE_CONFIG[phase]["name"],
            "ypp_gap":            gap,
            "timeline":           timeline,
            "sprint_plan":        sprint_plan,
            "active_streams":     active_streams,
            "upcoming_streams":   upcoming,
            "revenue_projection": revenue_proj,
            "compilation_boost":  compilations[:3],
            "today_action":       PHASE_CONFIG[phase]["key_action"],
        }

        # Save milestones to memory
        self._save_milestones(sprint_plan.get("milestones", []), stats)

        return result

    def get_sprint_summary(self) -> str:
        """One-paragraph text summary of sprint status."""
        result = self.run_daily_sprint()
        stats  = result["channel_stats"]
        gap    = result["ypp_gap"]
        tl     = result["timeline"]
        phase  = result["phase"]
        rev    = result["revenue_projection"]

        if gap["ypp_ready"]:
            status = "YPP APPROVED — focus on revenue optimization."
        else:
            bottleneck = tl.get("bottleneck", "watch_hours")
            days       = tl.get("days_to_ypp", "N/A")
            pct_h      = round(gap["watch_hours_pct"], 1)
            pct_s      = round(gap["subscribers_pct"], 1)
            status = (
                f"Phase {phase} — {pct_h}% watch hours, {pct_s}% subscribers. "
                f"YPP est. in {days} days ({tl.get('date_estimate','?')}). "
                f"Bottleneck: {bottleneck}."
            )

        revenue_note = (
            f"Revenue projection: ${rev['total_monthly']:,.2f}/month "
            f"(${rev['annual_est']:,.0f}/year)."
        )
        return f"{status} {revenue_note} Today: {result['today_action']}"

    def record_milestone(
        self,
        milestone_type: str,
        value: float,
        notes: str = "",
    ) -> None:
        if self.memory and hasattr(self.memory, "save_sprint_milestone"):
            self.memory.save_sprint_milestone({
                "milestone_type": milestone_type,
                "value":          value,
                "notes":          notes,
                "achieved_at":    datetime.now(timezone.utc).isoformat(),
            })

    def get_milestone_history(self) -> List[Dict]:
        if self.memory and hasattr(self.memory, "get_sprint_milestones"):
            return self.memory.get_sprint_milestones()
        return []

    def update_channel_stats(self, **kwargs) -> None:
        """Write channel stats to memory config for the next sprint run."""
        if not self.memory:
            return
        for key, value in kwargs.items():
            try:
                if hasattr(self.memory, "set_config"):
                    self.memory.set_config(f"fast_track_{key}", str(value))
            except Exception as exc:
                log.warning("Could not save fast_track_%s: %s", key, exc)

    # ── Private helpers ───────────────────────────────────────────────────

    def _load_channel_stats(self) -> Dict:
        defaults = {
            "subscribers":         0,
            "watch_hours":         0.0,
            "ypp_approved":        False,
            "channel_age_months":  0,
            "primary_niche":       "general",
            "monthly_views":       0,
        }
        if not self.memory:
            return defaults

        for key in list(defaults.keys()):
            try:
                if hasattr(self.memory, "get_config"):
                    raw = self.memory.get_config(f"fast_track_{key}")
                    if raw is not None:
                        orig = defaults[key]
                        if isinstance(orig, bool):
                            defaults[key] = raw.lower() in ("1", "true", "yes")
                        elif isinstance(orig, int):
                            defaults[key] = int(float(raw))
                        elif isinstance(orig, float):
                            defaults[key] = float(raw)
                        else:
                            defaults[key] = raw
            except Exception:
                pass
        return defaults

    def _save_milestones(self, milestones: List[Dict], stats: Dict) -> None:
        if not self.memory or not hasattr(self.memory, "save_sprint_milestone"):
            return
        for m in milestones:
            try:
                self.memory.save_sprint_milestone({
                    "milestone_type": m.get("type", ""),
                    "target_value":   m.get("target", 0),
                    "current_value":  m.get("current", 0),
                    "days_estimated": m.get("days_est", 0),
                    "date_estimated": m.get("date_est", ""),
                    "label":          m.get("label", ""),
                    "importance":     m.get("importance", ""),
                    "notes":          json.dumps(stats),
                })
            except Exception as exc:
                log.debug("Could not save milestone: %s", exc)
