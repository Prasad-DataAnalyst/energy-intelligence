"""
intelligence/algorithm_hacker.py — YouTube Algorithm Reverse-Engineer
======================================================================
Samples what YouTube is actually boosting TODAY, predicts video performance
before publishing, and auto-adjusts strategy when the algorithm shifts.

Components
----------
HomepageSampler         InnerTube calls across 50 varied viewer contexts
TagAnalyzer             Which tags YouTube is actively promoting right now
ShadowRanker            6-signal pre-publish performance predictor
AlgorithmChangeDetector Week-over-week distribution shift + auto-adjuster
AlgorithmHacker         Orchestrator; feeds findings into the pipeline

New memory tables (added via memory.py SCHEMA extension):
  algorithm_snapshots   Daily algorithm state records
  shadow_rankings       Pre-publish predictions vs eventual actuals
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import random
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.algorithm_hacker")

HERE = Path(__file__).parent.parent

# ── Browser / region diversity pool ──────────────────────────────────────────

_USER_AGENTS = [
    # Desktop Chrome (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Desktop Chrome (Mac)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Desktop Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Desktop Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    # Desktop Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Desktop Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Mobile Chrome (Android)
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; OnePlus 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    # Mobile Safari (iOS)
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    # Tablet
    "Mozilla/5.0 (iPad; CPU OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
]

_REGIONS = [
    "US", "GB", "CA", "AU", "IN", "DE", "FR", "BR", "JP", "KR",
    "MX", "ES", "IT", "NL", "SE", "PL", "AR", "ZA", "NG", "SG",
]

_SCREEN_SIZES = [
    (1920, 1080), (1440, 900), (1366, 768), (1280, 720),  # desktop
    (390, 844), (412, 915), (375, 812), (360, 800),        # mobile
]

# ── Duration bucket helpers ────────────────────────────────────────────────────

_DURATION_BUCKETS = [
    (0,    120,   "<2min"),
    (120,  300,   "2-5min"),
    (300,  480,   "5-8min"),
    (480,  720,   "8-12min"),
    (720,  1200,  "12-20min"),
    (1200, None,  "20+min"),
]

_UPLOAD_HOUR_BANDS = [
    (0,  6,   "00-06"),
    (6,  12,  "06-12"),
    (12, 18,  "12-18"),
    (18, 24,  "18-24"),
]


def _duration_bucket(seconds: float) -> str:
    for lo, hi, label in _DURATION_BUCKETS:
        if hi is None or lo <= seconds < hi:
            return label
    return "20+min"


def _hour_band(hour: int) -> str:
    for lo, hi, label in _UPLOAD_HOUR_BANDS:
        if lo <= hour < hi:
            return label
    return "18-24"


def _jsd(p: Dict[str, float], q: Dict[str, float]) -> float:
    """Jensen-Shannon divergence between two probability distributions (0 = identical, 1 = maximally different)."""
    keys = set(p) | set(q)
    p_arr = [p.get(k, 0.0) for k in keys]
    q_arr = [q.get(k, 0.0) for k in keys]

    def _norm(arr):
        s = sum(arr)
        return [x / s for x in arr] if s > 0 else [1 / len(arr)] * len(arr)

    p_n = _norm(p_arr)
    q_n = _norm(q_arr)
    m   = [(pp + qq) / 2 for pp, qq in zip(p_n, q_n)]

    def _kl(a, b):
        return sum(ai * math.log(ai / bi + 1e-12) for ai, bi in zip(a, b) if ai > 0)

    return (_kl(p_n, m) + _kl(q_n, m)) / 2


# ══════════════════════════════════════════════════════════════════════════════
# HomepageSampler
# ══════════════════════════════════════════════════════════════════════════════

class HomepageSampler:
    """
    Makes N InnerTube requests with varied client contexts (country, device,
    UA, visitor token) to sample which videos YouTube is currently surfacing
    to logged-out viewers across different user segments.
    """

    def __init__(self, max_workers: int = 5, request_delay: float = 0.4):
        self.max_workers   = max_workers
        self.request_delay = request_delay

    def _random_visitor_data(self) -> str:
        """Generate a plausible-looking visitorData token (base64, 22-26 chars)."""
        raw = bytes([random.randint(0, 255) for _ in range(16)])
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:24]

    def _make_context(self, seed: int) -> Tuple[Dict, str]:
        """Return (innertube_payload, user_agent) for one sampled session."""
        rng    = random.Random(seed)
        region = rng.choice(_REGIONS)
        ua     = rng.choice(_USER_AGENTS)
        w, h   = rng.choice(_SCREEN_SIZES)
        is_mob = w < 500
        client_name    = "MWEB" if is_mob else "WEB"
        client_version = f"2.2024{rng.randint(1,12):02d}{rng.randint(1,28):02d}.0{rng.randint(0,9)}"

        payload = {
            "browseId": "default",
            "context": {
                "client": {
                    "clientName":         client_name,
                    "clientVersion":      client_version,
                    "hl":                 "en",
                    "gl":                 region,
                    "visitorData":        self._random_visitor_data(),
                    "screenWidthPoints":  w,
                    "screenHeightPoints": h,
                    "utcOffsetMinutes":   rng.choice([-480, -420, -300, -240, 0, 60, 120, 330, 540, 600]),
                },
            },
        }
        return payload, ua

    def _fetch_one(self, seed: int) -> List[Dict]:
        """Fetch one homepage slice; return list of video dicts."""
        try:
            import requests
            payload, ua = self._make_context(seed)
            headers = {
                "Content-Type": "application/json",
                "User-Agent":   ua,
                "X-YouTube-Client-Name":    "1",
                "X-YouTube-Client-Version": payload["context"]["client"]["clientVersion"],
            }
            r = requests.post(
                _INNERTUBE_URL,
                json=payload,
                headers=headers,
                timeout=15,
                verify=False,
            )
            if not r.ok:
                return []
            return self._parse_videos(r.json(), seed)
        except Exception as e:
            log.debug(f"HomepageSampler seed={seed}: {e}")
            return []

    @staticmethod
    def _parse_videos(data: Dict, seed: int) -> List[Dict]:
        """Walk the InnerTube response tree and extract video cards."""
        videos: List[Dict] = []
        region = _REGIONS[seed % len(_REGIONS)]

        def _walk(obj):
            if isinstance(obj, dict):
                vr = obj.get("videoRenderer") or obj.get("compactVideoRenderer")
                if vr:
                    title = (vr.get("title", {}).get("runs", [{}])[0].get("text", "")
                             or vr.get("title", {}).get("simpleText", ""))
                    vid_id = vr.get("videoId", "")
                    # duration string e.g. "12:34"
                    dur_str = (vr.get("lengthText", {}).get("simpleText", "")
                               or vr.get("thumbnailOverlays", [{}])[0]
                                   .get("thumbnailOverlayTimeStatusRenderer", {})
                                   .get("text", {}).get("simpleText", ""))
                    dur_s   = _parse_dur_string(dur_str)
                    if title and vid_id:
                        videos.append({
                            "video_id":   vid_id,
                            "title":      title,
                            "duration_s": dur_s,
                            "is_short":   dur_s <= 60,
                            "region":     region,
                            "sampled_at": datetime.now(timezone.utc).isoformat(),
                        })
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(data)
        return videos[:30]

    def sample(self, n: int = 50) -> List[Dict]:
        """
        Fetch homepage recommendations across N varied contexts.
        Returns deduplicated list of boosted video dicts.
        """
        log.info(f"HomepageSampler: sampling {n} contexts (workers={self.max_workers})")
        all_videos: List[Dict] = []
        seeds = list(range(n))

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._fetch_one, s): s for s in seeds}
            for fut in as_completed(futures):
                vids = fut.result()
                all_videos.extend(vids)
                time.sleep(self.request_delay / self.max_workers)

        # Deduplicate by video_id; count appearance_count as boost signal
        counts: Dict[str, int]  = Counter(v["video_id"] for v in all_videos)
        by_id:  Dict[str, Dict] = {}
        for v in all_videos:
            vid = v["video_id"]
            if vid not in by_id:
                by_id[vid] = {**v, "boost_count": 0}
            by_id[vid]["boost_count"] += 1

        results = sorted(by_id.values(), key=lambda x: x["boost_count"], reverse=True)
        log.info(f"  Sampled {len(all_videos)} cards → {len(results)} unique videos")
        return results


def _parse_dur_string(s: str) -> int:
    """'1:23:45' or '12:34' or '0:45' → seconds."""
    parts = s.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0])
    except (ValueError, IndexError):
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# TagAnalyzer
# ══════════════════════════════════════════════════════════════════════════════

class TagAnalyzer:
    """Extracts the tags / keywords YouTube is actively promoting this week."""

    def __init__(self):
        try:
            import requests
            sess = requests.Session()
            sess.verify = False
            self._sess = sess
        except ImportError:
            self._sess = None

    def fetch_api_tags(self, video_ids: List[str]) -> Dict[str, List[str]]:
        """Use YouTube Data API to fetch tags for a batch of video IDs."""
        api_key = os.getenv("YOUTUBE_DATA_API_KEY", "")
        if not api_key or not self._sess or not video_ids:
            return {}
        result: Dict[str, List[str]] = {}
        for chunk in _chunks(video_ids, 50):
            try:
                r = self._sess.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={
                        "part":  "snippet,statistics,contentDetails",
                        "id":    ",".join(chunk),
                        "key":   api_key,
                    },
                    timeout=20,
                )
                if not r.ok:
                    continue
                for item in r.json().get("items", []):
                    vid  = item["id"]
                    snip = item.get("snippet", {})
                    tags = snip.get("tags", [])
                    dur  = _parse_iso_dur(item.get("contentDetails", {}).get("duration", "PT0S"))
                    result[vid] = {
                        "tags":        tags,
                        "duration_s":  dur,
                        "views":       int(item.get("statistics", {}).get("viewCount", 0)),
                        "category_id": snip.get("categoryId", ""),
                    }
                time.sleep(0.2)
            except Exception as e:
                log.debug(f"TagAnalyzer API error: {e}")
        return result

    def top_tags(self, samples: List[Dict], n: int = 30) -> List[str]:
        """
        Extract and rank the most-promoted tags from homepage sample data.
        Falls back to title keyword extraction when API data is unavailable.
        """
        video_ids = [v["video_id"] for v in samples[:100] if v.get("video_id")]
        api_data  = self.fetch_api_tags(video_ids)

        tag_counts: Counter = Counter()

        if api_data:
            for vid_data in api_data.values():
                if isinstance(vid_data, dict):
                    for tag in vid_data.get("tags", []):
                        tag_counts[tag.lower().strip()] += 1
        else:
            for video in samples:
                title = video.get("title", "")
                words = re.findall(r"\b[a-zA-Z]{4,}\b", title.lower())
                for w in words:
                    if w not in _STOP_WORDS:
                        tag_counts[w] += 1

        return [tag for tag, _ in tag_counts.most_common(n)]

    def shorts_ratio(self, samples: List[Dict]) -> float:
        """Fraction of boosted videos that are Shorts (≤ 60s)."""
        if not samples:
            return 0.0
        shorts = sum(1 for v in samples if v.get("is_short") or v.get("duration_s", 999) <= 60)
        return round(shorts / len(samples), 3)

    def duration_distribution(self, samples: List[Dict]) -> Dict[str, float]:
        """Normalised duration-bucket distribution of boosted videos."""
        counts: Counter = Counter()
        for v in samples:
            counts[_duration_bucket(v.get("duration_s", 0))] += 1
        total = max(sum(counts.values()), 1)
        return {k: round(v / total, 4) for k, v in counts.items()}

    def ctr_sweet_spots(self, samples: List[Dict]) -> Dict:
        """
        Infer CTR sweet spots from title patterns of top-boosted videos.
        Returns: {title_length_bucket, question_ratio, number_ratio, caps_ratio}
        """
        top = sorted(samples, key=lambda x: x.get("boost_count", 0), reverse=True)[:30]
        titles = [v["title"] for v in top if v.get("title")]
        if not titles:
            return {}

        lengths = [len(t.split()) for t in titles]
        avg_len = sum(lengths) / len(lengths)
        q_ratio = sum(1 for t in titles if "?" in t) / len(titles)
        n_ratio = sum(1 for t in titles if re.search(r"\d", t)) / len(titles)
        # Title length sweet spot bucket
        best_bucket = f"{(int(avg_len) // 2) * 2}-{(int(avg_len) // 2) * 2 + 1} words"

        return {
            "avg_title_words":    round(avg_len, 1),
            "title_sweet_spot":   best_bucket,
            "question_ratio":     round(q_ratio, 3),
            "number_ratio":       round(n_ratio, 3),
            "top_titles":         [t[:80] for t in titles[:5]],
        }


_STOP_WORDS = {
    "the", "and", "for", "that", "this", "with", "from", "have", "more",
    "been", "will", "your", "they", "what", "when", "which", "about",
    "also", "some", "than", "then", "into", "were", "their", "there",
    "most", "very", "just", "like", "know", "make", "time", "only",
}


def _chunks(lst: List, size: int):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _parse_iso_dur(s: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if not m:
        return 0
    h, mn, sec = (int(v or 0) for v in m.groups())
    return h * 3600 + mn * 60 + sec


# ══════════════════════════════════════════════════════════════════════════════
# ShadowRanker
# ══════════════════════════════════════════════════════════════════════════════

class ShadowRanker:
    """
    Predicts a video's algorithm performance before publishing.
    Uses 6 weighted signals → composite 0-100 predicted score.
    Gates publishing: only approve if predicted score > channel average.

    Weights
    -------
    psychology_score    0.25  — viewer_psychology engagement score
    trend_alignment     0.20  — title similarity to currently-boosted topics
    duration_fit        0.20  — proximity to current optimal duration
    upload_time         0.15  — quality of planned upload slot
    tag_relevance       0.10  — overlap with trending tags
    historical_style    0.10  — channel's learned best-performing style
    """

    WEIGHTS = {
        "psychology_score":  0.25,
        "trend_alignment":   0.20,
        "duration_fit":      0.20,
        "upload_time":       0.15,
        "tag_relevance":     0.10,
        "historical_style":  0.10,
    }

    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    # ── Individual signal scorers (each returns 0-100) ──────────────────────

    def _sig_psychology(self, script: Dict) -> float:
        """Use pre-computed psychology score, or run scorer now."""
        if "psychology_score" in script:
            return float(script["psychology_score"])
        try:
            from production.viewer_psychology import ViewerPsychology
            return ViewerPsychology().score(script)
        except Exception:
            return 50.0

    def _sig_trend_alignment(self, title: str,
                              boosted_titles: List[str]) -> float:
        """Cosine similarity between this title's words and boosted titles corpus."""
        if not title or not boosted_titles:
            return 50.0
        corpus = " ".join(boosted_titles).lower()
        corpus_words = Counter(re.findall(r"\b[a-z]{3,}\b", corpus))
        title_words  = set(re.findall(r"\b[a-z]{3,}\b", title.lower()))
        if not corpus_words or not title_words:
            return 50.0
        total   = sum(corpus_words.values())
        overlap = sum(corpus_words.get(w, 0) for w in title_words) / total
        return min(overlap * 500, 100.0)

    def _sig_duration_fit(self, duration_s: float,
                           optimal_dist: Dict[str, float]) -> float:
        """How well does this duration match current boosted distribution."""
        if not optimal_dist or not duration_s:
            return 50.0
        bucket = _duration_bucket(duration_s)
        # Score = fraction of boosted videos in this bucket × 100
        frac   = optimal_dist.get(bucket, 0.0)
        # Normalise: ideal bucket = max bucket (full score = 100)
        max_f  = max(optimal_dist.values()) if optimal_dist else 1.0
        return round((frac / max(max_f, 0.01)) * 100, 2)

    def _sig_upload_time(self, upload_hour: int, best_hour: int) -> float:
        """Proximity of planned upload hour to the current best upload hour."""
        diff = abs(upload_hour - best_hour)
        diff = min(diff, 24 - diff)   # wrap around midnight
        # Full score within 2h window; drops linearly to 0 at ±6h
        return max(0.0, 100.0 - diff * (100 / 6))

    def _sig_tag_relevance(self, tags: List[str],
                            trending_tags: List[str]) -> float:
        """Tag overlap ratio."""
        if not tags or not trending_tags:
            return 50.0
        tag_set  = {t.lower().strip() for t in tags}
        trend_s  = {t.lower().strip() for t in trending_tags}
        overlap  = len(tag_set & trend_s)
        return min(overlap * 10, 100.0)

    def _sig_historical_style(self, style: str) -> float:
        """Channel's learned CTR for this style preset."""
        best = self.memory.get_best_config("style_preset")
        if not best:
            return 50.0
        if style == best:
            return 80.0
        # Any other style gets proportional score based on config_performance
        correlations = self.memory.config_correlations()
        for row in correlations:
            if row["config_key"] == "style_preset" and row["config_value"] == style:
                best_ctr = correlations[0]["avg_ctr"] if correlations else 1.0
                return min((row["avg_ctr"] / max(best_ctr, 0.01)) * 80, 80.0)
        return 40.0

    # ── Main prediction API ──────────────────────────────────────────────────

    def predict(self, script: Dict, metadata: Dict,
                upload_hour: int = 14,
                boosted_titles: List[str] = None,
                trending_tags:  List[str] = None,
                duration_dist:  Dict[str, float] = None,
                best_upload_hour: int = 14) -> Dict:
        """
        Predict algorithm performance for this video.
        Returns score breakdown + composite 0-100 score + channel average comparison.
        """
        title      = metadata.get("title", script.get("title", ""))
        tags       = metadata.get("tags", [])
        duration_s = script.get("total_duration", script.get("duration_s", 600))
        style      = script.get("style", metadata.get("style", "tech-dark"))

        signals = {
            "psychology_score": self._sig_psychology(script),
            "trend_alignment":  self._sig_trend_alignment(title, boosted_titles or []),
            "duration_fit":     self._sig_duration_fit(duration_s, duration_dist or {}),
            "upload_time":      self._sig_upload_time(upload_hour, best_upload_hour),
            "tag_relevance":    self._sig_tag_relevance(tags, trending_tags or []),
            "historical_style": self._sig_historical_style(style),
        }

        composite = sum(
            signals[k] * self.WEIGHTS[k] for k in self.WEIGHTS
        )
        composite = round(composite, 2)

        channel_avg  = self._channel_avg_score()
        publish_gate = composite > channel_avg if channel_avg > 0 else True

        # Predicted CTR / AVD from memory + signal scaling
        summary      = self.memory.performance_summary()
        base_ctr     = summary.get("avg_ctr", 3.5)
        base_avd     = summary.get("avg_avd_pct", 45.0)
        scale        = composite / 100
        pred_ctr     = round(base_ctr * (0.5 + scale), 2)
        pred_avd     = round(base_avd * (0.6 + 0.4 * scale), 1)
        pred_impr_ctr = round(pred_ctr * 0.8, 2)

        return {
            "composite_score":     composite,
            "channel_avg_score":   round(channel_avg, 2),
            "publish_approved":    publish_gate,
            "predicted_ctr":       pred_ctr,
            "predicted_avd_pct":   pred_avd,
            "predicted_impr_ctr":  pred_impr_ctr,
            "signals":             {k: round(v, 2) for k, v in signals.items()},
            "weakest_signal":      min(signals, key=lambda k: signals[k]),
            "predicted_at":        datetime.now(timezone.utc).isoformat(),
        }

    def _channel_avg_score(self) -> float:
        """Derive a channel-average score from memory analytics."""
        summary = self.memory.performance_summary()
        ctr     = summary.get("avg_ctr", 0)
        avd     = summary.get("avg_avd_pct", 0)
        if not ctr and not avd:
            return 0.0
        # Normalise: 5% CTR = 100 signal, 60% AVD = 100 signal
        ctr_norm = min(ctr / 5.0, 1.0) * 100
        avd_norm = min(avd / 60.0, 1.0) * 100
        return round((ctr_norm + avd_norm) / 2, 2)

    def should_publish(self, prediction: Dict) -> Tuple[bool, str]:
        """
        Return (approved: bool, reason: str).
        Approved if composite_score > channel_avg, or if no history exists.
        """
        approved = prediction.get("publish_approved", True)
        score    = prediction.get("composite_score", 0)
        avg      = prediction.get("channel_avg_score", 0)
        if avg == 0:
            return True, "No channel history — publishing unconditionally"
        if approved:
            return True, f"Score {score:.1f} > channel avg {avg:.1f} — approved"
        return False, (
            f"Score {score:.1f} ≤ channel avg {avg:.1f} — "
            f"weakest signal: {prediction.get('weakest_signal','')}. "
            "Recommend: strengthen hook, add trending tags, or adjust upload time."
        )


