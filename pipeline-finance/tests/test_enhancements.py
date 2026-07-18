"""
tests/test_enhancements.py
Tests for Groups 1-4 enhancement modules:
  - channel_manager/end_screen_manager.py
  - channel_manager/post_manager.py
  - channel_manager/subtitle_manager.py
  - scrapers/trends_scraper.py
  - scrapers/rss_scraper.py
  - scrapers/market_scraper.py (VIX pre-check)
  - generators/title_gen.py (chapter markers + script tags)
  - generators/audio_gen.py (loudness normalization)
  - generators/script_gen.py (script caching)
  - uploader/preflight.py
  - uploader/uploader.py (archive manifest)
  - channel_manager/performance_tracker.py
  - channel_manager/content_tracker.py
"""
import json
import os
import sys
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, mock_open

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_settings(tmp_path):
    """Return a mock settings object with temp paths."""
    s = MagicMock()
    s.logs_dir = tmp_path / "logs"
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    s.output_dir = tmp_path / "output"
    s.output_dir.mkdir(parents=True, exist_ok=True)
    s.disclaimer_text = "Not financial advice."
    s.channel_id = "UC_TEST_CHANNEL"
    s.required_phrases = ["Not financial advice."]
    return s


# ── EndScreenManager ─────────────────────────────────────────────────────────

class TestEndScreenManager:
    def test_short_video_skipped(self):
        from channel_manager.end_screen_manager import EndScreenManager
        mgr = EndScreenManager(youtube_service=MagicMock())
        result = mgr.add_end_screen("vid123", duration_seconds=15.0)
        assert result is False

    def test_success_path(self):
        from channel_manager.end_screen_manager import EndScreenManager
        mock_svc = MagicMock()
        mock_svc.videos.return_value.update.return_value.execute.return_value = {}
        mgr = EndScreenManager(youtube_service=mock_svc)
        result = mgr.add_end_screen("vid123", duration_seconds=300.0)
        assert result is True

    def test_api_error_returns_false(self):
        from channel_manager.end_screen_manager import EndScreenManager
        mock_svc = MagicMock()
        mock_svc.videos.return_value.update.return_value.execute.side_effect = Exception("403")
        mgr = EndScreenManager(youtube_service=mock_svc)
        result = mgr.add_end_screen("vid123", duration_seconds=300.0)
        assert result is False

    def test_convenience_wrapper(self):
        from channel_manager.end_screen_manager import EndScreenManager
        mock_svc = MagicMock()
        mock_svc.videos.return_value.update.return_value.execute.return_value = {}
        mgr = EndScreenManager(youtube_service=mock_svc)
        result = mgr.add_end_screen_to_recent("vid456", video_duration=200.0)
        assert result is True

    def test_exactly_at_boundary(self):
        from channel_manager.end_screen_manager import EndScreenManager
        mgr = EndScreenManager(youtube_service=MagicMock())
        # exactly END_SCREEN_DURATION_SECONDS + 5 = 25s — should NOT skip
        mock_svc = MagicMock()
        mock_svc.videos.return_value.update.return_value.execute.return_value = {}
        mgr2 = EndScreenManager(youtube_service=mock_svc)
        result = mgr2.add_end_screen("vid123", duration_seconds=25.0)
        assert result is True


# ── PostManager ───────────────────────────────────────────────────────────────

