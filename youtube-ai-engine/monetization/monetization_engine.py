"""
monetization/monetization_engine.py — Multi-Stream Revenue Maximiser
=====================================================================
Maximises every dollar from every video across four revenue streams:

  AdRevenueOptimizer    Advertiser-friendly scoring, mid-roll placement,
                        demonetization word detection + auto-replace
  SponsorshipAutomation Brand discovery, outreach emails, deal value
                        estimation, sponsor segment auto-insertion at 35-40%
  AffiliateLinkEngine   Product detection, affiliate program matching,
                        description + pinned-comment link formatting
  MerchandiseTrigger    Subscriber milestone detection, merch idea generation,
                        Printful / Printify API stubs
  RevenueDashboard      Weekly RPM trend, niche comparison, growth forecast,
                        >20% drop alerts

Memory tables required (added to core/memory.py SCHEMA):
  sponsorships         Brand prospects and deal tracking
  affiliate_links      Product → program → placement tracking
  revenue_snapshots    Weekly revenue by stream
  monetization_events  General event / alert log
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.monetization")

HERE = Path(__file__).parent.parent

# ── Niche RPM benchmarks (USD per 1 000 views, 2024 averages) ─────────────────

NICHE_RPM = {
    "finance":          14.00,
    "business":         11.50,
    "technology":        8.50,
    "software":          7.50,
    "energy":            6.00,
    "health":            6.50,
    "education":         4.50,
    "science":           5.00,
    "gaming":            2.50,
    "entertainment":     3.00,
    "lifestyle":         3.50,
    "travel":            3.00,
    "default":           5.00,
}

# ── Mid-roll ad slot formula ───────────────────────────────────────────────────
# YouTube allows mid-rolls from 8 min; +1 slot per 2 additional minutes.
# slot_count = 1 + floor((duration_s - 480) / 120)  if  duration_s >= 480

def _midroll_slots(duration_s: float) -> int:
    if duration_s < 480:
        return 0
    return max(1, 1 + int((duration_s - 480) // 120))


def _midroll_timestamps(duration_s: float) -> List[float]:
    """Evenly-spaced mid-roll timestamps for a given duration (seconds)."""
    n = _midroll_slots(duration_s)
    if n == 0:
        return []
    interval = duration_s / (n + 1)
    return [round(interval * (i + 1), 1) for i in range(n)]


# ══════════════════════════════════════════════════════════════════════════════
# AdRevenueOptimizer
# ══════════════════════════════════════════════════════════════════════════════

# Words flagged by YouTube's advertiser-friendly content system.
# This list covers terms that, out of context, can trigger limited or no ads.
# Source: YouTube Help → Advertiser-friendly content guidelines (public).
_DEMONETIZATION_RISKS: Dict[str, str] = {
    # Violence
    "kill":      "eliminate",
    "kills":     "eliminates",
    "killed":    "eliminated",
    "killing":   "eliminating",
    "murder":    "end",
    "murders":   "ends",
    "murdered":  "ended",
    "dead":      "gone",
    "death":     "loss",
    "suicide":   "self-harm incident",
    "bomb":      "explosive",
    "weapon":    "device",
    "weapons":   "devices",
    "shoot":     "photograph",  # context: cameras
    "shooting":  "photographing",
    "shot":      "captured",
    "gun":       "tool",
    "guns":      "tools",
    "knife":     "blade",
    # Sensitive topics
    "terrorist":  "extremist",
    "terrorism":  "extremism",
    "racist":     "biased",
    "racism":     "systemic bias",
    "abuse":      "mistreatment",
    # Drugs (non-clinical)
    "cocaine":    "a controlled substance",
    "heroin":     "an opioid",
    "marijuana":  "cannabis",
    "meth":       "a stimulant",
    "overdose":   "overconsumption",
    # Finance/scam
    "scam":       "scheme",
    "scams":      "schemes",
    "fraud":      "misconduct",
    # Sexual
    "sex":        "relationships",
    "sexual":     "intimate",
}

# High-value safe content signals (boost score)
_BRAND_SAFE_SIGNALS = [
    "how to", "tutorial", "explained", "guide", "tips", "review",
    "best", "top", "compare", "vs", "learn", "understand", "science",
    "technology", "innovation", "research", "analysis", "future",
]


class AdRevenueOptimizer:
    """
    Scores scripts for advertiser friendliness, recommends optimal duration
    for maximum mid-roll slots, places natural ad breaks, and auto-replaces
    demonetization-risk words.
    """

    def score_advertiser_friendly(self, script: Dict) -> Dict:
        """
        Returns a 0-100 advertiser-friendly score + breakdown.
        100 = maximum ad revenue potential; below 60 = limited ads risk.
        """
        full_text = self._full_text(script)
        lower     = full_text.lower()
        score     = 100.0
        issues    = []

        # Penalise each risk word found (max -3 per unique word)
        found_risks = []
        for word in _DEMONETIZATION_RISKS:
            pattern = rf"\b{re.escape(word)}\b"
            count   = len(re.findall(pattern, lower))
            if count:
                penalty = min(count * 2, 6)
                score  -= penalty
                found_risks.append(word)
                issues.append(f"'{word}' × {count} (−{penalty}pts)")

        # Reward brand-safe content signals (+2 each, cap at +20)
        safe_hits = sum(1 for s in _BRAND_SAFE_SIGNALS if s in lower)
        bonus     = min(safe_hits * 2, 20)
        score    += bonus

        # Duration sweet spot (8-20 min = best RPM)
        dur = self._estimate_duration(script)
        if 480 <= dur <= 1200:
            pass                # no adjustment
        elif dur < 480:
            score -= 10
            issues.append(f"Under 8 min — no mid-roll ads (−10pts)")
        else:
            score -= 5
            issues.append(f"Over 20 min — retention risk (−5pts)")

        score = max(0.0, min(100.0, score))
        return {
            "score":           round(score, 1),
            "grade":           "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D",
            "risk_words":      found_risks,
            "brand_safe_hits": safe_hits,
            "issues":          issues,
            "mid_roll_slots":  _midroll_slots(dur),
            "duration_s":      dur,
        }

    def optimize_length_for_midrolls(self, current_duration_s: float,
                                      target_slots: int = 3) -> Dict:
        """
        Recommend the minimum video duration to hit target mid-roll slot count.
        Returns recommended_duration, slot_count, ad_timestamps, revenue_uplift_pct.
        """
        # Duration needed for target_slots: 480 + (target_slots - 1) * 120
        min_duration = 480 + (target_slots - 1) * 120
        current_slots = _midroll_slots(current_duration_s)

        if current_slots >= target_slots:
            rec_duration = current_duration_s
        else:
            # Round up to nearest clean minute above minimum
            rec_duration = math.ceil(min_duration / 60) * 60

        rec_slots = _midroll_slots(rec_duration)
        timestamps = _midroll_timestamps(rec_duration)

        # Revenue uplift: each additional mid-roll ≈ +25-40% ad revenue
        extra_slots    = max(0, rec_slots - current_slots)
        uplift_pct     = extra_slots * 30   # conservative 30% per slot

        return {
            "current_duration_s":   current_duration_s,
            "current_slots":        current_slots,
            "recommended_duration_s": rec_duration,
            "recommended_duration_min": round(rec_duration / 60, 1),
            "slot_count":           rec_slots,
            "ad_timestamps_s":      timestamps,
            "revenue_uplift_pct":   uplift_pct,
        }

    def find_natural_ad_breaks(self, script: Dict) -> List[Dict]:
        """
        Find natural pauses (scene transitions) closest to each mid-roll timestamp.
        Returns list of {target_s, actual_s, scene_id, distance_s}.
        """
        dur        = self._estimate_duration(script)
        targets    = _midroll_timestamps(dur)
        scenes     = script.get("scenes", [])
        if not scenes or not targets:
            return []

        # Build cumulative time per scene boundary
        boundaries: List[Tuple[float, int]] = []   # (time, scene_id)
        cumulative = 0.0
        for scene in scenes:
            d = scene.get("duration_s") or scene.get("duration") or 30
            cumulative += d
            boundaries.append((cumulative, scene.get("id", 0)))

        result = []
        for target in targets:
            best  = min(boundaries, key=lambda b: abs(b[0] - target))
            result.append({
                "target_s":   target,
                "actual_s":   best[0],
                "scene_id":   best[1],
                "distance_s": round(abs(best[0] - target), 1),
            })
        return result

    def replace_risky_words(self, text: str,
                             context_aware: bool = True) -> Tuple[str, List[Dict]]:
        """
        Replace demonetization-risk words in text.
        Returns (cleaned_text, list_of_replacements).
        context_aware: preserve 'shot' in photography/camera context.
        """
        replacements: List[Dict] = []
        result = text

        for word, replacement in _DEMONETIZATION_RISKS.items():
            pattern = rf"\b({re.escape(word)})\b"
            matches = list(re.finditer(pattern, result, re.IGNORECASE))
            if not matches:
                continue

            if context_aware and word in ("shoot", "shooting", "shot", "gun", "guns"):
                camera_ctx = re.search(
                    r"\b(camera|photo|footage|film|capture|record|lens)\b",
                    result, re.IGNORECASE,
                )
                if camera_ctx:
                    continue

            for m in reversed(matches):   # reverse to preserve indices
                original  = m.group(1)
                # Preserve original capitalisation
                rep = (replacement.capitalize() if original[0].isupper()
                       else replacement)
                result = result[:m.start()] + rep + result[m.end():]
                replacements.append({
                    "original":    original,
                    "replacement": rep,
                    "position":    m.start(),
                })

        return result, replacements

    def optimize_script(self, script: Dict) -> Dict:
        """
        Full ad-revenue optimization pass on a script:
        1. Score advertiser friendliness
        2. Replace risky words in all text fields
        3. Annotate scenes with natural ad-break markers
        4. Add duration recommendation
        """
        score_result = self.score_advertiser_friendly(script)
        all_replacements: List[Dict] = []

        # Replace in hook
        if script.get("hook"):
            script["hook"], reps = self.replace_risky_words(script["hook"])
            all_replacements.extend(reps)

        # Replace in outro
        if script.get("outro"):
            script["outro"], reps = self.replace_risky_words(script["outro"])
            all_replacements.extend(reps)

        # Replace in every scene
        for scene in script.get("scenes", []):
            for field in ("narration", "voiceover", "text", "text_overlay"):
                if scene.get(field):
                    scene[field], reps = self.replace_risky_words(scene[field])
                    all_replacements.extend(reps)

        # Mark natural ad break scenes
        ad_breaks = self.find_natural_ad_breaks(script)
        ad_scene_ids = {b["scene_id"] for b in ad_breaks}
        for scene in script.get("scenes", []):
            scene["ad_break"] = scene.get("id") in ad_scene_ids

        # Attach optimization metadata
        script["ad_optimization"] = {
            "advertiser_score":  score_result["score"],
            "grade":             score_result["grade"],
            "risk_words_fixed":  len(all_replacements),
            "replacements":      all_replacements[:20],  # cap log size
            "mid_roll_slots":    score_result["mid_roll_slots"],
            "ad_breaks":         ad_breaks,
        }

        log.info(f"[AdOpt] Score={score_result['score']:.0f} ({score_result['grade']}) "
                 f"slots={score_result['mid_roll_slots']} "
                 f"fixed={len(all_replacements)} risk words")
        return script

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _full_text(self, script: Dict) -> str:
        parts = [script.get("hook", ""), script.get("outro", "")]
        for s in script.get("scenes", []):
            for f in ("narration", "voiceover", "text", "text_overlay"):
                if s.get(f):
                    parts.append(s[f])
        return " ".join(p for p in parts if p)

    def _estimate_duration(self, script: Dict) -> float:
        total = sum(
            s.get("duration_s") or s.get("duration") or 30
            for s in script.get("scenes", [])
        )
        return total or script.get("total_duration", script.get("duration_s", 600))


# ══════════════════════════════════════════════════════════════════════════════
# SponsorshipAutomation
# ══════════════════════════════════════════════════════════════════════════════

# Known sponsor CPM ranges by niche (USD per 1 000 views)
_SPONSOR_CPM = {
    "technology":  25.0,
    "finance":     40.0,
    "software":    30.0,
    "energy":      20.0,
    "health":      18.0,
    "education":   15.0,
    "science":     18.0,
    "gaming":      12.0,
    "default":     20.0,
}

# Seed list of brands that actively sponsor YouTube tech/science/energy channels
_NICHE_SPONSOR_SEEDS = {
    "technology": [
        {"name": "NordVPN",     "email": "creators@nordvpn.com",   "category": "privacy"},
        {"name": "Skillshare",  "email": "partnerships@skillshare.com", "category": "education"},
        {"name": "Brilliant",   "email": "creators@brilliant.org", "category": "education"},
        {"name": "Squarespace", "email": "creators@squarespace.com", "category": "website"},
        {"name": "Hover",       "email": "sponsorships@hover.com", "category": "domain"},
        {"name": "Audible",     "email": "contentpartnerships@amazon.com", "category": "audio"},
        {"name": "ExpressVPN",  "email": "influencers@expressvpn.com", "category": "privacy"},
        {"name": "LastPass",    "email": "partnerships@lastpass.com", "category": "security"},
    ],
    "finance": [
        {"name": "Acorns",      "email": "partners@acorns.com",    "category": "investing"},
        {"name": "Robinhood",   "email": "influencers@robinhood.com", "category": "investing"},
        {"name": "Personal Capital", "email": "partnerships@personalcapital.com", "category": "finance"},
        {"name": "Betterment",  "email": "partnerships@betterment.com", "category": "investing"},
    ],
    "energy": [
        {"name": "Jackery",     "email": "marketing@jackery.com",  "category": "power"},
        {"name": "EcoFlow",     "email": "influencers@ecoflow.com", "category": "power"},
        {"name": "Bluetti",     "email": "marketing@bluettipower.com", "category": "power"},
        {"name": "SunPower",    "email": "partnerships@sunpower.com", "category": "solar"},
    ],
    "education": [
        {"name": "Brilliant",   "email": "creators@brilliant.org", "category": "learning"},
        {"name": "Coursera",    "email": "partnerships@coursera.org", "category": "courses"},
        {"name": "MasterClass", "email": "partnerships@masterclass.com", "category": "courses"},
        {"name": "Duolingo",    "email": "content@duolingo.com",   "category": "language"},
    ],
}

_OUTREACH_TEMPLATE = """Subject: Partnership Opportunity — {channel_name} × {brand_name}