# ══════════════════════════════════════════════════════════════════════════════
# AlgorithmChangeDetector
# ══════════════════════════════════════════════════════════════════════════════

class AlgorithmChangeDetector:
    """
    Stores a daily snapshot of algorithm state and detects week-over-week shifts.
    When a significant change is detected, auto-adjusts config within 24 hours.
    """

    # JSD threshold above which we fire an alert
    CHANGE_THRESHOLD = 0.15
    # JSD threshold above which we also auto-adjust config
    AUTO_ADJUST_THRESHOLD = 0.25

    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    def take_snapshot(self, analysis: Dict) -> int:
        """Persist today's algorithm analysis to the DB."""
        return self.memory.save_algorithm_snapshot(analysis)

    def detect_changes(self, days: int = 7) -> Dict:
        """
        Compare today's snapshot against the average of the previous N days.
        Returns a change report with per-dimension JSD scores and alerts.
        """
        snapshots = self.memory.get_algorithm_snapshots(n=days + 1)
        if len(snapshots) < 2:
            return {"status": "insufficient_data", "days_available": len(snapshots)}

        today    = snapshots[0]
        historic = snapshots[1:]

        alerts  = []
        changes = {}

        def _avg_dist(key: str) -> Dict[str, float]:
            all_dists = [json.loads(s.get(key) or "{}") for s in historic if s.get(key)]
            if not all_dists:
                return {}
            keys = set(k for d in all_dists for k in d)
            return {k: sum(d.get(k, 0) for d in all_dists) / len(all_dists) for k in keys}

        # Duration distribution shift
        today_dur = json.loads(today.get("duration_dist") or "{}")
        hist_dur  = _avg_dist("duration_dist")
        if today_dur and hist_dur:
            jsd = _jsd(today_dur, hist_dur)
            changes["duration_dist"] = {"jsd": round(jsd, 4), "today": today_dur, "historic_avg": hist_dur}
            if jsd > self.CHANGE_THRESHOLD:
                best_now  = max(today_dur, key=today_dur.get)
                best_then = max(hist_dur,  key=hist_dur.get)
                alerts.append(f"Duration preference shifted: {best_then} → {best_now} (JSD={jsd:.3f})")

        # Shorts ratio shift
        today_sr  = today.get("shorts_ratio", 0)
        hist_sr   = sum(s.get("shorts_ratio", 0) for s in historic) / max(len(historic), 1)
        sr_delta  = abs(today_sr - hist_sr)
        changes["shorts_ratio"] = {"today": today_sr, "historic_avg": round(hist_sr, 3), "delta": round(sr_delta, 3)}
        if sr_delta > 0.10:
            direction = "surging" if today_sr > hist_sr else "declining"
            alerts.append(f"Shorts {direction}: {hist_sr:.2%} → {today_sr:.2%}")

        # Upload-hour distribution shift
        today_hr = json.loads(today.get("upload_hour_dist") or "{}")
        hist_hr  = _avg_dist("upload_hour_dist")
        if today_hr and hist_hr:
            jsd = _jsd(today_hr, hist_hr)
            changes["upload_hour"] = {"jsd": round(jsd, 4)}
            if jsd > self.CHANGE_THRESHOLD:
                best_now  = max(today_hr, key=today_hr.get)
                best_then = max(hist_hr,  key=hist_hr.get)
                alerts.append(f"Best upload window shifted: {best_then} → {best_now} (JSD={jsd:.3f})")

        # Tag vocabulary shift
        today_tags = set(json.loads(today.get("top_tags") or "[]"))
        hist_tags  = set()
        for s in historic:
            hist_tags.update(json.loads(s.get("top_tags") or "[]"))
        if today_tags and hist_tags:
            new_tags  = today_tags - hist_tags
            lost_tags = hist_tags - today_tags
            if len(new_tags) > 5:
                alerts.append(f"{len(new_tags)} new tags promoted: {', '.join(list(new_tags)[:5])}")
            changes["tags"] = {"new": list(new_tags)[:10], "dropped": list(lost_tags)[:10]}

        # Overall severity
        max_jsd = max(
            (v["jsd"] for v in changes.values() if isinstance(v, dict) and "jsd" in v),
            default=0.0,
        )
        severity = "high" if max_jsd > 0.35 else "medium" if max_jsd > self.CHANGE_THRESHOLD else "low"

        return {
            "status":           "change_detected" if alerts else "stable",
            "severity":         severity,
            "max_jsd":          round(max_jsd, 4),
            "alerts":           alerts,
            "changes":          changes,
            "snapshots_used":   len(historic),
            "analysed_at":      datetime.now(timezone.utc).isoformat(),
        }

    def auto_adjust(self, change_report: Dict) -> List[str]:
        """
        Rewrite config keys in response to detected algorithm shifts.
        Only touches the TUNABLE_KEYS allow-list. Returns list of actions taken.
        """
        if change_report.get("severity") not in ("medium", "high"):
            return []

        actions = []
        changes = change_report.get("changes", {})

        # Duration preference shift → update target duration
        dur_change = changes.get("duration_dist", {})
        if dur_change.get("jsd", 0) > self.AUTO_ADJUST_THRESHOLD:
            today_dist = dur_change.get("today", {})
            if today_dist:
                best_bucket = max(today_dist, key=today_dist.get)
                new_s = _bucket_to_seconds(best_bucket)
                if new_s:
                    _update_yaml_config("video.duration_seconds", new_s)
                    actions.append(f"Updated video.duration_seconds → {new_s}s (bucket: {best_bucket})")

        # Upload-hour shift → update preferred upload hour
        hr_change = changes.get("upload_hour", {})
        if hr_change.get("jsd", 0) > self.AUTO_ADJUST_THRESHOLD:
            snapshots = self.memory.get_algorithm_snapshots(n=1)
            if snapshots and snapshots[0].get("best_upload_hour") is not None:
                new_hour = snapshots[0]["best_upload_hour"]
                _update_yaml_config("publishing.preferred_upload_hour_utc", new_hour)
                actions.append(f"Updated publishing.preferred_upload_hour_utc → {new_hour}")

        for alert in change_report.get("alerts", []):
            log.warning(f"[AlgorithmChangeDetector] ALERT: {alert}")

        if actions:
            log.info(f"[AlgorithmChangeDetector] Auto-adjusted {len(actions)} config keys")
        return actions