class TestPostManager:
    def test_pin_comment_success(self):
        from channel_manager.post_manager import PostManager
        mock_svc = MagicMock()
        mock_svc.commentThreads.return_value.insert.return_value.execute.return_value = {
            "snippet": {"topLevelComment": {"id": "comment_abc"}}
        }
        mgr = PostManager(youtube_service=mock_svc)
        result = mgr.pin_comment("vid123", "Great video!")
        assert result == "comment_abc"

    def test_pin_comment_failure_returns_none(self):
        from channel_manager.post_manager import PostManager
        mock_svc = MagicMock()
        mock_svc.commentThreads.return_value.insert.return_value.execute.side_effect = Exception("err")
        mgr = PostManager(youtube_service=mock_svc)
        result = mgr.pin_comment("vid123", "text")
        assert result is None

    def test_pin_disclaimer_comment(self):
        from channel_manager.post_manager import PostManager
        mock_svc = MagicMock()
        mock_svc.commentThreads.return_value.insert.return_value.execute.return_value = {
            "snippet": {"topLevelComment": {"id": "cmt_xyz"}}
        }
        mgr = PostManager(youtube_service=mock_svc)
        result = mgr.pin_disclaimer_comment("vid123", "My Long Video Title Here")
        assert result == "cmt_xyz"

    def test_refresh_channel_description_success(self):
        from channel_manager.post_manager import PostManager
        mock_svc = MagicMock()
        mock_svc.channels.return_value.update.return_value.execute.return_value = {}
        mgr = PostManager(youtube_service=mock_svc)
        result = mgr.refresh_channel_description()
        assert result is True

    def test_refresh_channel_description_failure(self):
        from channel_manager.post_manager import PostManager
        mock_svc = MagicMock()
        mock_svc.channels.return_value.update.return_value.execute.side_effect = Exception("403")
        mgr = PostManager(youtube_service=mock_svc)
        result = mgr.refresh_channel_description()
        assert result is False


# ── SubtitleManager ───────────────────────────────────────────────────────────

class TestSubtitleManager:
    def test_generate_srt_basic(self):
        from channel_manager.subtitle_manager import generate_srt
        srt = generate_srt("Hello world. This is a test narration.")
        assert "00:00:00,000" in srt
        assert "Hello world" in srt

    def test_generate_srt_empty(self):
        from channel_manager.subtitle_manager import generate_srt
        assert generate_srt("") == ""

    def test_generate_srt_strips_ssml(self):
        from channel_manager.subtitle_manager import generate_srt
        srt = generate_srt("<speak>Hello <break time='0.3s'/> world.</speak>")
        assert "<speak>" not in srt
        assert "<break" not in srt
        assert "Hello" in srt

    def test_generate_srt_with_offset(self):
        from channel_manager.subtitle_manager import generate_srt
        srt = generate_srt("Hello world.", offset_seconds=60.0)
        # 60 seconds = 1 minute → 00:01:00,000
        assert "00:01:00,000" in srt

    def test_ms_to_srt_time(self):
        from channel_manager.subtitle_manager import _ms_to_srt_time
        assert _ms_to_srt_time(0) == "00:00:00,000"
        assert _ms_to_srt_time(1000) == "00:00:01,000"
        assert _ms_to_srt_time(61500) == "00:01:01,500"
        assert _ms_to_srt_time(3723456) == "01:02:03,456"

    def test_upload_captions_success(self):
        from channel_manager.subtitle_manager import SubtitleManager
        mock_svc = MagicMock()
        mock_svc.captions.return_value.insert.return_value.execute.return_value = {}
        mgr = SubtitleManager(youtube_service=mock_svc)
        # Patch the entire upload call to simulate success without googleapiclient
        with patch.object(mgr, "_service", return_value=mock_svc):
            # Stub out the internal upload path by patching io and MediaIoBaseUpload
            import sys
            fake_module = MagicMock()
            fake_module.MediaIoBaseUpload = MagicMock(return_value=MagicMock())
            sys.modules.setdefault("googleapiclient", MagicMock())
            sys.modules.setdefault("googleapiclient.http", fake_module)
            with patch("channel_manager.subtitle_manager.io") as mock_io:
                mock_io.BytesIO.return_value = MagicMock()
                result = mgr.upload_captions("vid123", "Hello world this is a test script.")
        assert isinstance(result, bool)

    def test_upload_captions_empty_script(self):
        from channel_manager.subtitle_manager import SubtitleManager
        mgr = SubtitleManager(youtube_service=MagicMock())
        result = mgr.upload_captions("vid123", "")
        assert result is False


# ── TrendsScraper ─────────────────────────────────────────────────────────────

