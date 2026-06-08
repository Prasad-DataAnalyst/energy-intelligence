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
from datetime import datetime, timedelta, timezone
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

-- Sponsorship CRM: brand prospects and deal pipeline
CREATE TABLE IF NOT EXISTS sponsorships (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_name      TEXT    NOT NULL,
    email           TEXT,
    category        TEXT,
    niche           TEXT,
    cpm_rate        REAL,
    status          TEXT    DEFAULT 'prospect',  -- prospect/contacted/negotiating/active/declined
    deal_value      REAL,
    outreach_sent   INTEGER DEFAULT 0,
    outreach_date   TEXT,
    notes           TEXT,
    created_at      TEXT    NOT NULL
);

-- Affiliate link placements and conversion tracking
CREATE TABLE IF NOT EXISTS affiliate_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_db_id INTEGER REFERENCES videos(id),
    youtube_id  TEXT,
    product     TEXT    NOT NULL,
    program     TEXT,
    url         TEXT,
    placement   TEXT,   -- description | pinned_comment
    clicks      INTEGER DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    revenue     REAL    DEFAULT 0,
    created_at  TEXT    NOT NULL
);

-- Weekly revenue snapshots for trend tracking
CREATE TABLE IF NOT EXISTS revenue_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT    NOT NULL,
    week_num      INTEGER,
    total_views   INTEGER DEFAULT 0,
    avg_rpm       REAL    DEFAULT 0,
    ad_revenue    REAL    DEFAULT 0,
    sponsor_rev   REAL    DEFAULT 0,
    affiliate_rev REAL    DEFAULT 0,
    merch_rev     REAL    DEFAULT 0,
    total_rev     REAL    DEFAULT 0,
    subscribers   INTEGER DEFAULT 0,
    created_at    TEXT    NOT NULL
);

-- General monetization events (sponsor deals, milestone triggers, alerts)
CREATE TABLE IF NOT EXISTS monetization_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    event_type  TEXT,   -- video_optimized | sponsor_deal | merch_milestone | revenue_alert
    youtube_id  TEXT,
    video_db_id INTEGER REFERENCES videos(id),
    data        TEXT,   -- JSON blob
    revenue     REAL    DEFAULT 0
);

-- Competitor daily subscriber/view snapshots
CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  TEXT NOT NULL,
    date        TEXT NOT NULL,
    subscribers INTEGER,
    total_views INTEGER,
    video_count INTEGER,
    created_at  TEXT NOT NULL,
    UNIQUE(channel_id, date)
);

-- Per-channel deep intelligence blobs
CREATE TABLE IF NOT EXISTS competitor_intelligence (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id   TEXT NOT NULL,
    intel_type   TEXT,  -- full_analysis | title_formula | schedule | complaints
    data         TEXT,  -- JSON
    generated_at TEXT NOT NULL
);

-- Weekly content gap opportunities
CREATE TABLE IF NOT EXISTS content_gaps (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    topic             TEXT NOT NULL,
    gap_type          TEXT,  -- uncovered | poorly_covered | unanswered_question | trending_void
    opportunity_score REAL,
    evidence          TEXT,  -- JSON
    competitor_ids    TEXT,  -- JSON list
    status            TEXT DEFAULT 'open',  -- open | assigned | published
    video_db_id       INTEGER REFERENCES videos(id),
    discovered_at     TEXT NOT NULL,
    week_num          INTEGER
);

-- Title warfare battles tracker
CREATE TABLE IF NOT EXISTS title_battles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_channel  TEXT,
    competitor_video_id TEXT,
    competitor_title    TEXT,
    our_titles          TEXT,  -- JSON array of 3 alternatives
    best_title          TEXT,
    keywords            TEXT,  -- JSON list
    deadline_utc        TEXT,  -- 6h after competitor upload
    urgency_minutes     INTEGER,
    action_taken        TEXT DEFAULT 'pending',
    created_at          TEXT NOT NULL
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

-- Per-language translated metadata for each video
CREATE TABLE IF NOT EXISTS translations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_db_id   INTEGER REFERENCES videos(id),
    language_code        TEXT NOT NULL,
    translated_title     TEXT,
    translated_description TEXT,
    translated_tags      TEXT,  -- JSON
    youtube_id           TEXT,
    status               TEXT DEFAULT 'pending',
    created_at           TEXT NOT NULL,
    UNIQUE(source_video_db_id, language_code)
);

-- Per-country trend signals detected by RegionalTrendHunter
CREATE TABLE IF NOT EXISTS regional_trends (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    country_code TEXT NOT NULL,
    title        TEXT NOT NULL,
    score        REAL DEFAULT 0,
    source       TEXT,
    trend_data   TEXT,  -- JSON
    detected_at  TEXT NOT NULL
);

