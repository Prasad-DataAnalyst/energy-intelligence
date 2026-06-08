"""
core/memory.py — Long-term Learning Database
=============================================
SQLite-backed memory that every module reads from and writes to.
Drives continuous improvement: every run learns from the last.
"""
import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.memory")

DB_PATH = Path(__file__).parent.parent / "data" / "performance_db.sqlite"


SCHEMA = """
-- Every produced video and its eventual analytics
CREATE TABLE IF NOT EXISTS videos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT    NOT NULL,
    date                TEXT    NOT NULL,
    topic               TEXT    NOT NULL,
    title               TEXT,
    youtube_id          TEXT    UNIQUE,
    platform            TEXT    DEFAULT 'youtube',
    hook_style          TEXT,
    thumbnail_variant   TEXT,
    script_style        TEXT,
    video_length_s      REAL,
    ctr                 REAL,       -- click-through rate %
    avd                 REAL,       -- avg view duration s
    avd_pct             REAL,       -- avg view duration %
    views               INTEGER,
    likes               INTEGER,
    comments            INTEGER,
    subscribers_gained  INTEGER,
    rpm                 REAL,       -- revenue per 1000 views
    impressions         INTEGER,
    output_dir          TEXT,
    metadata            TEXT        -- JSON blob for extra fields
);

-- Trend candidates and which one was selected
CREATE TABLE IF NOT EXISTS trend_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at  TEXT    NOT NULL,
    trend       TEXT    NOT NULL,
    source      TEXT,
    score       REAL,
    selected    INTEGER DEFAULT 0,
    video_id    INTEGER REFERENCES videos(id)
);

-- Self-healer error log
CREATE TABLE IF NOT EXISTS errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    module      TEXT,
    file_path   TEXT,
    error_type  TEXT,
    error_msg   TEXT,
    traceback   TEXT,
    fixed       INTEGER DEFAULT 0,
    fix_applied TEXT,
    attempts    INTEGER DEFAULT 0
);

-- Code auditor upgrade history
CREATE TABLE IF NOT EXISTS upgrades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    file_path       TEXT    NOT NULL,
    reason          TEXT,
    original_hash   TEXT,
    improved_hash   TEXT,
    test_passed     INTEGER DEFAULT 0,
    rolled_back     INTEGER DEFAULT 0,
    commit_sha      TEXT
);

-- Config key→value performance correlation
CREATE TABLE IF NOT EXISTS config_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    config_key      TEXT    NOT NULL,
    config_value    TEXT    NOT NULL,
    avg_ctr         REAL,
    avg_avd         REAL,
    avg_rpm         REAL,
    sample_size     INTEGER DEFAULT 0,
    last_updated    TEXT
);

-- A/B test results
CREATE TABLE IF NOT EXISTS ab_tests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    INTEGER REFERENCES videos(id),
    test_type   TEXT,   -- thumbnail | title
    variant_a   TEXT,
    variant_b   TEXT,
    ctr_a       REAL,
    ctr_b       REAL,
    winner      TEXT,
    impressions INTEGER,
    completed   INTEGER DEFAULT 0,
    created_at  TEXT
);

-- Meta-learning runs: the system analysing its own performance & code,
-- writing an improvement plan, and recording what it executed.
CREATE TABLE IF NOT EXISTS meta_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,
    video_count   INTEGER,
    milestone     INTEGER,    -- which N-video milestone triggered this run
    report        TEXT,       -- JSON self-report snapshot
    plan          TEXT,       -- JSON improvement plan
    results       TEXT,       -- JSON execution results
    actions_done  INTEGER DEFAULT 0
);

-- Daily snapshots of what YouTube is currently boosting
CREATE TABLE IF NOT EXISTS algorithm_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT    NOT NULL,
    samples_count       INTEGER,
    shorts_ratio        REAL,
    optimal_duration_s  INTEGER,
    best_upload_hour    INTEGER,
    best_upload_day     TEXT,
    form_balance        TEXT,
    duration_dist       TEXT,   -- JSON {bucket: fraction}
    upload_hour_dist    TEXT,   -- JSON {band: fraction}
    top_tags            TEXT,   -- JSON list[str]
    ctr_sweet_spots     TEXT,   -- JSON dict
    boosted_titles      TEXT,   -- JSON list[str] top 20
    created_at          TEXT    NOT NULL
);

-- Shadow ranking predictions: pre-publish vs eventual actuals
CREATE TABLE IF NOT EXISTS shadow_rankings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id      TEXT,
    predicted_ctr   REAL,
    predicted_avd   REAL,
    predicted_score REAL,
    actual_ctr      REAL,
    actual_avd      REAL,
    publish_decision TEXT,  -- approved | rejected
    created_at      TEXT    NOT NULL
);
"""