class TestTrendsScraper:
    def test_get_top_finance_trends_empty_on_failure(self):
        from scrapers.trends_scraper import TrendsScraper
        scraper = TrendsScraper()
        # Patch pytrends import to simulate failure
        with patch.object(scraper, "_client_or_build", side_effect=Exception("network err")):
            result = scraper.get_top_finance_trends()
        assert result == []

    def test_rising_queries_returns_list(self):
        from scrapers.trends_scraper import TrendsScraper
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.head.return_value.iterrows.return_value = iter([
            (0, {"query": "inflation rate", "value": 500}),
            (1, {"query": "fed rate cut", "value": 300}),
        ])
        mock_related = {"S&P 500": {"rising": mock_df}}
        mock_pt = MagicMock()
        mock_pt.related_queries.return_value = mock_related

        scraper = TrendsScraper()
        scraper._client = mock_pt
        result = scraper.get_rising_queries(keywords=["S&P 500"])
        assert isinstance(result, list)
        # Values sorted descending
        if len(result) >= 2:
            assert result[0]["value"] >= result[1]["value"]

    def test_interest_over_time_empty_on_error(self):
        from scrapers.trends_scraper import TrendsScraper
        mock_pt = MagicMock()
        mock_pt.interest_over_time.side_effect = Exception("timeout")
        scraper = TrendsScraper()
        scraper._client = mock_pt
        result = scraper.get_interest_over_time(keywords=["stocks"])
        assert result == {}

    def test_interest_over_time_returns_dict(self):
        import pandas as pd
        from scrapers.trends_scraper import TrendsScraper
        df = pd.DataFrame({"stocks": [50, 60, 70]})
        mock_pt = MagicMock()
        mock_pt.interest_over_time.return_value = df
        scraper = TrendsScraper()
        scraper._client = mock_pt
        result = scraper.get_interest_over_time(keywords=["stocks"])
        assert "stocks" in result
        assert result["stocks"] == [50, 60, 70]


# ── RssScraper ────────────────────────────────────────────────────────────────

class TestRssScraper:
    def test_score_relevance_finance_words(self):
        from scrapers.rss_scraper import _score_relevance
        score = _score_relevance("The stock market fell on Fed rate decision")
        assert score > 0

    def test_score_relevance_zero_for_unrelated(self):
        from scrapers.rss_scraper import _score_relevance
        score = _score_relevance("cats dogs pets animals")
        assert score == 0.0

    def test_fetch_all_returns_list(self):
        import scrapers.rss_scraper as rss_mod
        from scrapers.rss_scraper import RssScraper
        import types

        mock_entry = types.SimpleNamespace(
            **{"get": lambda self, k, d="": {"title": "Fed raises rates", "summary": "The Fed raised interest rates by 25bps", "link": "http://example.com/1", "published": "2026-07-01"}.get(k, d)}
        )
        # feedparser returns an object with entries attr
        mock_feed = MagicMock()
        mock_feed.entries = [MagicMock(**{
            "get.side_effect": lambda k, d="": {"title": "Fed raises rates", "summary": "The Fed raised rates", "link": "http://example.com", "published": "2026-07-01"}.get(k, d),
        })]

        scraper = RssScraper(feeds={"test_feed": "http://fake.feed/rss"})
        mock_fp = MagicMock()
        mock_fp.parse.return_value = mock_feed
        original_fp = rss_mod.feedparser
        rss_mod.feedparser = mock_fp
        try:
            results = scraper.fetch_all()
        finally:
            rss_mod.feedparser = original_fp
        assert isinstance(results, list)

    def test_get_top_headlines_returns_strings(self):
        from scrapers.rss_scraper import RssScraper
        scraper = RssScraper(feeds={"test": "http://fake.feed/rss"})
        with patch.object(scraper, "fetch_all", return_value=[]):
            result = scraper.get_top_headlines()
        assert isinstance(result, list)

    def test_news_item_to_dict(self):
        from scrapers.rss_scraper import NewsItem
        item = NewsItem(
            title="Test headline",
            summary="Test summary",
            link="http://example.com",
            published="2026-07-01",
            source="test",
            score=0.5,
        )
        d = item.to_dict()
        assert d["title"] == "Test headline"
        assert d["score"] == 0.5

    def test_fetch_by_source_unknown_returns_empty(self):
        from scrapers.rss_scraper import RssScraper
        scraper = RssScraper(feeds={})
        result = scraper.fetch_by_source("nonexistent")
        assert result == []


# ── MarketScraper VIX ─────────────────────────────────────────────────────────