-- Per-language YouTube channel registry
CREATE TABLE IF NOT EXISTS language_channels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    language_code    TEXT NOT NULL UNIQUE,
    channel_id       TEXT,
    channel_name     TEXT,
    subscriber_count INTEGER DEFAULT 0,
    total_views      INTEGER DEFAULT 0,
    video_count      INTEGER DEFAULT 0,
    updated_at       TEXT NOT NULL
);

-- Async localization job queue (translate / voice / thumbnail / publish)
CREATE TABLE IF NOT EXISTS localization_jobs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_db_id INTEGER REFERENCES videos(id),
    language_code      TEXT NOT NULL,
    job_type           TEXT,   -- full | translate | voice | thumbnail | publish
    status             TEXT DEFAULT 'queued',  -- queued | running | done | failed
    result             TEXT,   -- JSON
    error              TEXT,
    created_at         TEXT NOT NULL,
    completed_at       TEXT
);

-- Each Short produced from a long-form video
CREATE TABLE IF NOT EXISTS shorts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_db_id INTEGER REFERENCES videos(id),
    source_youtube_id  TEXT,
    short_index        INTEGER DEFAULT 1,
    segment_type       TEXT,   -- shocking_stat | surprising_reveal | emotional_peak | etc.
    virality_score     REAL    DEFAULT 0,
    start_s            REAL    DEFAULT 0,
    end_s              REAL    DEFAULT 0,
    duration_s         REAL    DEFAULT 0,
    title              TEXT,
    hook_text          TEXT,
    platform           TEXT    DEFAULT 'multi',
    file_path          TEXT,
    youtube_id         TEXT,
    status             TEXT    DEFAULT 'pending',
    views              INTEGER DEFAULT 0,
    likes              INTEGER DEFAULT 0,
    shares             INTEGER DEFAULT 0,
    revenue            REAL    DEFAULT 0,
    created_at         TEXT    NOT NULL
);

-- Shorts that should be expanded into full long-form videos
CREATE TABLE IF NOT EXISTS shorts_expansion_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    short_db_id    INTEGER REFERENCES shorts(id),
    topic          TEXT,
    virality_score REAL    DEFAULT 0,
    views          INTEGER DEFAULT 0,
    signal_type    TEXT,
    status         TEXT    DEFAULT 'queued',  -- queued | in_production | published
    created_at     TEXT    NOT NULL
);

-- Comments fetched from YouTube videos
CREATE TABLE IF NOT EXISTS comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id   TEXT    NOT NULL,
    comment_id   TEXT    UNIQUE,
    author       TEXT,
    text         TEXT,
    likes        INTEGER DEFAULT 0,
    category     TEXT,
    reply_status TEXT    DEFAULT 'pending',
    reply_text   TEXT,
    pin_score    REAL    DEFAULT 0,
    is_pinned    INTEGER DEFAULT 0,
    is_hearted   INTEGER DEFAULT 0,
    published_at TEXT,
    fetched_at   TEXT    NOT NULL
);

-- Scheduled and published community posts
CREATE TABLE IF NOT EXISTS community_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_type       TEXT,
    content         TEXT,
    poll_options    TEXT,
    scheduled_at    TEXT,
    published_at    TEXT,
    engagement_goal TEXT,
    youtube_post_id TEXT,
    views           INTEGER DEFAULT 0,
    votes           INTEGER DEFAULT 0,
    comments        INTEGER DEFAULT 0,
    status          TEXT    DEFAULT 'scheduled',
    created_at      TEXT    NOT NULL
);

-- Subscriber drop events and recovery tracking
CREATE TABLE IF NOT EXISTS retention_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    event_type       TEXT,
    subscriber_count INTEGER,
    change_count     INTEGER,
    change_pct       REAL,
    culprit_video    TEXT,
    strategy_applied TEXT,
    resolved         INTEGER DEFAULT 0
);

-- Collaboration prospect pipeline
CREATE TABLE IF NOT EXISTS collab_prospects (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id       TEXT    NOT NULL UNIQUE,
    channel_name     TEXT,
    subscriber_count INTEGER DEFAULT 0,
    content_overlap  REAL    DEFAULT 0,
    niche            TEXT,
    pitch_email      TEXT,
    pitch_sent       INTEGER DEFAULT 0,
    pitch_date       TEXT,
    response         TEXT,
    status           TEXT    DEFAULT 'prospect',
    collab_video_id  TEXT,
    our_growth       REAL    DEFAULT 0,
    their_growth     REAL    DEFAULT 0,
    created_at       TEXT    NOT NULL
);

-- Keyword clusters for topic authority
CREATE TABLE IF NOT EXISTS keyword_clusters (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    main_keyword      TEXT    NOT NULL,
    cluster_topic     TEXT,
    opportunity_score REAL    DEFAULT 0,
    videos_planned    TEXT,   -- JSON list of video idea dicts
    videos_done       INTEGER DEFAULT 0,
    status            TEXT    DEFAULT 'planned',
    created_at        TEXT    NOT NULL
);