Hi {brand_name} Team,

I'm the creator behind {channel_name}, a {niche} channel with {subscriber_count} subscribers
and an average of {avg_views:,} views per video.

Our audience is {audience_description} — exactly the demographic that converts
for {brand_category} products.

Our recent performance:
• Average CTR: {avg_ctr:.1f}%
• Average retention: {avg_avd:.0f}%
• Engagement rate: {engagement_rate:.1f}%
• Estimated monthly reach: {monthly_reach:,} views

I'd love to explore a sponsored integration. Based on our niche and audience quality,
I'm proposing {deal_type} at ${deal_value:,.0f} per video.

The integration would be a 60–90 second mid-roll segment at the 35–40% mark
(proven highest completion rate). I always ensure sponsor reads are:
  • On-brand and audience-relevant
  • Clearly disclosed per FTC guidelines
  • Delivered authentically in my own voice

Would you be open to a quick call this week?

Best,
{creator_name}
{channel_url}
{media_kit_note}"""


class SponsorshipAutomation:
    """
    Discovers brands sponsoring channels in your niche, generates outreach emails,
    tracks deal pipeline, and auto-inserts sponsor segments at the proven 35-40% mark.
    """

    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()
        try:
            import requests
            sess = requests.Session()
            sess.verify = False
            self._sess = sess
        except ImportError:
            self._sess = None

    def discover_niche_sponsors(self, niche: str = "technology",
                                 competitor_channel_ids: List[str] = None) -> List[Dict]:
        """
        Build sponsor prospect list by:
        1. Scanning competitor video descriptions for 'sponsored by' mentions
        2. Returning the seed list for the niche
        """
        seeds = _NICHE_SPONSOR_SEEDS.get(niche.lower(), _NICHE_SPONSOR_SEEDS["technology"])
        discovered: List[Dict] = list(seeds)

        if competitor_channel_ids and self._sess:
            scraped = self._scrape_descriptions_for_sponsors(competitor_channel_ids[:5])
            # Merge scraped with seeds (avoid duplicates by name)
            known_names = {s["name"].lower() for s in discovered}
            for b in scraped:
                if b["name"].lower() not in known_names:
                    discovered.append(b)
                    known_names.add(b["name"].lower())

        log.info(f"[Sponsorship] Found {len(discovered)} sponsor prospects for '{niche}'")
        return discovered

    def _scrape_descriptions_for_sponsors(self,
                                            channel_ids: List[str]) -> List[Dict]:
        """Scan competitor RSS feeds for 'sponsored by' / 'ad' disclosure mentions."""
        found: List[Dict] = []
        try:
            import feedparser
        except ImportError:
            return found

        sponsor_pattern = re.compile(
            r"(?:sponsored by|partnered with|this video is sponsored by|"
            r"brought to you by|use code [A-Z]+)\s+([A-Z][a-zA-Z0-9\s]+?)(?:\.|,|\n|!|\))",
            re.IGNORECASE,
        )
        for cid in channel_ids:
            try:
                url  = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    desc = entry.get("summary", "") + entry.get("content", [{}])[0].get("value", "")
                    for m in sponsor_pattern.finditer(desc):
                        name = m.group(1).strip()[:40]
                        if len(name) > 2:
                            found.append({"name": name, "email": "", "category": "unknown"})
                time.sleep(0.5)
            except Exception:
                continue
        return found

    def build_outreach_email(self, brand: Dict,
                              channel_stats: Dict,
                              niche: str = "technology") -> str:
        """Generate a personalised outreach email. Uses Claude if available."""
        try:
            return self._ai_outreach_email(brand, channel_stats, niche)
        except Exception:
            return self._template_outreach_email(brand, channel_stats, niche)

    def _template_outreach_email(self, brand: Dict, stats: Dict, niche: str) -> str:
        avg_views  = int(stats.get("avg_views", 5000))
        subs       = int(stats.get("subscribers", 10000))
        deal_value = self.estimate_deal_value(avg_views, niche)["deal_value"]

        return _OUTREACH_TEMPLATE.format(
            channel_name      = stats.get("channel_name", "Our Channel"),
            brand_name        = brand.get("name", "Your Brand"),
            niche             = niche,
            subscriber_count  = f"{subs:,}",
            avg_views         = avg_views,
            audience_description = f"highly engaged {niche} enthusiasts",
            brand_category    = brand.get("category", "tech"),
            avg_ctr           = stats.get("avg_ctr", 4.5),
            avg_avd           = stats.get("avg_avd_pct", 45.0),
            engagement_rate   = stats.get("engagement_rate", 3.5),
            monthly_reach     = avg_views * stats.get("videos_per_month", 4),
            deal_type         = "a flat-fee integration",
            deal_value        = deal_value,
            creator_name      = stats.get("creator_name", "Creator"),
            channel_url       = stats.get("channel_url", ""),
            media_kit_note    = "Media kit available on request.",
        )

    def _ai_outreach_email(self, brand: Dict, stats: Dict, niche: str) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("No API key")
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": (
                    f"Write a short, professional YouTube sponsorship outreach email "
                    f"from a {niche} creator to {brand['name']} ({brand.get('category','tech')}).\n"
                    f"Channel stats: {stats.get('subscribers',10000):,} subs, "
                    f"{stats.get('avg_views',5000):,} avg views, "
                    f"{stats.get('avg_ctr',4.5):.1f}% CTR.\n"
                    f"Proposed rate: ${self.estimate_deal_value(stats.get('avg_views',5000), niche)['deal_value']:,.0f}/video.\n"
                    "Keep it under 250 words. Professional but warm tone."
                ),
            }],
        )
        return msg.content[0].text.strip()

    def estimate_deal_value(self, avg_views: int, niche: str = "technology") -> Dict:
        """
        Estimate sponsorship deal value.
        Industry standard: $20-$50 CPM for tech/education niches.
        """
        cpm        = _SPONSOR_CPM.get(niche.lower(), _SPONSOR_CPM["default"])
        deal_value = (avg_views / 1000) * cpm
        range_low  = deal_value * 0.7
        range_high = deal_value * 1.4

        return {
            "avg_views":   avg_views,
            "niche_cpm":   cpm,
            "deal_value":  round(deal_value, 2),
            "range_low":   round(range_low, 2),
            "range_high":  round(range_high, 2),
            "niche":       niche,
        }

    def insert_sponsor_segment(self, script: Dict,
                                brand: Dict,
                                position_pct: float = 0.375) -> Dict:
        """
        Insert a sponsor segment scene at position_pct (35-40%) of the video.
        The proven placement for highest viewer completion of the sponsor read.
        """
        scenes   = script.get("scenes", [])
        if not scenes:
            return script

        total_dur = sum(
            s.get("duration_s") or s.get("duration") or 30 for s in scenes
        )
        target_s  = total_dur * position_pct

        # Find insertion point (between scenes)
        cumulative = 0.0
        insert_idx = len(scenes)
        for i, scene in enumerate(scenes):
            d = scene.get("duration_s") or scene.get("duration") or 30
            if cumulative + d > target_s:
                insert_idx = i
                break
            cumulative += d

        brand_name = brand.get("name", "our sponsor")
        promo_code = brand.get("promo_code", "CREATOR")
        brand_url  = brand.get("url", "")

        sponsor_scene = {
            "id":           "sponsor",
            "section":      "sponsor",
            "duration_s":   75,
            "duration":     75,
            "narration":    (
                f"Quick word from today's sponsor, {brand_name}. "
                f"{brand.get('pitch', f'{brand_name} helps creators like you do more with less.')} "
                f"Use code {promo_code} for an exclusive discount — link in description. "
                f"And now, back to the video."
            ),
            "voiceover":    f"[SPONSOR READ: {brand_name} — 60-90 seconds]",
            "visual":       f"Sponsor card: {brand_name} logo + offer",
            "text_overlay": f"Use code {promo_code}",
            "ad_break":     False,
            "is_sponsor":   True,
            "sponsor_name": brand_name,
            "sponsor_url":  brand_url,
            "disclosure":   "#ad #sponsored",
        }

        new_scenes = scenes[:insert_idx] + [sponsor_scene] + scenes[insert_idx:]
        script["scenes"]           = new_scenes
        script["has_sponsor"]      = True
        script["sponsor_name"]     = brand_name
        script["sponsor_position"] = round(position_pct * 100, 1)

        log.info(f"[Sponsor] Inserted {brand_name} segment at {position_pct:.0%} "
                 f"(scene index {insert_idx}/{len(scenes)})")
        return script


# ══════════════════════════════════════════════════════════════════════════════
# AffiliateLinkEngine
# ══════════════════════════════════════════════════════════════════════════════

# Affiliate programs by product category
_AFFILIATE_PROGRAMS: Dict[str, Dict] = {
    "amazon": {
        "name":            "Amazon Associates",
        "search_base":     "https://www.amazon.com/s?k=",
        "tag_param":       "&tag=YOURTAG-20",
        "commission_pct":  4.0,
        "cookie_days":     1,
    },
    "impact": {
        "name":            "Impact Radius",
        "join_url":        "https://impact.com/publishers",
        "commission_pct":  8.0,
        "cookie_days":     30,
        "top_brands":      ["Squarespace", "Canva", "NordVPN", "Skillshare"],
    },
    "shareasale": {
        "name":            "ShareASale",
        "join_url":        "https://www.shareasale.com/join",
        "commission_pct":  6.0,
        "cookie_days":     30,
        "top_brands":      ["WP Engine", "Tailwind", "BigCommerce"],
    },
}

# Product keyword → program mapping
_PRODUCT_PROGRAM_MAP: Dict[str, str] = {
    "book":           "amazon",
    "books":          "amazon",
    "camera":         "amazon",
    "microphone":     "amazon",
    "mic":            "amazon",
    "laptop":         "amazon",
    "keyboard":       "amazon",
    "monitor":        "amazon",
    "headphones":     "amazon",
    "vpn":            "impact",
    "website builder":"impact",
    "domain":         "impact",
    "hosting":        "impact",
    "squarespace":    "impact",
    "canva":          "impact",
    "course":         "impact",
    "software":       "impact",
    "plugin":         "shareasale",
    "theme":          "shareasale",
    "wordpress":      "shareasale",
    "stock photo":    "shareasale",
    "font":           "shareasale",
    "tool":           "amazon",
    "app":            "impact",
    "solar panel":    "amazon",
    "battery":        "amazon",
    "charger":        "amazon",
}

# High-value product noun phrases to detect in script
_PRODUCT_PATTERNS = [
    r"\b(solar panel[s]?)\b",
    r"\b(power station[s]?)\b",
    r"\b(portable battery|portable charger)\b",
    r"\b(vpn service[s]?|VPN)\b",
    r"\b(course[s]?|online course[s]?)\b",
    r"\b(book[s]?)\b",
    r"\b(camera[s]?)\b",
    r"\b(microphone[s]?|mic)\b",
    r"\b(laptop[s]?)\b",
    r"\b(software)\b",
    r"\b(app[s]?)\b",
    r"\b(tool[s]?)\b",
    r"\b(website|website builder)\b",
    r"\b(hosting)\b",
    r"\b(plugin[s]?)\b",
    r"\b(keyboard[s]?)\b",
    r"\b(monitor[s]?)\b",
    r"\b(headphone[s]?|earbuds?)\b",
]


class AffiliateLinkEngine:
    """
    Detects product mentions in scripts, matches them to affiliate programs,
    formats description sections, and tracks A/B test placements.
    """

    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    def detect_products(self, script: Dict) -> List[str]:
        """Extract product mentions from the full script text."""
        full_text = " ".join([
            script.get("hook", ""),
            script.get("outro", ""),
            *[s.get("narration", "") + " " + s.get("voiceover", "")
              for s in script.get("scenes", [])],
        ])
        found: set = set()
        for pattern in _PRODUCT_PATTERNS:
            for m in re.finditer(pattern, full_text, re.IGNORECASE):
                found.add(m.group(1).lower().strip())
        return sorted(found)

    def find_affiliate_programs(self, products: List[str]) -> Dict[str, Dict]:
        """
        Map detected products to affiliate programs.
        Returns {product: {program_name, join_url, estimated_commission, search_url}}
        """
        result: Dict[str, Dict] = {}
        for product in products:
            prog_key = None
            for keyword, pk in _PRODUCT_PROGRAM_MAP.items():
                if keyword in product.lower():
                    prog_key = pk
                    break
            if not prog_key:
                prog_key = "amazon"   # default fallback
            prog = _AFFILIATE_PROGRAMS[prog_key]
            search_query = product.replace(" ", "+")
            result[product] = {
                "program":    prog_key,
                "name":       prog["name"],
                "commission": prog["commission_pct"],
                "cookie_days": prog["cookie_days"],
                "search_url": prog.get("search_base", "") + search_query + prog.get("tag_param", ""),
                "join_url":   prog.get("join_url", prog.get("search_base", "")),
            }
        return result

    def format_description_links(self, description: str,
                                  links: Dict[str, Dict],
                                  amazon_tag: str = "") -> str:
        """
        Append a formatted affiliate links section to the video description.
        """
        if not links:
            return description
        lines = ["\n\n── LINKS & RESOURCES ──────────────────────────────────",
                 "(*affiliate links — I earn a small commission at no cost to you)"]
        for product, data in links.items():
            url = data["search_url"]
            if "amazon" in data["program"] and amazon_tag:
                url = url.replace("YOURTAG", amazon_tag)
            lines.append(f"• {product.title()}: {url}")
        lines.append("────────────────────────────────────────────────────")
        return description + "\n".join(lines)

    def create_pinned_comment(self, links: Dict[str, Dict],
                               amazon_tag: str = "") -> str:
        """Generate a pinned-comment variant for A/B testing vs description links."""
        if not links:
            return ""
        parts = ["📌 All links from this video:\n"]
        for product, data in links.items():
            url = data["search_url"]
            if "amazon" in data["program"] and amazon_tag:
                url = url.replace("YOURTAG", amazon_tag)
            parts.append(f"• {product.title()} → {url}")
        parts.append("\n*affiliate links — thanks for supporting the channel!")
        return "\n".join(parts)

    def log_placement(self, video_id: int, youtube_id: str,
                       placement: str, links: Dict[str, Dict]) -> None:
        """Record affiliate link placement to memory for A/B tracking."""
        for product, data in links.items():
            self.memory.save_affiliate_link({
                "video_db_id": video_id,
                "youtube_id":  youtube_id,
                "product":     product,
                "program":     data.get("program", ""),
                "url":         data.get("search_url", ""),
                "placement":   placement,
            })

    def estimate_monthly_affiliate_revenue(self,
                                            monthly_views: int,
                                            products_per_video: int = 3,
                                            click_through_rate: float = 0.02,
                                            conversion_rate: float = 0.04,
                                            avg_order: float = 50.0,
                                            commission_pct: float = 4.0) -> Dict:
        """Forecast monthly affiliate revenue from current view rate."""
        clicks      = monthly_views * click_through_rate * products_per_video
        conversions = clicks * conversion_rate
        revenue     = conversions * avg_order * (commission_pct / 100)
        return {
            "monthly_views":  monthly_views,
            "estimated_clicks": int(clicks),
            "conversions":    int(conversions),
            "revenue":        round(revenue, 2),
        }


# ══════════════════════════════════════════════════════════════════════════════
# MerchandiseTrigger
# ══════════════════════════════════════════════════════════════════════════════

_MERCH_MILESTONES = [1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000]

_MERCH_PRODUCT_TYPES = [
    "t-shirt", "hoodie", "mug", "tote bag", "phone case",
    "poster", "notebook", "hat", "sticker pack", "canvas print",
]


class MerchandiseTrigger:
    """
    Monitors subscriber milestones and generates merch launch plans
    with Printful / Printify integration stubs.
    """

    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    def check_milestones(self, current_subs: int,
                          previous_subs: int = 0) -> List[Dict]:
        """Return list of milestone dicts for newly crossed thresholds."""
        crossed = []
        for milestone in _MERCH_MILESTONES:
            if previous_subs < milestone <= current_subs:
                crossed.append({
                    "milestone":    milestone,
                    "label":        f"{milestone:,} subscribers",
                    "action":       "Launch merch collection",
                    "urgency":      "high" if milestone >= 10_000 else "medium",
                })
        return crossed

    def generate_merch_ideas(self, channel_name: str,
                              niche: str,
                              catchphrases: List[str] = None,
                              subscriber_count: int = 0) -> List[Dict]:
        """Generate merch product ideas using Claude or rule-based fallback."""
        try:
            return self._ai_merch_ideas(channel_name, niche, catchphrases, subscriber_count)
        except Exception:
            return self._fallback_merch_ideas(channel_name, niche, catchphrases)

    def _ai_merch_ideas(self, channel_name: str, niche: str,
                         catchphrases: List[str],
                         subscriber_count: int) -> List[Dict]:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("No API key")
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        phrases = ", ".join(catchphrases[:5]) if catchphrases else "none"
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate 5 merchandise product ideas for a YouTube channel called "
                    f"'{channel_name}' in the {niche} niche with {subscriber_count:,} subscribers.\n"
                    f"Channel catchphrases: {phrases}\n"
                    "For each idea: product_type, slogan, design_brief, estimated_price_usd.\n"
                    "Return JSON array."
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        m   = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            ideas = json.loads(m.group())
            return ideas[:5]
        raise ValueError("No JSON array in response")

    def _fallback_merch_ideas(self, channel_name: str, niche: str,
                               catchphrases: List[str] = None) -> List[Dict]:
        """Rule-based merch ideas when Claude is unavailable."""
        phrase = (catchphrases[0] if catchphrases else f"I ♥ {niche.title()}")
        ideas  = []
        for product in _MERCH_PRODUCT_TYPES[:5]:
            ideas.append({
                "product_type":       product,
                "slogan":             phrase,
                "design_brief":       f"Minimalist design with '{phrase}' text, channel colour scheme",
                "estimated_price_usd": {"t-shirt": 25, "hoodie": 45, "mug": 18,
                                         "tote bag": 20, "phone case": 22,
                                         "poster": 18, "notebook": 15, "hat": 28,
                                         "sticker pack": 8, "canvas print": 40,
                                        }.get(product, 20),
                "platform":           "printful",
            })
        return ideas

    def create_printful_product(self, idea: Dict,
                                 printful_api_key: str = "") -> Dict:
        """
        Stub for Printful API product creation.
        Requires PRINTFUL_API_KEY env variable.
        Returns mock response when key is missing.
        """
        key = printful_api_key or os.getenv("PRINTFUL_API_KEY", "")
        if not key:
            return {
                "status":  "mock",
                "message": "Set PRINTFUL_API_KEY to create real products",
                "product": idea,
                "store_url": "https://www.printful.com/dashboard",
            }
        try:
            import requests
            r = requests.post(
                "https://api.printful.com/store/products",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={
                    "sync_product": {
                        "name":  f"{idea.get('slogan','')} — {idea.get('product_type','')}",
                        "thumbnail": "",
                    },
                    "sync_variants": [],
                },
                timeout=20,
            )
            return r.json() if r.ok else {"status": "error", "code": r.status_code}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def create_printify_product(self, idea: Dict,
                                 printify_api_key: str = "") -> Dict:
        """Stub for Printify API product creation."""
        key = printify_api_key or os.getenv("PRINTIFY_API_KEY", "")
        if not key:
            return {
                "status":  "mock",
                "message": "Set PRINTIFY_API_KEY to create real products",
                "product": idea,
                "store_url": "https://printify.com/app/dashboard",
            }
        try:
            import requests
            r = requests.post(
                "https://api.printify.com/v1/shops/products.json",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"title": idea.get("slogan", ""), "variants": []},
                timeout=20,
            )
            return r.json() if r.ok else {"status": "error", "code": r.status_code}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# RevenueDashboard
# ══════════════════════════════════════════════════════════════════════════════

class RevenueDashboard:
    """
    Tracks RPM trends, compares against niche benchmarks,
    forecasts monthly revenue, and alerts on >20% drops.
    """

    DROP_THRESHOLD = 0.20   # alert when any stream drops > 20%

    def __init__(self, memory=None, niche: str = "technology"):
        from core.memory import get_memory
        self.memory = memory or get_memory()
        self.niche  = niche

    def weekly_rpm_trend(self, weeks: int = 4) -> List[Dict]:
        """
        Compute week-by-week RPM from video analytics in memory.
        Returns list of {week, avg_rpm, total_views, video_count}.
        """
        videos = self.memory.get_video_stats(n=200)
        by_week: Dict[int, Dict[str, list]] = defaultdict(lambda: {"rpm": [], "views": []})

        for v in videos:
            ts  = v.get("created_at", "")
            rpm = v.get("rpm", 0) or 0
            views = v.get("views", 0) or 0
            if not ts or not rpm:
                continue
            try:
                dt   = datetime.fromisoformat(ts)
                week = (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).days // 7
                if week < weeks:
                    by_week[week]["rpm"].append(rpm)
                    by_week[week]["views"].append(views)
            except Exception:
                continue

        result = []
        for w in range(weeks):
            data = by_week.get(w, {"rpm": [], "views": []})
            result.append({
                "week":         w,
                "label":        "This week" if w == 0 else f"{w}w ago",
                "avg_rpm":      round(sum(data["rpm"]) / max(len(data["rpm"]), 1), 2),
                "total_views":  sum(data["views"]),
                "video_count":  len(data["rpm"]),
            })
        return result

    def niche_rpm_comparison(self) -> Dict:
        """Compare channel's current RPM against niche average."""
        trend   = self.weekly_rpm_trend(weeks=2)
        ch_rpm  = trend[0]["avg_rpm"] if trend else 0.0
        niche_avg = NICHE_RPM.get(self.niche.lower(), NICHE_RPM["default"])
        pct_diff  = ((ch_rpm - niche_avg) / niche_avg * 100) if niche_avg else 0.0

        return {
            "channel_rpm":    round(ch_rpm, 2),
            "niche_avg_rpm":  niche_avg,
            "niche":          self.niche,
            "pct_vs_niche":   round(pct_diff, 1),
            "status":         "above_average" if pct_diff > 0 else "below_average",
        }

    def forecast_monthly_revenue(self, months_ahead: int = 3) -> Dict:
        """
        Simple linear regression on weekly RPM + views to project monthly revenue.
        """
        trend         = self.weekly_rpm_trend(weeks=8)
        valid_weeks   = [w for w in trend if w["video_count"] > 0]
        if len(valid_weeks) < 2:
            summary     = self.memory.performance_summary()
            est_monthly = (summary.get("avg_rpm", 0) * 20_000 / 1000)
            return {
                "monthly_forecast": [round(est_monthly * (1 + 0.05 * m), 2)
                                      for m in range(1, months_ahead + 1)],
                "confidence": "low",
                "basis": "insufficient_data",
            }

        # Simple linear trend in RPM × views
        revenues = [w["avg_rpm"] * w["total_views"] / 1000 for w in valid_weeks]
        n        = len(revenues)
        x_mean   = (n - 1) / 2
        y_mean   = sum(revenues) / n
        slope    = sum((i - x_mean) * (revenues[i] - y_mean) for i in range(n)) / \
                   max(sum((i - x_mean) ** 2 for i in range(n)), 1)

        # Project: assume 4 videos/month
        current_monthly = revenues[0] * 4
        forecasts = []
        for m in range(1, months_ahead + 1):
            projected = max(current_monthly + slope * m * 4, 0)
            forecasts.append(round(projected, 2))

        return {
            "current_monthly_estimate": round(current_monthly, 2),
            "monthly_forecast":         forecasts,
            "weeks_slope":              round(slope, 4),
            "confidence":               "medium" if n >= 4 else "low",
            "basis":                    f"{n} weeks of data",
        }

    def check_alerts(self) -> List[Dict]:
        """
        Compare current week vs previous week across revenue streams.
        Return alerts for any stream that dropped > 20%.
        """
        trend  = self.weekly_rpm_trend(weeks=2)
        alerts: List[Dict] = []

        if len(trend) < 2:
            return alerts

        this_week = trend[0]
        last_week = trend[1]

        # RPM drop
        if last_week["avg_rpm"] > 0:
            rpm_delta = (this_week["avg_rpm"] - last_week["avg_rpm"]) / last_week["avg_rpm"]
            if rpm_delta < -self.DROP_THRESHOLD:
                alerts.append({
                    "stream":   "ad_revenue_rpm",
                    "drop_pct": round(-rpm_delta * 100, 1),
                    "this":     this_week["avg_rpm"],
                    "prev":     last_week["avg_rpm"],
                    "severity": "high" if -rpm_delta > 0.40 else "medium",
                    "message":  f"RPM dropped {-rpm_delta:.0%} this week "
                                f"(${this_week['avg_rpm']:.2f} vs ${last_week['avg_rpm']:.2f})",
                })

        # Views drop
        if last_week["total_views"] > 0:
            view_delta = (this_week["total_views"] - last_week["total_views"]) / last_week["total_views"]
            if view_delta < -self.DROP_THRESHOLD:
                alerts.append({
                    "stream":   "views",
                    "drop_pct": round(-view_delta * 100, 1),
                    "this":     this_week["total_views"],
                    "prev":     last_week["total_views"],
                    "severity": "high" if -view_delta > 0.40 else "medium",
                    "message":  f"Views dropped {-view_delta:.0%} this week",
                })

        # Check affiliate revenue from memory
        affiliate_alerts = self._check_affiliate_drops()
        alerts.extend(affiliate_alerts)

        for a in alerts:
            log.warning(f"[RevenueDashboard] ALERT: {a['message']}")
        return alerts

    def _check_affiliate_drops(self) -> List[Dict]:
        """Compare this week vs last week affiliate revenue from memory."""
        try:
            stats = self.memory.affiliate_revenue_weekly(weeks=2)
        except Exception:
            return []
        alerts = []
        if len(stats) >= 2 and stats[1]["revenue"] > 0:
            delta = (stats[0]["revenue"] - stats[1]["revenue"]) / stats[1]["revenue"]
            if delta < -self.DROP_THRESHOLD:
                alerts.append({
                    "stream":   "affiliate",
                    "drop_pct": round(-delta * 100, 1),
                    "this":     stats[0]["revenue"],
                    "prev":     stats[1]["revenue"],
                    "severity": "medium",
                    "message":  f"Affiliate revenue dropped {-delta:.0%} this week",
                })
        return alerts

    def generate_report(self, include_forecast: bool = True) -> str:
        """Generate a full plain-text revenue dashboard report."""
        rpm_trend  = self.weekly_rpm_trend(weeks=4)
        niche_cmp  = self.niche_rpm_comparison()
        alerts     = self.check_alerts()
        forecast   = self.forecast_monthly_revenue(months_ahead=3) if include_forecast else {}

        lines = [
            "=" * 60,
            f"  REVENUE DASHBOARD — {datetime.now().strftime('%Y-%m-%d')}",
            "=" * 60,
            "",
            f"  Niche: {self.niche.title()}",
            f"  Channel RPM:   ${niche_cmp['channel_rpm']:.2f}",
            f"  Niche avg RPM: ${niche_cmp['niche_avg_rpm']:.2f}",
            f"  vs Niche:      {niche_cmp['pct_vs_niche']:+.1f}%  ({niche_cmp['status']})",
            "",
            "  WEEKLY RPM TREND",
            "  " + "-" * 44,
        ]
        for w in rpm_trend:
            bar_len = int(w["avg_rpm"] / 0.5)
            bar     = "█" * min(bar_len, 30)
            lines.append(f"  {w['label']:>10}  ${w['avg_rpm']:5.2f}  {bar}  ({w['video_count']} videos)")

        if forecast and forecast.get("monthly_forecast"):
            lines += [
                "",
                "  REVENUE FORECAST",
                "  " + "-" * 44,
            ]
            for i, rev in enumerate(forecast["monthly_forecast"], 1):
                lines.append(f"  Month +{i}: ${rev:,.0f}   (confidence: {forecast['confidence']})")
            if forecast.get("current_monthly_estimate"):
                lines.append(f"  Current est. monthly: ${forecast['current_monthly_estimate']:,.0f}")

        if alerts:
            lines += ["", "  ⚠  ALERTS", "  " + "-" * 44]
            for a in alerts:
                lines.append(f"  [{a['severity'].upper()}] {a['message']}")
        else:
            lines += ["", "  ✓  All revenue streams stable"]

        lines += ["", "=" * 60]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MonetizationEngine — Main orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class MonetizationEngine:
    """
    Single entry point that applies all four monetization layers to a video
    before publishing, and provides the revenue dashboard.
    """

    def __init__(self, memory=None, niche: str = "technology"):
        from core.memory import get_memory
        self.memory    = memory or get_memory()
        self.niche     = niche
        self.ad_opt    = AdRevenueOptimizer()
        self.sponsor   = SponsorshipAutomation(self.memory)
        self.affiliate = AffiliateLinkEngine(self.memory)
        self.merch     = MerchandiseTrigger(self.memory)
        self.dashboard = RevenueDashboard(self.memory, niche)

    def optimize_video(self, script: Dict, metadata: Dict,
                        brand: Dict = None,
                        subscriber_count: int = 0,
                        prev_subscriber_count: int = 0,
                        video_db_id: int = 0,
                        youtube_id: str = "") -> Dict:
        """
        Full monetization pass on a video before publishing:
          1. Ad revenue optimization (score + clean + ad-break placement)
          2. Sponsor segment injection (if brand provided)
          3. Affiliate link detection + description injection
          4. Merch milestone check
          5. Pre-upload revenue event log

        Returns updated (script, metadata, report).
        """
        report: Dict[str, Any] = {"niche": self.niche}

        # 1. Ad revenue optimization
        script   = self.ad_opt.optimize_script(script)
        ad_info  = script.get("ad_optimization", {})
        report["ad"] = {
            "advertiser_score": ad_info.get("advertiser_score"),
            "grade":            ad_info.get("grade"),
            "mid_roll_slots":   ad_info.get("mid_roll_slots"),
            "risk_words_fixed": ad_info.get("risk_words_fixed"),
        }

        # Recommend optimal length
        current_dur = self.ad_opt._estimate_duration(script)
        len_rec     = self.ad_opt.optimize_length_for_midrolls(current_dur, target_slots=3)
        report["ad"]["length_recommendation"] = len_rec

        # 2. Sponsor segment
        if brand:
            script = self.sponsor.insert_sponsor_segment(script, brand)
            deal   = self.sponsor.estimate_deal_value(
                metadata.get("expected_views", 5000), self.niche
            )
            report["sponsor"] = {"brand": brand.get("name"), "deal_estimate": deal}
            self.memory.save_monetization_event({
                "event_type": "sponsor_deal",
                "youtube_id": youtube_id,
                "data":       {"brand": brand.get("name"), **deal},
            })

        # 3. Affiliate links
        products = self.affiliate.detect_products(script)
        links    = self.affiliate.find_affiliate_programs(products)
        if links:
            desc = metadata.get("description", "")
            metadata["description"]         = self.affiliate.format_description_links(desc, links)
            metadata["pinned_comment_draft"] = self.affiliate.create_pinned_comment(links)
            if video_db_id:
                self.affiliate.log_placement(video_db_id, youtube_id, "description", links)
            aff_est = self.affiliate.estimate_monthly_affiliate_revenue(
                monthly_views=metadata.get("expected_views", 5000) * 4
            )
            report["affiliate"] = {
                "products_found": list(links.keys()),
                "programs":       {p: d["program"] for p, d in links.items()},
                "monthly_estimate": aff_est["revenue"],
            }

        # 4. Merch milestones
        milestones = self.merch.check_milestones(subscriber_count, prev_subscriber_count)
        if milestones:
            report["merch_triggers"] = milestones
            self.memory.save_monetization_event({
                "event_type": "merch_milestone",
                "youtube_id": youtube_id,
                "data":       milestones,
            })
            log.info(f"[Monetize] Merch milestones triggered: "
                     f"{[m['label'] for m in milestones]}")

        # 5. Log overall event
        self.memory.save_monetization_event({
            "event_type": "video_optimized",
            "youtube_id": youtube_id,
            "data":       report,
        })

        report["optimized_at"] = datetime.now(timezone.utc).isoformat()
        return script, metadata, report

    def run_revenue_dashboard(self) -> str:
        """Print the full revenue dashboard and return the report string."""
        report = self.dashboard.generate_report()
        print(report)
        alerts = self.dashboard.check_alerts()
        if alerts:
            self.memory.save_monetization_event({
                "event_type": "revenue_alert",
                "data":       alerts,
            })
        return report

    def discover_sponsors(self) -> List[Dict]:
        """Discover current niche sponsors and save prospects to memory."""
        sponsors = self.sponsor.discover_niche_sponsors(self.niche)
        for s in sponsors:
            self.memory.save_sponsorship_prospect(s)
        log.info(f"[Monetize] Saved {len(sponsors)} sponsor prospects")
        return sponsors