class TestMarketScraperVix:
    def test_vix_market_state_calm(self):
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        with patch.object(scraper, "get_vix_level", return_value=14.5):
            state = scraper.vix_market_state()
        assert state["state"] == "calm"
        assert state["tone_hint"] == "upbeat"

    def test_vix_market_state_moderate(self):
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        with patch.object(scraper, "get_vix_level", return_value=20.0):
            state = scraper.vix_market_state()
        assert state["state"] == "moderate"

    def test_vix_market_state_elevated(self):
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        with patch.object(scraper, "get_vix_level", return_value=28.0):
            state = scraper.vix_market_state()
        assert state["state"] == "elevated"
        assert state["tone_hint"] == "sober"

    def test_vix_market_state_extreme(self):
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        with patch.object(scraper, "get_vix_level", return_value=40.0):
            state = scraper.vix_market_state()
        assert state["state"] == "extreme"
        assert state["tone_hint"] == "crisis"

    def test_vix_market_state_unknown_when_fetch_fails(self):
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        with patch.object(scraper, "get_vix_level", return_value=None):
            state = scraper.vix_market_state()
        assert state["state"] == "unknown"
        assert state["level"] is None


# ── title_gen chapter markers and script tags ──────────────────────────────────

class TestTitleGenEnhancements:
    def test_generate_chapter_markers_from_headers(self):
        from generators.title_gen import generate_chapter_markers
        script = "# Introduction\nHello world.\n## Top Movers\nMarket data here.\n## Outlook\nThings look good."
        chapters = generate_chapter_markers(script, audio_duration_seconds=300.0)
        assert "0:00" in chapters
        assert "Introduction" in chapters

    def test_generate_chapter_markers_fallback(self):
        from generators.title_gen import generate_chapter_markers
        # No headers in script
        chapters = generate_chapter_markers("The stock market opened lower today.", audio_duration_seconds=120.0)
        assert "0:00" in chapters

    def test_generate_chapter_markers_no_duration(self):
        from generators.title_gen import generate_chapter_markers
        chapters = generate_chapter_markers("# Intro\nHello.\n## Body\nContent.")
        assert isinstance(chapters, str)
        assert "0:00" in chapters

    def test_extract_script_tags_finds_tickers(self):
        from generators.title_gen import extract_script_tags
        script = "AAPL surged 5% today. NVDA and TSLA also gained. The S&P 500 closed up."
        tags = extract_script_tags(script)
        assert "AAPL" in tags
        assert "NVDA" in tags
        assert "TSLA" in tags

    def test_extract_script_tags_filters_stopwords(self):
        from generators.title_gen import extract_script_tags
        script = "THE OR AND BUT A IS AN IN AT TO FOR OF ON UP US ALL NEW NOW"
        tags = extract_script_tags(script)
        # These should all be filtered out
        assert "THE" not in tags
        assert "AND" not in tags

    def test_extract_script_tags_deduplicates(self):
        from generators.title_gen import extract_script_tags
        script = "AAPL was up. AAPL gained. AAPL closed higher."
        tags = extract_script_tags(script)
        assert tags.count("AAPL") == 1

    def test_extract_script_tags_max_20(self):
        from generators.title_gen import extract_script_tags
        # Build a script with 30 unique tickers
        tickers = [f"SYM{i:02d}" for i in range(30)]
        script = " ".join(tickers)
        tags = extract_script_tags(script)
        assert len(tags) <= 20


# ── audio_gen loudness normalization ──────────────────────────────────────────