-- Strategic playlist plans
CREATE TABLE IF NOT EXISTS playlists (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id      TEXT,
    title           TEXT    NOT NULL,
    seo_title       TEXT,
    topic_cluster   TEXT,
    video_ids       TEXT,   -- JSON ordered list of youtube_ids
    binge_trap      INTEGER DEFAULT 0,
    total_views     INTEGER DEFAULT 0,
    avg_session_dur REAL    DEFAULT 0,
    status          TEXT    DEFAULT 'active',
    created_at      TEXT    NOT NULL
);

-- 30/60/90 day subscriber growth targets
CREATE TABLE IF NOT EXISTS growth_targets (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    set_date           TEXT    NOT NULL,
    baseline_subs      INTEGER DEFAULT 0,
    posting_freq_week  REAL    DEFAULT 7,
    weekly_growth_rate REAL    DEFAULT 0,
    freq_multiplier    REAL    DEFAULT 1,
    day30_target       INTEGER DEFAULT 0,
    day60_target       INTEGER DEFAULT 0,
    day90_target       INTEGER DEFAULT 0,
    day30_actual       INTEGER,
    day60_actual       INTEGER,
    day90_actual       INTEGER,
    status             TEXT    DEFAULT 'active',
    intensified        INTEGER DEFAULT 0,
    created_at         TEXT    NOT NULL
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


    # ── Monetization ──────────────────────────────────────────────────────────

    def save_sponsorship_prospect(self, data: Dict) -> int:
        """Upsert a sponsorship prospect (dedup by brand name)."""
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM sponsorships WHERE brand_name=?",
                (data.get("name", data.get("brand_name", "")),),
            ).fetchone()
            if existing:
                return existing["id"]
            cur = conn.execute(
                """INSERT INTO sponsorships
                   (brand_name, email, category, niche, status, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    data.get("name", data.get("brand_name", "")),
                    data.get("email", ""),
                    data.get("category", ""),
                    data.get("niche", ""),
                    "prospect",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def update_sponsorship_status(self, brand_name: str, status: str,
                                   deal_value: float = None,
                                   notes: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE sponsorships
                   SET status=?, deal_value=?, notes=?, outreach_sent=1,
                       outreach_date=?
                   WHERE brand_name=?""",
                (status, deal_value, notes,
                 datetime.now(timezone.utc).isoformat()[:10], brand_name),
            )

    def get_sponsorship_prospects(self, status: str = None) -> List[Dict]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM sponsorships WHERE status=? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sponsorships ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def save_affiliate_link(self, data: Dict) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO affiliate_links
                   (video_db_id, youtube_id, product, program, url, placement, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    data.get("video_db_id"),
                    data.get("youtube_id", ""),
                    data.get("product", ""),
                    data.get("program", ""),
                    data.get("url", ""),
                    data.get("placement", "description"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def update_affiliate_stats(self, link_id: int, clicks: int = 0,
                                conversions: int = 0, revenue: float = 0.0) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE affiliate_links
                   SET clicks=clicks+?, conversions=conversions+?, revenue=revenue+?
                   WHERE id=?""",
                (clicks, conversions, revenue, link_id),
            )

    def affiliate_revenue_weekly(self, weeks: int = 4) -> List[Dict]:
        """Aggregate affiliate revenue by week."""
        cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT strftime('%Y-W%W', created_at) AS week,
                          SUM(revenue) AS revenue,
                          SUM(clicks) AS clicks,
                          SUM(conversions) AS conversions
                   FROM affiliate_links
                   WHERE created_at >= ?
                   GROUP BY week ORDER BY week DESC""",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_revenue_snapshot(self, data: Dict) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO revenue_snapshots
                   (date, week_num, total_views, avg_rpm,
                    ad_revenue, sponsor_rev, affiliate_rev, merch_rev,
                    total_rev, subscribers, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data.get("date", datetime.now().strftime("%Y-%m-%d")),
                    data.get("week_num", 0),
                    data.get("total_views", 0),
                    data.get("avg_rpm", 0.0),
                    data.get("ad_revenue", 0.0),
                    data.get("sponsor_rev", 0.0),
                    data.get("affiliate_rev", 0.0),
                    data.get("merch_rev", 0.0),
                    data.get("total_rev", 0.0),
                    data.get("subscribers", 0),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def save_monetization_event(self, data: Dict) -> int:
        import json as _json
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO monetization_events
                   (timestamp, event_type, youtube_id, video_db_id, data, revenue)
                   VALUES (?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    data.get("event_type", ""),
                    data.get("youtube_id", ""),
                    data.get("video_db_id"),
                    _json.dumps(data.get("data", {})),
                    data.get("revenue", 0.0),
                ),
            )
            return cur.lastrowid

    def get_monetization_events(self, event_type: str = None,
                                  n: int = 50) -> List[Dict]:
        with self._conn() as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM monetization_events WHERE event_type=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (event_type, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM monetization_events ORDER BY timestamp DESC LIMIT ?",
                    (n,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ── Competitor dominator ──────────────────────────────────────────────────

    def upsert_competitor_snapshot(self, channel_id: str, date: str,
                                    subscribers: int, total_views: int,
                                    video_count: int) -> int:
        """Insert or replace a daily snapshot for a competitor channel."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO competitor_snapshots
                   (channel_id, date, subscribers, total_views, video_count, created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(channel_id, date) DO UPDATE SET
                       subscribers=excluded.subscribers,
                       total_views=excluded.total_views,
                       video_count=excluded.video_count,
                       created_at=excluded.created_at""",
                (channel_id, date, subscribers, total_views, video_count,
                 datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def get_competitor_snapshots(self, channel_id: str, days: int = 30) -> List[Dict]:
        """Return snapshots for a channel over the last N days."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM competitor_snapshots
                   WHERE channel_id=? AND date >= date('now', ?)
                   ORDER BY date DESC""",
                (channel_id, f"-{days} days"),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_competitor_intelligence(self, channel_id: str,
                                      intel_type: str, data: Dict) -> int:
        """Persist a deep-intelligence record for a competitor channel."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO competitor_intelligence
                   (channel_id, intel_type, data, generated_at)
                   VALUES (?,?,?,?)""",
                (channel_id, intel_type,
                 json.dumps(data),
                 datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def get_competitor_intelligence(self, channel_id: str = None,
                                     intel_type: str = None) -> List[Dict]:
        """Retrieve competitor intelligence records, optionally filtered."""
        with self._conn() as conn:
            if channel_id and intel_type:
                rows = conn.execute(
                    """SELECT * FROM competitor_intelligence
                       WHERE channel_id=? AND intel_type=?
                       ORDER BY generated_at DESC""",
                    (channel_id, intel_type),
                ).fetchall()
            elif channel_id:
                rows = conn.execute(
                    """SELECT * FROM competitor_intelligence
                       WHERE channel_id=?
                       ORDER BY generated_at DESC""",
                    (channel_id,),
                ).fetchall()
            elif intel_type:
                rows = conn.execute(
                    """SELECT * FROM competitor_intelligence
                       WHERE intel_type=?
                       ORDER BY generated_at DESC""",
                    (intel_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM competitor_intelligence ORDER BY generated_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def save_content_gap(self, data: Dict) -> int:
        """Upsert a content gap by topic."""
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM content_gaps WHERE topic=?",
                (data.get("topic", ""),),
            ).fetchone()
            now = datetime.now(timezone.utc).isoformat()
            if existing:
                conn.execute(
                    """UPDATE content_gaps
                       SET gap_type=?, opportunity_score=?, evidence=?,
                           competitor_ids=?, status=?, week_num=?
                       WHERE topic=?""",
                    (
                        data.get("gap_type", ""),
                        data.get("opportunity_score", 0.0),
                        json.dumps(data.get("evidence", {})),
                        json.dumps(data.get("competitor_ids", [])),
                        data.get("status", "open"),
                        data.get("week_num", 0),
                        data.get("topic", ""),
                    ),
                )
                return existing["id"]
            cur = conn.execute(
                """INSERT INTO content_gaps
                   (topic, gap_type, opportunity_score, evidence,
                    competitor_ids, status, discovered_at, week_num)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    data.get("topic", ""),
                    data.get("gap_type", ""),
                    data.get("opportunity_score", 0.0),
                    json.dumps(data.get("evidence", {})),
                    json.dumps(data.get("competitor_ids", [])),
                    data.get("status", "open"),
                    now,
                    data.get("week_num", 0),
                ),
            )
            return cur.lastrowid

    def get_content_gaps(self, status: str = "open", n: int = 20) -> List[Dict]:
        """Return open content gaps sorted by opportunity score."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    """SELECT * FROM content_gaps WHERE status=?
                       ORDER BY opportunity_score DESC LIMIT ?""",
                    (status, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM content_gaps
                       ORDER BY opportunity_score DESC LIMIT ?""",
                    (n,),
                ).fetchall()
        return [dict(r) for r in rows]

    def mark_gap_published(self, gap_id: int, video_db_id: int) -> None:
        """Mark a content gap as published and link it to a video."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE content_gaps SET status='published', video_db_id=? WHERE id=?",
                (video_db_id, gap_id),
            )

    def save_title_battle(self, data: Dict) -> int:
        """Save a title warfare battle record."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO title_battles
                   (competitor_channel, competitor_video_id, competitor_title,
                    our_titles, best_title, keywords, deadline_utc,
                    urgency_minutes, action_taken, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    data.get("competitor_channel", ""),
                    data.get("competitor_video_id", ""),
                    data.get("competitor_title", ""),
                    json.dumps(data.get("our_titles", [])),
                    data.get("best_title", ""),
                    json.dumps(data.get("keywords", [])),
                    data.get("deadline_utc", ""),
                    data.get("urgency_minutes", 360),
                    data.get("action_taken", "pending"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_pending_battles(self) -> List[Dict]:
        """Return title battles whose deadline has not yet passed."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM title_battles
                   WHERE action_taken='pending'
                     AND deadline_utc > ?
                   ORDER BY deadline_utc ASC""",
                (datetime.now(timezone.utc).isoformat(),),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_battle_action(self, battle_id: int, action: str) -> None:
        """Update the action_taken field on a title battle."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE title_battles SET action_taken=? WHERE id=?",
                (action, battle_id),
            )

    def competitor_growth_rate(self, channel_id: str, days: int = 7) -> float:
        """
        Return subscriber growth rate (%) over the last N days.
        Returns 0.0 if insufficient data.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT date, subscribers FROM competitor_snapshots
                   WHERE channel_id=? AND subscribers IS NOT NULL
                   ORDER BY date DESC LIMIT ?""",
                (channel_id, days + 1),
            ).fetchall()
        if len(rows) < 2:
            return 0.0
        newest = rows[0]["subscribers"]
        oldest = rows[-1]["subscribers"]
        if oldest == 0:
            return 0.0
        return round((newest - oldest) / oldest * 100, 4)

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


    # ── Global expansion ──────────────────────────────────────────────────────

    def save_translation(self, source_video_db_id: int,
                          language_code: str, seo: Dict) -> int:
        """Upsert a translated metadata record for a video."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO translations
                   (source_video_db_id, language_code, translated_title,
                    translated_description, translated_tags, status, created_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(source_video_db_id, language_code) DO UPDATE SET
                       translated_title=excluded.translated_title,
                       translated_description=excluded.translated_description,
                       translated_tags=excluded.translated_tags,
                       status=excluded.status""",
                (
                    source_video_db_id,
                    language_code,
                    seo.get("title", ""),
                    seo.get("description", ""),
                    json.dumps(seo.get("tags", [])),
                    "done",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_translations(self, source_video_db_id: int) -> List[Dict]:
        """Return all translations for a given source video."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM translations WHERE source_video_db_id=? "
                "ORDER BY language_code",
                (source_video_db_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_regional_trends(self, trends: List[Dict],
                              source: str = "innertube") -> None:
        """Bulk-insert regional trend signals."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for t in trends:
                conn.execute(
                    """INSERT INTO regional_trends
                       (country_code, title, score, source, trend_data, detected_at)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        t.get("country_code", t.get("source_market", "XX")),
                        t.get("title", ""),
                        t.get("score", 0.0),
                        source,
                        json.dumps(t),
                        now,
                    ),
                )

    def get_regional_trends(self, country_code: str = None,
                             days: int = 7, n: int = 50) -> List[Dict]:
        """Return recent regional trend records."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            if country_code:
                rows = conn.execute(
                    "SELECT * FROM regional_trends WHERE country_code=? "
                    "AND detected_at >= ? ORDER BY score DESC LIMIT ?",
                    (country_code, cutoff, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM regional_trends WHERE detected_at >= ? "
                    "ORDER BY score DESC LIMIT ?",
                    (cutoff, n),
                ).fetchall()
        return [dict(r) for r in rows]

    def upsert_language_channel(self, language_code: str,
                                 channel_id: str, channel_name: str) -> int:
        """Insert or update a language channel record."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO language_channels
                   (language_code, channel_id, channel_name, updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(language_code) DO UPDATE SET
                       channel_id=excluded.channel_id,
                       channel_name=excluded.channel_name,
                       updated_at=excluded.updated_at""",
                (language_code, channel_id, channel_name,
                 datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def get_language_channel(self, language_code: str) -> Optional[Dict]:
        """Return channel config for a given language code."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM language_channels WHERE language_code=?",
                (language_code,),
            ).fetchone()
        return dict(row) if row else None

    def get_all_language_channels(self) -> List[Dict]:
        """Return all registered language channels."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM language_channels ORDER BY language_code"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_language_channel_stats(self, language_code: str,
                                       subscribers: int, total_views: int,
                                       video_count: int = 0) -> None:
        """Update subscriber and view counts for a language channel."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE language_channels
                   SET subscriber_count=?, total_views=?, video_count=?, updated_at=?
                   WHERE language_code=?""",
                (subscribers, total_views, video_count,
                 datetime.now(timezone.utc).isoformat(), language_code),
            )

    def save_localization_job(self, source_video_db_id: int,
                               language_code: str, job_type: str,
                               status: str, result: Dict = None,
                               error: str = "") -> int:
        """Insert or update a localization job record."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM localization_jobs "
                "WHERE source_video_db_id=? AND language_code=? AND job_type=?",
                (source_video_db_id, language_code, job_type),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE localization_jobs
                       SET status=?, result=?, error=?, completed_at=?
                       WHERE id=?""",
                    (status,
                     json.dumps(result) if result else None,
                     error,
                     now if status in ("done", "failed") else None,
                     existing["id"]),
                )
                return existing["id"]
            cur = conn.execute(
                """INSERT INTO localization_jobs
                   (source_video_db_id, language_code, job_type,
                    status, result, error, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    source_video_db_id, language_code, job_type,
                    status,
                    json.dumps(result) if result else None,
                    error, now,
                ),
            )
            return cur.lastrowid

    def get_localization_jobs(self, source_video_db_id: int) -> List[Dict]:
        """Return all localization jobs for a source video."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM localization_jobs WHERE source_video_db_id=? "
                "ORDER BY language_code, job_type",
                (source_video_db_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_localization_jobs(self, limit: int = 20) -> List[Dict]:
        """Return queued or failed localization jobs for retry."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM localization_jobs WHERE status IN ('queued','failed') "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Shorts factory ────────────────────────────────────────────────────────

    def save_short(self, data: Dict) -> int:
        """Persist a produced Short record."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO shorts
                   (source_video_db_id, source_youtube_id, short_index,
                    segment_type, virality_score, start_s, end_s, duration_s,
                    title, hook_text, platform, file_path, youtube_id,
                    status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data.get("source_video_db_id"),
                    data.get("source_youtube_id", ""),
                    data.get("short_index", 1),
                    data.get("segment_type", ""),
                    data.get("virality_score", 0.0),
                    data.get("start_s", 0.0),
                    data.get("end_s", 0.0),
                    data.get("duration_s", 0.0),
                    data.get("title", ""),
                    data.get("hook_text", ""),
                    data.get("platform", "multi"),
                    data.get("file_path", ""),
                    data.get("youtube_id", ""),
                    data.get("status", "pending"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_shorts(self, source_video_db_id: int = None,
                    platform: str = None, n: int = 50) -> List[Dict]:
        """Return Shorts, optionally filtered by source video or platform."""
        with self._conn() as conn:
            if source_video_db_id and platform:
                rows = conn.execute(
                    "SELECT * FROM shorts WHERE source_video_db_id=? AND platform=? "
                    "ORDER BY virality_score DESC LIMIT ?",
                    (source_video_db_id, platform, n),
                ).fetchall()
            elif source_video_db_id:
                rows = conn.execute(
                    "SELECT * FROM shorts WHERE source_video_db_id=? "
                    "ORDER BY virality_score DESC LIMIT ?",
                    (source_video_db_id, n),
                ).fetchall()
            elif platform:
                rows = conn.execute(
                    "SELECT * FROM shorts WHERE platform=? "
                    "ORDER BY virality_score DESC LIMIT ?",
                    (platform, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM shorts ORDER BY created_at DESC LIMIT ?", (n,)
                ).fetchall()
        return [dict(r) for r in rows]

    def get_top_shorts(self, min_score: float = 65.0, n: int = 20) -> List[Dict]:
        """Return Shorts above a virality score threshold, sorted by score."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM shorts WHERE virality_score >= ? "
                "ORDER BY virality_score DESC LIMIT ?",
                (min_score, n),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_short_analytics(self, short_id: int, views: int = 0,
                                 likes: int = 0, shares: int = 0,
                                 revenue: float = 0.0) -> None:
        """Update views/likes/shares/revenue for a Short."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE shorts
                   SET views=views+?, likes=likes+?, shares=shares+?,
                       revenue=revenue+?
                   WHERE id=?""",
                (views, likes, shares, revenue, short_id),
            )

    def update_short_youtube_id(self, short_id: int, youtube_id: str) -> None:
        """Set YouTube Shorts ID after upload."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE shorts SET youtube_id=?, status='published' WHERE id=?",
                (youtube_id, short_id),
            )

    def get_shorts_analytics(self, days: int = 7, n: int = 100) -> List[Dict]:
        """Return Short records created in the last N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM shorts WHERE created_at >= ? "
                "ORDER BY views DESC LIMIT ?",
                (cutoff, n),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_shorts_expansion(self, short_db_id: int, topic: str,
                               virality_score: float,
                               signal_type: str = "") -> int:
        """Queue a Short for expansion into a full video."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO shorts_expansion_queue
                   (short_db_id, topic, virality_score, signal_type,
                    status, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    short_db_id, topic, virality_score, signal_type,
                    "queued", datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_expansion_queue(self, status: str = "queued",
                             n: int = 20) -> List[Dict]:
        """Return Shorts queued for full-video expansion."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM shorts_expansion_queue WHERE status=? "
                "ORDER BY virality_score DESC LIMIT ?",
                (status, n),
            ).fetchall()
        return [dict(r) for r in rows]

    def short_count(self) -> int:
        """Return total number of Shorts in the database."""
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM shorts").fetchone()["n"]

    # ── Community engine ──────────────────────────────────────────────────────

    def save_comment(self, data: Dict) -> int:
        """Upsert a comment record (dedup by comment_id)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO comments
                   (youtube_id, comment_id, author, text, likes, category,
                    reply_status, reply_text, pin_score, published_at, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(comment_id) DO UPDATE SET
                       likes=excluded.likes,
                       category=excluded.category,
                       reply_text=COALESCE(excluded.reply_text, reply_text),
                       fetched_at=excluded.fetched_at""",
                (
                    data.get("youtube_id", ""),
                    data.get("comment_id", ""),
                    data.get("author", ""),
                    data.get("text", ""),
                    data.get("likes", 0),
                    data.get("category", "other"),
                    data.get("reply_status", "pending"),
                    data.get("reply_text"),
                    data.get("pin_score", 0.0),
                    data.get("published_at", ""),
                    now,
                ),
            )
            return cur.lastrowid

    def get_comments(self, youtube_id: str, category: str = None,
                     n: int = 200) -> List[Dict]:
        """Return comments for a video, optionally filtered by category."""
        with self._conn() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM comments WHERE youtube_id=? AND category=? "
                    "ORDER BY pin_score DESC, likes DESC LIMIT ?",
                    (youtube_id, category, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM comments WHERE youtube_id=? "
                    "ORDER BY pin_score DESC, likes DESC LIMIT ?",
                    (youtube_id, n),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_replies(self, limit: int = 20) -> List[Dict]:
        """Return comments queued for an automated reply."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM comments WHERE reply_status='pending' "
                "AND reply_text IS NOT NULL "
                "ORDER BY pin_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_comment_status(self, comment_id: str, reply_status: str,
                               reply_text: str = None,
                               is_pinned: bool = False,
                               is_hearted: bool = False) -> None:
        """Update reply/heart/pin state for a comment."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE comments
                   SET reply_status=?, reply_text=COALESCE(?,reply_text),
                       is_pinned=?, is_hearted=?
                   WHERE comment_id=?""",
                (reply_status, reply_text,
                 int(is_pinned), int(is_hearted), comment_id),
            )

    def save_community_post(self, data: Dict) -> int:
        """Persist a scheduled or published community post."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO community_posts
                   (post_type, content, poll_options, scheduled_at,
                    published_at, engagement_goal, youtube_post_id,
                    status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    data.get("post_type", ""),
                    data.get("content", ""),
                    json.dumps(data.get("poll_options", [])),
                    data.get("scheduled_at", ""),
                    data.get("published_at", ""),
                    data.get("engagement_goal", ""),
                    data.get("youtube_post_id", ""),
                    data.get("status", "scheduled"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_due_community_posts(self) -> List[Dict]:
        """Return community posts scheduled for now or in the past."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM community_posts "
                "WHERE status='scheduled' AND scheduled_at <= ? "
                "ORDER BY scheduled_at ASC",
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_scheduled_community_posts(self, n: int = 10) -> List[Dict]:
        """Return upcoming scheduled community posts."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM community_posts WHERE status='scheduled' "
                "ORDER BY scheduled_at ASC LIMIT ?",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_community_post_status(self, post_id: int, status: str,
                                      youtube_post_id: str = None) -> None:
        """Mark a community post as published or failed."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE community_posts
                   SET status=?, published_at=?,
                       youtube_post_id=COALESCE(?,youtube_post_id)
                   WHERE id=?""",
                (status,
                 datetime.now(timezone.utc).isoformat() if status == "published" else None,
                 youtube_post_id,
                 post_id),
            )

    def save_retention_event(self, data: Dict) -> int:
        """Record a subscriber drop/recovery event."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO retention_events
                   (timestamp, event_type, subscriber_count, change_count,
                    change_pct, culprit_video, strategy_applied, resolved)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    data.get("event_type", "drop"),
                    data.get("subscriber_count", 0),
                    data.get("change_count", 0),
                    data.get("change_pct", 0.0),
                    data.get("culprit_video", ""),
                    data.get("strategy_applied", ""),
                    int(data.get("resolved", False)),
                ),
            )
            return cur.lastrowid

    def get_retention_events(self, resolved: bool = None,
                              n: int = 20) -> List[Dict]:
        """Return retention events, optionally filtered by resolved state."""
        with self._conn() as conn:
            if resolved is None:
                rows = conn.execute(
                    "SELECT * FROM retention_events ORDER BY timestamp DESC LIMIT ?",
                    (n,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM retention_events WHERE resolved=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (int(resolved), n),
                ).fetchall()
        return [dict(r) for r in rows]

    # ── Growth hacker ─────────────────────────────────────────────────────────

    def save_collab_prospect(self, data: Dict) -> int:
        """Upsert a collaboration prospect by channel_id."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO collab_prospects
                   (channel_id, channel_name, subscriber_count, content_overlap,
                    niche, pitch_email, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(channel_id) DO UPDATE SET
                       channel_name=excluded.channel_name,
                       subscriber_count=excluded.subscriber_count,
                       pitch_email=COALESCE(excluded.pitch_email, pitch_email),
                       niche=excluded.niche""",
                (
                    data.get("channel_id", ""),
                    data.get("channel_name", ""),
                    data.get("subs", data.get("subscriber_count", 0)),
                    data.get("content_overlap", 0.0),
                    data.get("niche", ""),
                    data.get("pitch_email", ""),
                    data.get("status", "prospect"),
                    now,
                ),
            )
            return cur.lastrowid

    def get_collab_prospects(self, status: str = None,
                              n: int = 20) -> List[Dict]:
        """Return collaboration prospects, optionally filtered by status."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM collab_prospects WHERE status=? "
                    "ORDER BY subscriber_count DESC LIMIT ?",
                    (status, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM collab_prospects ORDER BY created_at DESC LIMIT ?",
                    (n,),
                ).fetchall()
        return [dict(r) for r in rows]

    def update_collab_prospect(self, channel_id: str, **kwargs) -> None:
        """Update any fields on a collab prospect by channel_id."""
        if not kwargs:
            return
        fields = ", ".join(f"{k}=?" for k in kwargs)
        vals   = list(kwargs.values()) + [channel_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE collab_prospects SET {fields} WHERE channel_id=?", vals
            )

    def save_keyword_cluster(self, data: Dict) -> int:
        """Persist a keyword cluster plan."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO keyword_clusters
                   (main_keyword, cluster_topic, opportunity_score,
                    videos_planned, videos_done, status, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    data.get("main_keyword", ""),
                    data.get("cluster_topic", ""),
                    data.get("opportunity_score", 0.0),
                    data.get("videos_planned", "[]"),
                    data.get("videos_done", 0),
                    data.get("status", "planned"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_keyword_clusters(self, status: str = None,
                              n: int = 20) -> List[Dict]:
        """Return keyword clusters, optionally filtered by status."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM keyword_clusters WHERE status=? "
                    "ORDER BY opportunity_score DESC LIMIT ?",
                    (status, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM keyword_clusters "
                    "ORDER BY opportunity_score DESC LIMIT ?",
                    (n,),
                ).fetchall()
        return [dict(r) for r in rows]

    def save_playlist(self, data: Dict) -> int:
        """Persist a strategic playlist record."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO playlists
                   (youtube_id, title, seo_title, topic_cluster,
                    video_ids, binge_trap, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    data.get("youtube_id", ""),
                    data.get("title", ""),
                    data.get("seo_title", ""),
                    data.get("topic_cluster", ""),
                    json.dumps(data.get("video_ids", [])),
                    int(data.get("binge_trap", 0)),
                    data.get("status", "active"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_playlists(self, status: str = None, n: int = 20) -> List[Dict]:
        """Return strategic playlists, optionally filtered by status."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM playlists WHERE status=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM playlists ORDER BY created_at DESC LIMIT ?",
                    (n,),
                ).fetchall()
        return [dict(r) for r in rows]

    def save_growth_target(self, data: Dict) -> int:
        """Persist a 30/60/90 day growth target model."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO growth_targets
                   (set_date, baseline_subs, posting_freq_week,
                    weekly_growth_rate, freq_multiplier,
                    day30_target, day60_target, day90_target,
                    status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    data.get("set_date", datetime.now().strftime("%Y-%m-%d")),
                    data.get("baseline_subs", 0),
                    data.get("posting_freq_week", 7),
                    data.get("weekly_growth_rate", 0.0),
                    data.get("freq_multiplier", 1.0),
                    data.get("day30_target", 0),
                    data.get("day60_target", 0),
                    data.get("day90_target", 0),
                    data.get("status", "active"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_growth_targets(self, status: str = None, n: int = 5) -> List[Dict]:
        """Return growth target records, most recent first."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM growth_targets WHERE status=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM growth_targets ORDER BY created_at DESC LIMIT ?",
                    (n,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_revenue_snapshots(self, n: int = 10) -> List[Dict]:
        """Return the N most recent revenue snapshots."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM revenue_snapshots ORDER BY date DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]


# Module-level singleton
_mem: Optional[Memory] = None

def get_memory() -> Memory:
    global _mem
    if _mem is None:
        _mem = Memory()
    return _mem