def _bucket_to_seconds(bucket: str) -> Optional[int]:
    """Return the midpoint of a duration bucket in seconds."""
    _map = {
        "<2min":    90,
        "2-5min":   210,
        "5-8min":   390,
        "8-12min":  600,
        "12-20min": 960,
        "20+min":   1200,
    }
    return _map.get(bucket)


def _update_yaml_config(dotted_key: str, value: Any) -> bool:
    """Update a dotted key in master_config.yaml safely."""
    cfg_path = HERE / "config" / "master_config.yaml"
    if not cfg_path.exists():
        return False
    try:
        import yaml
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        keys = dotted_key.split(".")
        node = cfg
        for k in keys[:-1]:
            if k not in node:
                return False
            node = node[k]
        node[keys[-1]] = value
        with open(cfg_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        log.info(f"[config] {dotted_key} = {value}")
        return True
    except Exception as e:
        log.warning(f"Config update failed ({dotted_key}): {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# AlgorithmHacker — Main orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class AlgorithmHacker:
    """
    Daily algorithm reverse-engineering cycle:
      1. Sample homepage across 50 viewer contexts
      2. Analyse boosted-video patterns (duration, tags, Shorts ratio, CTR signals)
      3. Compute best upload times from own-channel + external data
      4. Detect week-over-week algorithm shifts
      5. Auto-adjust strategy config if significant change detected
      6. Save snapshot to DB; expose optimal settings for production pipeline

    Pre-publish gate (pre_publish_check):
      Shadow-rank the video; only approve if predicted score > channel average.
    """

    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory   = memory or get_memory()
        self.sampler  = HomepageSampler()
        self.tagger   = TagAnalyzer()
        self.ranker   = ShadowRanker(self.memory)
        self.detector = AlgorithmChangeDetector(self.memory)

    # ── Full daily cycle ──────────────────────────────────────────────────────

    def run_daily(self, n_sessions: int = 50) -> Dict:
        """
        Run the full daily algorithm-reverse-engineering cycle.
        Returns the analysis dict; also saves a snapshot + triggers change detection.
        """
        log.info("=" * 60)
        log.info(f"ALGORITHM HACKER — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
        log.info("=" * 60)

        # 1. Sample homepage
        log.info(f"[1/5] Sampling homepage ({n_sessions} contexts)…")
        samples = self.sampler.sample(n_sessions)
        if not samples:
            log.warning("  No samples returned — using cached data")
            return self._load_cached_analysis()

        # 2. Analyse patterns
        log.info("[2/5] Analysing boosted-video patterns…")
        analysis = self._analyse_samples(samples)

        # 3. Best upload time from own-channel data
        log.info("[3/5] Computing optimal upload times…")
        analysis.update(self._best_upload_times())

        # 4. Save snapshot
        log.info("[4/5] Saving algorithm snapshot…")
        snap_id = self.detector.take_snapshot(analysis)
        analysis["snapshot_id"] = snap_id

        # 5. Detect changes + auto-adjust
        log.info("[5/5] Checking for algorithm shifts…")
        change_report = self.detector.detect_changes(days=7)
        analysis["change_report"] = change_report
        if change_report.get("severity") in ("medium", "high"):
            actions = self.detector.auto_adjust(change_report)
            analysis["auto_adjustments"] = actions
            log.info(f"  Auto-adjusted {len(actions)} settings")
        else:
            log.info(f"  Algorithm status: {change_report.get('status', 'stable')}")

        # Persist to disk cache
        self._save_analysis(analysis)
        log.info("Algorithm Hacker daily cycle complete.")
        return analysis

    # ── Pattern analysis ──────────────────────────────────────────────────────

    def _analyse_samples(self, samples: List[Dict]) -> Dict:
        """Derive algorithm signals from homepage samples."""
        top_tags      = self.tagger.top_tags(samples, n=30)
        ctr_spots     = self.tagger.ctr_sweet_spots(samples)
        shorts_ratio  = self.tagger.shorts_ratio(samples)
        dur_dist      = self.tagger.duration_distribution(samples)
        boosted_titles = [v["title"] for v in samples[:50] if v.get("title")]

        # Infer optimal duration from distribution
        best_bucket = max(dur_dist, key=dur_dist.get) if dur_dist else "8-12min"
        optimal_dur = _bucket_to_seconds(best_bucket) or 600

        # Shorts vs long-form verdict
        if shorts_ratio > 0.40:
            form_balance = "algorithm_favors_shorts"
        elif shorts_ratio > 0.20:
            form_balance = "balanced"
        else:
            form_balance = "algorithm_favors_longform"

        return {
            "date":              datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "samples_count":     len(samples),
            "top_tags":          top_tags,
            "ctr_sweet_spots":   ctr_spots,
            "shorts_ratio":      shorts_ratio,
            "duration_dist":     dur_dist,
            "optimal_duration_s": optimal_dur,
            "best_duration_bucket": best_bucket,
            "form_balance":      form_balance,
            "boosted_titles":    boosted_titles[:20],
            "top_boosted":       samples[:10],
        }

    def _best_upload_times(self) -> Dict:
        """
        Derive best upload times from own-channel data via AlgorithmDecoder,
        plus infer from boosted-video timestamp patterns.
        """
        try:
            from intelligence.algorithm_decoder import AlgorithmDecoder
            dec     = AlgorithmDecoder(self.memory)
            day, hr = dec.best_upload_time()
            heatmap = dec.upload_time_heatmap()

            # Build hour-band distribution from heatmap
            hr_dist: Dict[str, float] = defaultdict(float)
            for day_data in heatmap.values():
                for h, v in day_data.items():
                    hr_dist[_hour_band(h)] += v
            total = max(sum(hr_dist.values()), 1)
            hr_dist_norm = {k: round(v / total, 4) for k, v in hr_dist.items()}

            return {
                "best_upload_day":       day,
                "best_upload_hour":      hr,
                "upload_hour_dist":      hr_dist_norm,
                "upload_time_source":    "own_channel_analytics",
            }
        except Exception as e:
            log.debug(f"Best upload time from decoder failed: {e}")
            return {
                "best_upload_day":    "Wednesday",
                "best_upload_hour":   14,
                "upload_hour_dist":   {"12-18": 0.6, "18-24": 0.4},
                "upload_time_source": "default",
            }

    # ── Pre-publish gate ───────────────────────────────────────────────────────

    def pre_publish_check(self, script: Dict, metadata: Dict,
                           upload_hour: int = None) -> Dict:
        """
        Shadow-rank the video before publishing.
        Returns prediction dict with 'publish_approved' and 'reason'.
        Also saves the prediction to shadow_rankings table.
        """
        analysis = self._load_cached_analysis()
        if not analysis:
            log.warning("No algorithm analysis cached — running quick sample (10 sessions)")
            analysis = self.run_daily(n_sessions=10)

        if upload_hour is None:
            upload_hour = analysis.get("best_upload_hour", 14)

        prediction = self.ranker.predict(
            script       = script,
            metadata     = metadata,
            upload_hour  = upload_hour,
            boosted_titles = analysis.get("boosted_titles", []),
            trending_tags  = analysis.get("top_tags", []),
            duration_dist  = analysis.get("duration_dist", {}),
            best_upload_hour = analysis.get("best_upload_hour", 14),
        )

        approved, reason = self.ranker.should_publish(prediction)
        prediction["reason"] = reason

        # Persist prediction
        self.memory.save_shadow_ranking({
            "youtube_id":       metadata.get("youtube_id", ""),
            "predicted_ctr":    prediction["predicted_ctr"],
            "predicted_avd":    prediction["predicted_avd_pct"],
            "predicted_score":  prediction["composite_score"],
            "publish_decision": "approved" if approved else "rejected",
        })

        log.info(
            f"[ShadowRank] Score={prediction['composite_score']:.1f} "
            f"vs avg={prediction['channel_avg_score']:.1f} → "
            f"{'APPROVED' if approved else 'REJECTED'}"
        )
        return prediction

    # ── Optimal settings export ────────────────────────────────────────────────

    def get_todays_optimal_settings(self) -> Dict:
        """
        Return today's algorithm-optimised production settings.
        The Brain / DailyPipeline should call this before producing a video.
        """
        analysis = self._load_cached_analysis()
        if not analysis:
            return {}
        return {
            "duration_seconds":         analysis.get("optimal_duration_s", 600),
            "best_duration_bucket":     analysis.get("best_duration_bucket", "8-12min"),
            "preferred_upload_hour":    analysis.get("best_upload_hour", 14),
            "preferred_upload_day":     analysis.get("best_upload_day", "Wednesday"),
            "trending_tags":            analysis.get("top_tags", [])[:15],
            "shorts_ratio":             analysis.get("shorts_ratio", 0.0),
            "form_balance":             analysis.get("form_balance", "balanced"),
            "ctr_sweet_spots":          analysis.get("ctr_sweet_spots", {}),
            "algorithm_status":         analysis.get("change_report", {}).get("status", "stable"),
            "last_updated":             analysis.get("date", ""),
        }

    def print_report(self, analysis: Dict = None):
        """Print a human-readable algorithm status report."""
        if analysis is None:
            analysis = self._load_cached_analysis() or {}

        print("\n" + "=" * 60)
        print(f"  ALGORITHM HACKER REPORT — {analysis.get('date', 'N/A')}")
        print("=" * 60)
        print(f"  Samples:        {analysis.get('samples_count', 0)}")
        print(f"  Form balance:   {analysis.get('form_balance', 'unknown')}")
        print(f"  Shorts ratio:   {analysis.get('shorts_ratio', 0):.1%}")
        print(f"  Optimal length: {analysis.get('optimal_duration_s', 0)//60}min "
              f"({analysis.get('best_duration_bucket', '')})")
        print(f"  Best upload:    {analysis.get('best_upload_day', '')} "
              f"{analysis.get('best_upload_hour', 14):02d}:00 UTC")
        ctr = analysis.get("ctr_sweet_spots", {})
        if ctr:
            print(f"  Title sweet spot: {ctr.get('title_sweet_spot', '')} "
                  f"({ctr.get('question_ratio', 0):.0%} with ?, "
                  f"{ctr.get('number_ratio', 0):.0%} with numbers)")
        tags = analysis.get("top_tags", [])[:10]
        print(f"  Top tags:       {', '.join(tags)}")
        cr = analysis.get("change_report", {})
        status = cr.get("status", "stable")
        sev    = cr.get("severity", "low")
        print(f"  Algorithm:      {status} (severity={sev})")
        for alert in cr.get("alerts", []):
            print(f"  ⚠  {alert}")
        adj = analysis.get("auto_adjustments", [])
        for act in adj:
            print(f"  ✓  {act}")
        print("=" * 60)

    # ── Disk cache ────────────────────────────────────────────────────────────

    def _save_analysis(self, analysis: Dict):
        path = HERE / "data" / "algorithm_analysis.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Don't persist large list of raw cards to JSON
        slim = {k: v for k, v in analysis.items() if k != "top_boosted"}
        with open(path, "w") as f:
            json.dump(slim, f, indent=2, default=str)

    def _load_cached_analysis(self) -> Optional[Dict]:
        path = HERE / "data" / "algorithm_analysis.json"
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            # Stale if older than 25h
            date_str = data.get("date", "")
            if date_str:
                saved    = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                age_h    = (datetime.now(timezone.utc) - saved).total_seconds() / 3600
                if age_h > 25:
                    log.debug("Cached analysis is stale (>25h)")
                    return None
            return data
        except Exception:
            return None