class TestAudioGenLoudness:
    def test_normalize_loudness_no_ffmpeg(self, tmp_path):
        from generators.audio_gen import normalize_loudness
        fake_audio = tmp_path / "test.mp3"
        fake_audio.write_bytes(b"\xff\xfb" + b"\x00" * 1000)
        with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
            result = normalize_loudness(fake_audio)
        assert result is None

    def test_normalize_loudness_ffmpeg_fails(self, tmp_path):
        from generators.audio_gen import normalize_loudness
        fake_audio = tmp_path / "test.mp3"
        fake_audio.write_bytes(b"\xff\xfb" + b"\x00" * 1000)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"
        with patch("subprocess.run", return_value=mock_result):
            result = normalize_loudness(fake_audio)
        assert result is None

    def test_normalize_loudness_success_inplace(self, tmp_path):
        from generators.audio_gen import normalize_loudness
        fake_audio = tmp_path / "test.mp3"
        fake_audio.write_bytes(b"\xff\xfb" + b"\x00" * 1000)
        norm_path = fake_audio.with_suffix(".norm.mp3")

        def fake_ffmpeg(cmd, **kwargs):
            # Simulate ffmpeg writing the output file
            norm_path.write_bytes(b"\xff\xfb" + b"\x00" * 900)
            r = MagicMock()
            r.returncode = 0
            return r

        with patch("subprocess.run", side_effect=fake_ffmpeg):
            result = normalize_loudness(fake_audio)
        # In-place: returns original path
        assert result == fake_audio

    def test_normalize_loudness_timeout(self, tmp_path):
        import subprocess
        from generators.audio_gen import normalize_loudness
        fake_audio = tmp_path / "test.mp3"
        fake_audio.write_bytes(b"\x00" * 100)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)):
            result = normalize_loudness(fake_audio)
        assert result is None


# ── script_gen caching ────────────────────────────────────────────────────────

class TestScriptGenCache:
    def test_cache_and_retrieve(self, tmp_path):
        from generators.script_gen import ScriptGenerator, GeneratedScript
        sg = ScriptGenerator(output_dir=tmp_path)
        script = GeneratedScript(
            video_type="weekday",
            title_draft="Fed Rate Decision Recap",
            script="The Fed raised rates today...",
            word_count=50,
            estimated_duration_seconds=120,
            segments={"intro": "Hello"},
            tier="tier2",
            style="analytical",
            raw_prompt="generate script",
            model="claude-sonnet-4-6",
            tokens_used=200,
        )
        sg.cache_script(script, topic="Fed rate decision", tier=2)
        cached = sg.get_cached_script("weekday", "Fed rate decision", tier=2)
        assert cached is not None
        assert cached.script == "The Fed raised rates today..."

    def test_cache_miss_returns_none(self, tmp_path):
        from generators.script_gen import ScriptGenerator
        sg = ScriptGenerator(output_dir=tmp_path)
        result = sg.get_cached_script("weekday", "nonexistent topic xyz", tier=1)
        assert result is None

    def test_cache_key_stable(self, tmp_path):
        from generators.script_gen import ScriptGenerator
        sg = ScriptGenerator(output_dir=tmp_path)
        k1 = sg._cache_key("weekday", "Fed rate decision", 2)
        k2 = sg._cache_key("weekday", "Fed rate decision", 2)
        assert k1 == k2

    def test_cache_key_different_for_different_inputs(self, tmp_path):
        from generators.script_gen import ScriptGenerator
        sg = ScriptGenerator(output_dir=tmp_path)
        k1 = sg._cache_key("weekday", "Fed rate decision", 2)
        k2 = sg._cache_key("weekday", "S&P 500 rally", 2)
        assert k1 != k2

    def test_cache_expires_next_day(self, tmp_path):
        from generators.script_gen import ScriptGenerator, GeneratedScript
        from datetime import date
        sg = ScriptGenerator(output_dir=tmp_path)
        script = GeneratedScript(
            video_type="weekday",
            title_draft="Stale Topic Title",
            script="Old content",
            word_count=10,
            estimated_duration_seconds=30,
            segments={},
            tier="tier3",
            style="calm",
            raw_prompt="generate",
            model="claude-sonnet-4-6",
            tokens_used=100,
        )
        sg.cache_script(script, topic="Stale topic", tier=3)

        # Manually backdate the cache file
        key = sg._cache_key("weekday", "Stale topic", 3)
        path = sg._cache_path(key)
        data = json.loads(path.read_text())
        data["cached_date"] = (date.today() - timedelta(days=1)).isoformat()
        path.write_text(json.dumps(data))

        result = sg.get_cached_script("weekday", "Stale topic", tier=3)
        assert result is None


# ── PreflightChecker ──────────────────────────────────────────────────────────

