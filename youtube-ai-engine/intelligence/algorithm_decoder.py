"""
intelligence/algorithm_decoder.py — YouTube Algorithm Pattern Tracker
======================================================================
Tracks which upload patterns correlate with algorithmic promotion:
  - Upload day / hour → view velocity correlation
  - Title length vs CTR
  - Video duration sweet spots
  - Thumbnail style → impression-to-click ratio
  - First 48h view velocity → long-tail prediction

Builds a scoring model to predict which settings maximise reach.
"""
import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.algorithm_decoder")

HERE = Path(__file__).parent.parent


class AlgorithmDecoder:
    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    # ── Data extraction ────────────────────────────────────────────────────

    def _get_all_video_stats(self) -> List[Dict]:
        """Pull all videos with analytics from memory."""
        return self.memory.get_video_stats(n=500)

    # ── Pattern analysis ───────────────────────────────────────────────────

    def upload_time_heatmap(self) -> Dict[str, Dict[int, float]]:
        """
        Returns {day_name: {hour: avg_view_velocity}} where view_velocity
        = views / hours_since_upload (normalised).
        """
        videos = self._get_all_video_stats()
        heatmap: Dict[str, Dict[int, List[float]]] = defaultdict(
            lambda: defaultdict(list))

        for v in videos:
            views = v.get("views", 0)
            if not views or not v.get("created_at"):
                continue
            try:
                ts   = datetime.fromisoformat(v["created_at"])
                day  = ts.strftime("%A")
                hour = ts.hour
                heatmap[day][hour].append(views)
            except Exception:
                continue

        result: Dict[str, Dict[int, float]] = {}
        for day, hours in heatmap.items():
            result[day] = {h: sum(vs) / len(vs) for h, vs in hours.items()}
        return result

    def best_upload_time(self) -> Tuple[str, int]:
        """Return (day_of_week, hour_utc) with highest avg view velocity."""
        hmap = self.upload_time_heatmap()
        best_day, best_hour, best_score = "Wednesday", 14, 0.0
        for day, hours in hmap.items():
            for hour, score in hours.items():
                if score > best_score:
                    best_day, best_hour, best_score = day, hour, score
        log.info(f"Best upload time: {best_day} {best_hour:02d}:00 UTC "
                 f"(avg {best_score:.0f} views)")
        return best_day, best_hour

    def title_length_analysis(self) -> Dict:
        """Correlate title length (words) with CTR."""
        videos = self._get_all_video_stats()
        buckets: Dict[int, List[float]] = defaultdict(list)
        for v in videos:
            title = v.get("title", "")
            ctr   = v.get("ctr", 0)
            if not title or not ctr:
                continue
            words = len(title.split())
            bucket = (words // 2) * 2   # group: 0-1, 2-3, 4-5, …
            buckets[bucket].append(ctr)

        result = {}
        for bucket, ctrs in sorted(buckets.items()):
            result[f"{bucket}-{bucket+1}_words"] = {
                "avg_ctr": round(sum(ctrs) / len(ctrs), 4),
                "samples": len(ctrs),
            }
        best_bucket = max(result, key=lambda k: result[k]["avg_ctr"]) if result else "6-7_words"
        log.info(f"Title length analysis: best bucket = {best_bucket}")
        return {"buckets": result, "best": best_bucket}

    def duration_sweet_spots(self) -> Dict:
        """Find video durations with best AVD% and CTR."""
        videos = self._get_all_video_stats()
        buckets: Dict[str, Dict[str, list]] = defaultdict(
            lambda: {"avd_pct": [], "ctr": []})

        for v in videos:
            length = v.get("video_length_s", 0)
            avd    = v.get("avd_pct", 0)
            ctr    = v.get("ctr", 0)
            if not length:
                continue
            mins   = length // 60
            if mins < 2:
                key = "< 2 min"
            elif mins < 5:
                key = "2–5 min"
            elif mins < 8:
                key = "5–8 min"
            elif mins < 12:
                key = "8–12 min"
            elif mins < 20:
                key = "12–20 min"
            else:
                key = "20+ min"
            if avd:
                buckets[key]["avd_pct"].append(avd)
            if ctr:
                buckets[key]["ctr"].append(ctr)

        result = {}
        for key, data in buckets.items():
            result[key] = {
                "avg_avd_pct": (round(sum(data["avd_pct"]) / len(data["avd_pct"]), 2)
                                if data["avd_pct"] else 0),
                "avg_ctr":     (round(sum(data["ctr"]) / len(data["ctr"]), 4)
                                if data["ctr"] else 0),
                "samples":     max(len(data["avd_pct"]), len(data["ctr"])),
            }

        # Composite score = avd_pct * ctr
        best = max(result, key=lambda k: (
            result[k]["avg_avd_pct"] * result[k]["avg_ctr"]
        )) if result else "8–12 min"
        log.info(f"Duration sweet spot: {best}")
        return {"buckets": result, "best": best}

    def velocity_predictor(self, views_24h: int,
                            views_48h: int) -> Dict:
        """
        Given early view counts, predict 28-day outcome based on
        historical ratios stored in memory.
        """
        videos = self._get_all_video_stats()
        ratios = []
        for v in videos:
            v28 = v.get("views", 0)
            # We don't have per-checkpoint data in this schema —
            # use avg_ctr as proxy for early momentum
            ctr = v.get("ctr", 0)
            if v28 and ctr:
                ratios.append(v28 / max(ctr, 0.01))

        avg_ratio = sum(ratios) / len(ratios) if ratios else 1000
        predicted_28d = int(views_48h * (avg_ratio / 100))

        return {
            "views_24h":     views_24h,
            "views_48h":     views_48h,
            "predicted_28d": predicted_28d,
            "confidence":    "low" if len(ratios) < 10 else "medium" if len(ratios) < 30 else "high",
        }

    # ── Pattern DB ─────────────────────────────────────────────────────────

    def build_pattern_db(self) -> Dict:
        """Full algorithm pattern analysis — saves to data/algorithm_patterns.json."""
        best_day, best_hour = self.best_upload_time()
        title_analysis      = self.title_length_analysis()
        duration_analysis   = self.duration_sweet_spots()

        pattern_db = {
            "best_upload_day":      best_day,
            "best_upload_hour_utc": best_hour,
            "title_analysis":       title_analysis,
            "duration_analysis":    duration_analysis,
            "generated_at":         datetime.now(timezone.utc).isoformat(),
        }

        out = HERE / "data" / "algorithm_patterns.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(pattern_db, f, indent=2)

        log.info(f"Algorithm pattern DB saved → {out}")
        return pattern_db

    def get_patterns(self) -> Dict:
        """Load pattern DB from disk (build if missing)."""
        path = HERE / "data" / "algorithm_patterns.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return self.build_pattern_db()