class Memory:
    _lock = threading.Lock()

    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DB_PATH)
        self._in_memory = (self.db_path == ":memory:")
        if not self._in_memory:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # Keep a persistent connection for in-memory DBs (they're per-connection)
        self._mem_conn: Optional[sqlite3.Connection] = None
        if self._in_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        if self._in_memory and self._mem_conn is not None:
            try:
                yield self._mem_conn
                self._mem_conn.commit()
            except Exception:
                self._mem_conn.rollback()
                raise
        else:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # ── Videos ────────────────────────────────────────────────────────────────

    def log_video(self, date: str, topic: str, title: str,
                  output_dir: str, **kwargs) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO videos
                   (created_at, date, topic, title, output_dir,
                    hook_style, script_style, video_length_s, platform)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(),
                 date, topic, title, output_dir,
                 kwargs.get("hook_style", ""),
                 kwargs.get("script_style", ""),
                 kwargs.get("video_length_s", 0),
                 kwargs.get("platform", "youtube")),
            )
            return cur.lastrowid

    def update_video_analytics(self, video_id: int, **metrics) -> None:
        fields = ", ".join(f"{k}=?" for k in metrics)
        vals   = list(metrics.values()) + [video_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE videos SET {fields} WHERE id=?", vals)

    def update_youtube_id(self, video_id: int, yt_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE videos SET youtube_id=? WHERE id=?",
                         (yt_id, video_id))

    def get_video_stats(self, n: int = 30) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM videos ORDER BY created_at DESC LIMIT ?", (n,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_best_config(self, config_key: str) -> Optional[str]:
        """Return config value that correlates with highest average CTR."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT config_value FROM config_performance
                   WHERE config_key=? AND sample_size >= 3
                   ORDER BY avg_ctr DESC LIMIT 1""",
                (config_key,),
            ).fetchone()
        return row["config_value"] if row else None

    # ── Trends ─────────────────────────────────────────────────────────────────

    def log_trend(self, trend: str, source: str, score: float,
                  selected: bool = False, video_id: int = None):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO trend_history
                   (fetched_at, trend, source, score, selected, video_id)
                   VALUES (?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(),
                 trend, source, score, int(selected), video_id),
            )

    def get_recent_topics(self, days: int = 7) -> List[str]:
        """Return topics covered in the last N days (avoid repeats)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT topic FROM videos
                   WHERE date >= date('now', ?)
                   ORDER BY created_at DESC""",
                (f"-{days} days",),
            ).fetchall()
        return [r["topic"] for r in rows]

    def recent_topics(self, days: int = 7) -> List[str]:
        """Alias for get_recent_topics."""
        return self.get_recent_topics(days)

    def save_video(self, data: Dict) -> int:
        """Save a new video record and return its DB id."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO videos
                   (created_at, date, topic, title, youtube_id, output_dir,
                    hook_style, script_style, video_length_s, platform,
                    thumbnail_variant, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(),
                 data.get("date", datetime.now().strftime("%Y-%m-%d")),
                 data.get("topic", ""),
                 data.get("title", ""),
                 data.get("youtube_id", None),
                 data.get("output_dir", ""),
                 data.get("hook_style", ""),
                 data.get("script_style", ""),
                 data.get("video_length_s", 0),
                 data.get("platform", "youtube"),
                 data.get("thumbnail_variant", ""),
                 json.dumps({k: v for k, v in data.items()
                             if k not in ("date","topic","title","youtube_id",
                                          "output_dir","hook_style","script_style",
                                          "video_length_s","platform","thumbnail_variant")}),
                 ),
            )
            return cur.lastrowid

    def save_trends(self, trends: List[Dict]) -> None:
        """Batch-save trend candidates to trend_history."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for t in trends:
                conn.execute(
                    """INSERT INTO trend_history
                       (fetched_at, trend, source, score, selected, video_id)
                       VALUES (?,?,?,?,0,NULL)""",
                    (now, t.get("title",""), t.get("source",""), t.get("score",0)),
                )

    # ── Errors ─────────────────────────────────────────────────────────────────

    def log_error(self, module: str, file_path: str,
                  error_type: str, error_msg: str,
                  traceback_str: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO errors
                   (timestamp, module, file_path, error_type, error_msg, traceback)
                   VALUES (?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(),
                 module, file_path, error_type, error_msg, traceback_str),
            )
            return cur.lastrowid

    def mark_error_fixed(self, error_id: int, fix: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE errors SET fixed=1, fix_applied=? WHERE id=?",
                (fix, error_id),
            )

    def increment_attempts(self, error_id: int) -> int:
        with self._conn() as conn:
            conn.execute(
                "UPDATE errors SET attempts = attempts + 1 WHERE id=?",
                (error_id,),
            )
            row = conn.execute(
                "SELECT attempts FROM errors WHERE id=?", (error_id,)
            ).fetchone()
        return row["attempts"] if row else 0

    def get_unfixed_errors(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM errors WHERE fixed=0 ORDER BY timestamp DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Upgrades ──────────────────────────────────────────────────────────────

    def log_upgrade(self, file_path: str, reason: str,
                    original_hash: str, improved_hash: str,
                    test_passed: bool, commit_sha: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO upgrades
                   (timestamp, file_path, reason, original_hash,
                    improved_hash, test_passed, commit_sha)
                   VALUES (?,?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(),
                 file_path, reason, original_hash, improved_hash,
                 int(test_passed), commit_sha),
            )
            return cur.lastrowid

    # ── Learning / config correlation ─────────────────────────────────────────

    def update_config_performance(self, key: str, value: str,
                                   ctr: float, avd: float,
                                   rpm: float = 0.0) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM config_performance WHERE config_key=? AND config_value=?",
                (key, value),
            ).fetchone()
            now = datetime.now(timezone.utc).isoformat()
            if row:
                n       = row["sample_size"]
                new_ctr = (row["avg_ctr"] * n + ctr) / (n + 1)
                new_avd = (row["avg_avd"] * n + avd) / (n + 1)
                new_rpm = (row["avg_rpm"] * n + rpm) / (n + 1)
                conn.execute(
                    """UPDATE config_performance
                       SET avg_ctr=?, avg_avd=?, avg_rpm=?,
                           sample_size=sample_size+1, last_updated=?
                       WHERE config_key=? AND config_value=?""",
                    (new_ctr, new_avd, new_rpm, now, key, value),
                )
            else:
                conn.execute(
                    """INSERT INTO config_performance
                       (config_key, config_value, avg_ctr, avg_avd,
                        avg_rpm, sample_size, last_updated)
                       VALUES (?,?,?,?,?,1,?)""",
                    (key, value, ctr, avd, rpm, now),
                )

    def performance_summary(self) -> Dict[str, Any]:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) as n FROM videos").fetchone()["n"]
            avg   = conn.execute(
                "SELECT AVG(ctr) ac, AVG(avd_pct) aa, AVG(rpm) ar FROM videos"
                " WHERE ctr IS NOT NULL"
            ).fetchone()
            recent = conn.execute(
                "SELECT * FROM videos ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return {
            "total_videos": total,
            "avg_ctr":      round(avg["ac"] or 0, 2),
            "avg_avd_pct":  round(avg["aa"] or 0, 2),
            "avg_rpm":      round(avg["ar"] or 0, 2),
            "last_video":   dict(recent) if recent else {},
        }

    def video_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"]

    def error_summary(self) -> Dict[str, Any]:
        """Aggregate error stats for the meta-learner's self-report."""
        with self._conn() as conn:
            total  = conn.execute("SELECT COUNT(*) n FROM errors").fetchone()["n"]
            unfix  = conn.execute("SELECT COUNT(*) n FROM errors WHERE fixed=0").fetchone()["n"]
            by_mod = conn.execute(
                """SELECT module, error_type, COUNT(*) c FROM errors
                   GROUP BY module, error_type ORDER BY c DESC LIMIT 10"""
            ).fetchall()
        return {
            "total_errors":   total,
            "unfixed_errors": unfix,
            "recurring": [
                {"module": r["module"], "error_type": r["error_type"], "count": r["c"]}
                for r in by_mod
            ],
        }

    def upgrade_summary(self) -> Dict[str, Any]:
        """Aggregate code-upgrade stats (success vs rollback)."""
        with self._conn() as conn:
            total  = conn.execute("SELECT COUNT(*) n FROM upgrades").fetchone()["n"]
            passed = conn.execute(
                "SELECT COUNT(*) n FROM upgrades WHERE test_passed=1"
            ).fetchone()["n"]
        return {
            "total_upgrades":  total,
            "passed_upgrades": passed,
            "failed_upgrades": total - passed,
        }

    def config_correlations(self, min_samples: int = 1) -> List[Dict]:
        """All learned config→performance correlations, best CTR first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT config_key, config_value, avg_ctr, avg_avd, avg_rpm, sample_size
                   FROM config_performance
                   WHERE sample_size >= ?
                   ORDER BY avg_ctr DESC""",
                (min_samples,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Meta-learning runs ──────────────────────────────────────────────────────

    def last_meta_milestone(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(milestone) AS m FROM meta_runs"
            ).fetchone()
        return int(row["m"] or 0)

    def record_meta_run(self, video_count: int, milestone: int,
                        report: Dict, plan: Dict, results: Dict,
                        actions_done: int = 0) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO meta_runs
                   (timestamp, video_count, milestone, report, plan,
                    results, actions_done)
                   VALUES (?,?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(),
                 video_count, milestone,
                 json.dumps(report), json.dumps(plan), json.dumps(results),
                 actions_done),
            )
            return cur.lastrowid

    def recent_meta_runs(self, n: int = 5) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM meta_runs ORDER BY timestamp DESC LIMIT ?", (n,)
            ).fetchall()
        return [dict(r) for r in rows]


    # ── Algorithm hacker ───────────────────────────────────────────────────────

    def save_algorithm_snapshot(self, analysis: Dict) -> int:
        """Persist a daily algorithm state snapshot."""
        import json as _json
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO algorithm_snapshots
                   (date, samples_count, shorts_ratio, optimal_duration_s,
                    best_upload_hour, best_upload_day, form_balance,
                    duration_dist, upload_hour_dist, top_tags,
                    ctr_sweet_spots, boosted_titles, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    analysis.get("date", datetime.now().strftime("%Y-%m-%d")),
                    analysis.get("samples_count", 0),
                    analysis.get("shorts_ratio", 0.0),
                    analysis.get("optimal_duration_s", 0),
                    analysis.get("best_upload_hour", 14),
                    analysis.get("best_upload_day", ""),
                    analysis.get("form_balance", ""),
                    _json.dumps(analysis.get("duration_dist", {})),
                    _json.dumps(analysis.get("upload_hour_dist", {})),
                    _json.dumps(analysis.get("top_tags", [])),
                    _json.dumps(analysis.get("ctr_sweet_spots", {})),
                    _json.dumps(analysis.get("boosted_titles", [])[:20]),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_algorithm_snapshots(self, n: int = 8) -> List[Dict]:
        """Return the N most recent algorithm snapshots."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM algorithm_snapshots ORDER BY created_at DESC LIMIT ?", (n,)
            ).fetchall()
        return [dict(r) for r in rows]

    def save_shadow_ranking(self, data: Dict) -> int:
        """Persist a pre-publish shadow ranking prediction."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO shadow_rankings
                   (youtube_id, predicted_ctr, predicted_avd, predicted_score,
                    publish_decision, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    data.get("youtube_id", ""),
                    data.get("predicted_ctr", 0.0),
                    data.get("predicted_avd", 0.0),
                    data.get("predicted_score", 0.0),
                    data.get("publish_decision", ""),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def update_shadow_ranking_actuals(self, youtube_id: str,
                                       actual_ctr: float,
                                       actual_avd: float) -> None:
        """Fill in actual analytics for a shadow-ranked video after publishing."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE shadow_rankings
                   SET actual_ctr=?, actual_avd=?
                   WHERE youtube_id=? AND actual_ctr IS NULL""",
                (actual_ctr, actual_avd, youtube_id),
            )

    def shadow_ranking_accuracy(self) -> Dict:
        """
        Compare predicted vs actual CTR/AVD for calibration insight.
        Returns mean absolute error and sample size.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT predicted_ctr, actual_ctr, predicted_avd, actual_avd
                   FROM shadow_rankings
                   WHERE actual_ctr IS NOT NULL AND actual_avd IS NOT NULL"""
            ).fetchall()
        if not rows:
            return {"samples": 0, "ctr_mae": None, "avd_mae": None}
        n       = len(rows)
        ctr_mae = sum(abs(r["predicted_ctr"] - r["actual_ctr"]) for r in rows) / n
        avd_mae = sum(abs(r["predicted_avd"] - r["actual_avd"]) for r in rows) / n
        return {
            "samples":  n,
            "ctr_mae":  round(ctr_mae, 4),
            "avd_mae":  round(avd_mae, 2),
        }


# Module-level singleton
_mem: Optional[Memory] = None

def get_memory() -> Memory:
    global _mem
    if _mem is None:
        _mem = Memory()
    return _mem