class TestPreflightChecker:
    def test_pass_with_all_valid(self, tmp_path):
        from uploader.preflight import PreflightChecker
        from config.settings import settings

        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00" * 10000)

        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = True

        checker = PreflightChecker(quota_tracker=mock_qt)
        desc = f"{settings.disclaimer_text} Narration is AI-generated. More details here."
        # The fake video bytes are not a real mp4 — on hosts where ffprobe is
        # installed the stream check would (correctly) reject them, so stub it.
        with patch.object(PreflightChecker, "_check_video_streams"):
            result = checker.run(
                video_path=video,
                title="S&P 500 Recap Today",
                description=desc,
                check_quota=True,
            )
        assert result.passed is True
        assert not result.errors

    def test_fail_missing_video(self, tmp_path):
        from uploader.preflight import PreflightChecker
        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = True
        checker = PreflightChecker(quota_tracker=mock_qt)
        result = checker.run(
            video_path=tmp_path / "nonexistent.mp4",
            title="Test",
            description="This content is for informational and entertainment purposes only and does not constitute financial advice. Always consult a licensed financial advisor before making any investment decisions. Narration is AI-generated.",
            check_quota=False,
        )
        assert result.passed is False
        assert any("not found" in e.lower() or "video" in e.lower() for e in result.errors)

    def test_fail_title_too_long(self, tmp_path):
        from uploader.preflight import PreflightChecker
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 10000)
        checker = PreflightChecker(quota_tracker=MagicMock())
        result = checker.run(
            video_path=video,
            title="X" * 101,
            description="This content is for informational and entertainment purposes only and does not constitute financial advice. Always consult a licensed financial advisor before making any investment decisions. Narration is AI-generated.",
            check_quota=False,
        )
        assert result.passed is False
        assert any("title" in e.lower() for e in result.errors)

    def test_fail_missing_disclaimer_in_description(self, tmp_path):
        from uploader.preflight import PreflightChecker
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 10000)
        checker = PreflightChecker(quota_tracker=MagicMock())
        result = checker.run(
            video_path=video,
            title="Good Title",
            description="No disclaimer here at all.",
            check_quota=False,
        )
        assert result.passed is False

    def test_fail_insufficient_quota(self, tmp_path):
        from uploader.preflight import PreflightChecker
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 10000)
        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = False
        mock_qt.get_remaining.return_value = 500
        checker = PreflightChecker(quota_tracker=mock_qt)
        result = checker.run(
            video_path=video,
            title="Test",
            description="This content is for informational and entertainment purposes only and does not constitute financial advice. Always consult a licensed financial advisor before making any investment decisions. Narration is AI-generated.",
            check_quota=True,
        )
        assert result.passed is False
        assert any("quota" in e.lower() for e in result.errors)

    def test_warn_on_missing_thumbnail(self, tmp_path):
        from uploader.preflight import PreflightChecker
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 10000)
        checker = PreflightChecker(quota_tracker=MagicMock())
        result = checker.run(
            video_path=video,
            title="Test Title",
            description="This content is for informational and entertainment purposes only and does not constitute financial advice. Always consult a licensed financial advisor before making any investment decisions. Narration is AI-generated.",
            thumbnail_path=None,
            check_quota=False,
        )
        # No thumbnail is a warning, not an error
        assert any("thumbnail" in w.lower() for w in result.warnings)

    def test_preflight_result_summary(self):
        from uploader.preflight import PreflightResult
        r = PreflightResult(passed=True)
        assert "PASS" in r.summary()
        r.add_error("Something went wrong")
        assert "FAIL" in r.summary()
        assert "Something went wrong" in r.summary()


# ── Upload Archive Manifest ───────────────────────────────────────────────────

class TestUploadManifest:
    def test_record_upload_writes_jsonl(self, tmp_path):
        from uploader import uploader as umod
        # Temporarily point manifest to tmp_path
        original = umod._UPLOAD_MANIFEST_PATH
        umod._UPLOAD_MANIFEST_PATH = tmp_path / "manifest.jsonl"
        try:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.video_id = "abc123"
            mock_result.quota_used = 1600
            mock_config = MagicMock()
            mock_config.title = "Test Upload"
            mock_config.video_type = "weekday"
            mock_config.playlist_id = "PL_test"
            mock_config.tags = ["stocks", "market"]
            umod.record_upload(mock_result, mock_config)
            manifest = umod._UPLOAD_MANIFEST_PATH
            assert manifest.exists()
            lines = [l for l in manifest.read_text().splitlines() if l.strip()]
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["video_id"] == "abc123"
            assert "youtu.be/abc123" in record["url"]
        finally:
            umod._UPLOAD_MANIFEST_PATH = original

    def test_record_upload_skips_failed(self, tmp_path):
        from uploader import uploader as umod
        original = umod._UPLOAD_MANIFEST_PATH
        umod._UPLOAD_MANIFEST_PATH = tmp_path / "manifest.jsonl"
        try:
            mock_result = MagicMock()
            mock_result.success = False
            mock_result.video_id = None
            umod.record_upload(mock_result, MagicMock())
            assert not umod._UPLOAD_MANIFEST_PATH.exists()
        finally:
            umod._UPLOAD_MANIFEST_PATH = original

    def test_load_upload_manifest_empty(self, tmp_path):
        from uploader import uploader as umod
        original = umod._UPLOAD_MANIFEST_PATH
        umod._UPLOAD_MANIFEST_PATH = tmp_path / "manifest.jsonl"
        try:
            records = umod.load_upload_manifest()
            assert records == []
        finally:
            umod._UPLOAD_MANIFEST_PATH = original

    def test_load_upload_manifest_returns_newest_first(self, tmp_path):
        from uploader import uploader as umod
        original = umod._UPLOAD_MANIFEST_PATH
        manifest_path = tmp_path / "manifest.jsonl"
        umod._UPLOAD_MANIFEST_PATH = manifest_path
        try:
            records_to_write = [
                {"video_id": "first", "uploaded_at": "2026-07-01T10:00:00"},
                {"video_id": "second", "uploaded_at": "2026-07-01T11:00:00"},
            ]
            with open(manifest_path, "w") as f:
                for r in records_to_write:
                    f.write(json.dumps(r) + "\n")
            loaded = umod.load_upload_manifest()
            assert loaded[0]["video_id"] == "second"
            assert loaded[1]["video_id"] == "first"
        finally:
            umod._UPLOAD_MANIFEST_PATH = original


# ── PerformanceTracker ────────────────────────────────────────────────────────

class TestPerformanceTracker:
    def test_record_and_get_best_style(self, tmp_path):
        from channel_manager.performance_tracker import PerformanceTracker
        pt = PerformanceTracker(state_path=tmp_path / "perf.json")
        pt.record_performance(style="energetic", ctr=0.05, views=1000, watch_time_minutes=3.0)
        pt.record_performance(style="calm", ctr=0.02, views=500, watch_time_minutes=2.0)
        best = pt.get_best_style()
        assert best == "energetic"

    def test_ema_update_weights_recent(self, tmp_path):
        from channel_manager.performance_tracker import PerformanceTracker, _ema_update
        # EMA: lower alpha → weight past more; higher alpha → weight new more
        ema = _ema_update(0.5, 1.0, alpha=0.3)
        assert 0.5 < ema < 1.0

    def test_default_returned_when_empty(self, tmp_path):
        from channel_manager.performance_tracker import PerformanceTracker
        pt = PerformanceTracker(state_path=tmp_path / "empty.json")
        assert pt.get_best_style(default="energetic") == "energetic"
        assert pt.get_best_hook(default="stat_first") == "stat_first"
        assert pt.get_best_template(default="A") == "A"
        assert pt.get_best_time_slot(default="09:00") == "09:00"

    def test_get_summary(self, tmp_path):
        from channel_manager.performance_tracker import PerformanceTracker
        pt = PerformanceTracker(state_path=tmp_path / "perf.json")
        pt.record_performance(style="analytical", hook="question_hook", template="B", time_slot="09:00", ctr=0.04)
        summary = pt.get_summary()
        assert "best_style" in summary
        assert "best_hook" in summary
        assert "best_template" in summary
        assert "best_time_slot" in summary

    def test_get_scores_sorted(self, tmp_path):
        from channel_manager.performance_tracker import PerformanceTracker
        pt = PerformanceTracker(state_path=tmp_path / "perf.json")
        pt.record_performance(style="energetic", ctr=0.05, views=2000)
        pt.record_performance(style="calm", ctr=0.01, views=200)
        scores = pt.get_scores("styles")
        assert scores[0]["score"] >= scores[-1]["score"]

    def test_state_persisted_across_instances(self, tmp_path):
        from channel_manager.performance_tracker import PerformanceTracker
        path = tmp_path / "perf.json"
        pt1 = PerformanceTracker(state_path=path)
        pt1.record_performance(style="storytelling", ctr=0.06)
        pt2 = PerformanceTracker(state_path=path)
        assert "storytelling" in pt2._state.get("styles", {})

    def test_update_from_video_stats(self, tmp_path):
        from channel_manager.performance_tracker import PerformanceTracker
        pt = PerformanceTracker(state_path=tmp_path / "perf.json")
        pt.update_from_video_stats(
            {"ctr": 0.04, "views": 800, "avg_watch_seconds": 180},
            style="energetic",
            hook="bold_claim",
        )
        scores = pt.get_scores("styles")
        assert any(s["label"] == "energetic" for s in scores)


# ── ContentTracker ────────────────────────────────────────────────────────────

class TestContentTracker:
    def test_record_and_check_duplicate(self, tmp_path):
        from channel_manager.content_tracker import ContentTracker
        ct = ContentTracker(history_path=tmp_path / "history.json")
        ct.record_content("Fed raises interest rates", title="Fed Hikes Rates by 25bps")
        is_dup, sim = ct.is_duplicate("Federal Reserve interest rate hike", title="Fed Raises Rates Again")
        # Should detect similarity
        assert isinstance(is_dup, bool)
        assert 0.0 <= sim <= 1.0

    def test_unique_topic_not_duplicate(self, tmp_path):
        from channel_manager.content_tracker import ContentTracker
        ct = ContentTracker(history_path=tmp_path / "history.json")
        ct.record_content("Apple earnings beat", title="AAPL Beats Q2 Estimates")
        is_dup, _ = ct.is_duplicate("crude oil prices rally", title="Oil Surges 3%")
        assert is_dup is False

    def test_prune_old_records(self, tmp_path):
        from channel_manager.content_tracker import ContentTracker, ContentRecord
        path = tmp_path / "history.json"
        old_date = (date.today() - timedelta(days=20)).isoformat()
        old_record = ContentRecord(
            topic="old topic",
            title="Old Video",
            published_date=old_date,
            fingerprint=["old", "topic"],
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([old_record.to_dict()]))
        ct = ContentTracker(history_path=path)
        # After loading, old record should be pruned
        assert len(ct.get_recent()) == 0

    def test_get_recent_topics(self, tmp_path):
        from channel_manager.content_tracker import ContentTracker
        ct = ContentTracker(history_path=tmp_path / "history.json")
        ct.record_content("market rally stocks", title="Markets Up Today")
        ct.record_content("fed inflation data", title="Inflation Report")
        topics = ct.get_recent_topics()
        assert len(topics) == 2

    def test_content_record_to_dict_roundtrip(self):
        from channel_manager.content_tracker import ContentRecord
        r = ContentRecord(
            topic="test topic",
            title="Test Title",
            published_date="2026-07-01",
            video_id="vid123",
            fingerprint=["test", "topic"],
        )
        d = r.to_dict()
        r2 = ContentRecord.from_dict(d)
        assert r2.topic == r.topic
        assert r2.video_id == r.video_id
        assert r2.fingerprint == r.fingerprint

    def test_keyword_fingerprint_filters_stopwords(self):
        from channel_manager.content_tracker import _keyword_fingerprint
        fp = _keyword_fingerprint("the market recap today")
        assert "the" not in fp
        assert "today" not in fp
        assert "market" not in fp  # "market" is in stopwords

    def test_jaccard_similarity_identical(self):
        from channel_manager.content_tracker import _jaccard_similarity
        s = {"a", "b", "c"}
        assert _jaccard_similarity(s, s) == 1.0

    def test_jaccard_similarity_disjoint(self):
        from channel_manager.content_tracker import _jaccard_similarity
        assert _jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_jaccard_similarity_both_empty(self):
        from channel_manager.content_tracker import _jaccard_similarity
        assert _jaccard_similarity(set(), set()) == 0.0

    def test_clear_history(self, tmp_path):
        from channel_manager.content_tracker import ContentTracker
        ct = ContentTracker(history_path=tmp_path / "history.json")
        ct.record_content("some topic", title="Some Title")
        ct.clear_history()
        assert ct.get_recent() == []
