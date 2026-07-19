"""
tests/test_phase1.py
Phase 1 content-spec implementation tests:
  - Video length retune (240s max, 420-500 word prompts)
  - Day-themed Short rotation (scheduler/short_pipeline.py)
  - New scheduler jobs (midday_short, saturday_short, 17:15 postmarket)
  - Description metadata footer (data cutoff + sources)
  - upload_short manifest recording + video_type
"""
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Length retune ─────────────────────────────────────────────────────────────

class TestLengthRetune:
    def test_audio_limits_match_spec(self):
        from generators.audio_gen import MIN_AUDIO_SECONDS, MAX_AUDIO_SECONDS
        assert MIN_AUDIO_SECONDS == 180
        assert MAX_AUDIO_SECONDS == 240   # spec: 4:00 hard cap

    def test_prompts_use_new_word_range(self):
        from pathlib import Path
        text = (Path(__file__).parent.parent / "config" / "prompts.py").read_text()
        assert "420-500 word" in text
        assert "280-420" not in text     # old long-form range fully removed
        assert "280-350" not in text     # old Sunday range fully removed

    def test_shorts_card_word_range_updated(self):
        from pathlib import Path
        text = (Path(__file__).parent.parent / "config" / "prompts.py").read_text()
        assert "75-110 words" in text

    def test_500_words_fits_four_minutes(self):
        # 500 words at 140 WPM narration = 3:34 — must fit inside the cap
        from generators.audio_gen import MAX_AUDIO_SECONDS
        assert (500 / 140) * 60 < MAX_AUDIO_SECONDS


# ── Theme rotation ────────────────────────────────────────────────────────────

class TestThemeRotation:
    def test_all_six_days_have_themes(self):
        from scheduler.short_pipeline import get_todays_theme
        for wd in range(6):   # Mon..Sat
            theme = get_todays_theme(wd)
            assert theme is not None, f"weekday {wd} missing theme"
            assert theme["name"]
            assert theme["title"]
            assert callable(theme["context"])
            assert theme["brief"]

    def test_sunday_has_no_theme(self):
        from scheduler.short_pipeline import get_todays_theme
        assert get_todays_theme(6) is None

    def test_expected_theme_names(self):
        from scheduler.short_pipeline import get_todays_theme
        expected = {
            0: "Three Stocks to Watch",
            1: "Market News Explained",
            2: "Economic Report Explained",
            3: "Personal Finance Tip",
            4: "Week in 60 Seconds",
            5: "Finance Explained Simply",
        }
        for wd, name in expected.items():
            assert get_todays_theme(wd)["name"] == name

    def test_rotate_is_deterministic_daily(self):
        from scheduler.short_pipeline import _rotate, _ECON_TOPICS
        assert _rotate(_ECON_TOPICS) == _rotate(_ECON_TOPICS)
        assert _rotate(_ECON_TOPICS) in _ECON_TOPICS

    def test_topic_lists_nonempty(self):
        from scheduler.short_pipeline import _ECON_TOPICS, _FINANCE_TIPS, _EVERGREEN_TOPICS
        assert len(_ECON_TOPICS) >= 5
        assert len(_FINANCE_TIPS) >= 5
        assert len(_EVERGREEN_TOPICS) >= 5


class TestRunThemedShort:
    def test_skips_sunday(self):
        from scheduler.short_pipeline import run_themed_short
        assert run_themed_short(weekday=6) is None

    def test_tuesday_skips_without_news(self):
        from scheduler import short_pipeline as sp
        theme = sp.get_todays_theme(1)
        with patch.object(sp, "get_todays_theme", return_value={**theme, "context": lambda: None}):
            assert sp.run_themed_short(weekday=1) is None

    def test_script_failure_returns_none(self):
        from scheduler import short_pipeline as sp
        theme = {**sp.get_todays_theme(5), "context": lambda: "What is an ETF?"}
        with patch.object(sp, "get_todays_theme", return_value=theme), \
             patch.object(sp, "generate_short_script", return_value=None):
            assert sp.run_themed_short(weekday=5) is None

    def test_build_without_upload(self, tmp_path):
        from scheduler import short_pipeline as sp
        theme = {**sp.get_todays_theme(5), "context": lambda: "What is an ETF?"}
        built = MagicMock()
        built.path = tmp_path / "short.mp4"

        mock_compliance = MagicMock(passed=True, risk_level="low")
        with patch.object(sp, "get_todays_theme", return_value=theme), \
             patch.object(sp, "generate_short_script",
                          return_value="[CARD 1] x\n[CARD 2] y\n[CARD 3] z\n[CARD 4] a\n[CARD 5] b"), \
             patch("generators.compliance_filter.check_compliance", return_value=mock_compliance), \
             patch("builders.shorts_builder.ShortsBuilder") as MockSB:
            MockSB.return_value.build_short_from_script.return_value = built
            result = sp.run_themed_short(weekday=5, upload=False)
        assert result == str(built.path)

    def test_high_compliance_risk_blocks(self):
        from scheduler import short_pipeline as sp
        theme = {**sp.get_todays_theme(3), "context": lambda: "budgeting"}
        bad = MagicMock(passed=False, risk_level="high", issues=["guaranteed returns"])
        with patch.object(sp, "get_todays_theme", return_value=theme), \
             patch.object(sp, "generate_short_script", return_value="[CARD 1] buy now guaranteed"), \
             patch("generators.compliance_filter.check_compliance", return_value=bad), \
             patch("generators.compliance_filter.auto_fix_script", return_value="fixed"):
            assert sp.run_themed_short(weekday=3) is None

    def test_upload_respects_quota(self, tmp_path):
        from scheduler import short_pipeline as sp
        theme = {**sp.get_todays_theme(5), "context": lambda: "What is an ETF?"}
        built = MagicMock()
        built.path = tmp_path / "short.mp4"
        ok = MagicMock(passed=True, risk_level="low")
        with patch.object(sp, "get_todays_theme", return_value=theme), \
             patch.object(sp, "generate_short_script", return_value="[CARD 1] x"), \
             patch("generators.compliance_filter.check_compliance", return_value=ok), \
             patch("builders.shorts_builder.ShortsBuilder") as MockSB, \
             patch("uploader.quota_tracker.QuotaTracker") as MockQT:
            MockSB.return_value.build_short_from_script.return_value = built
            MockQT.return_value.can_upload.return_value = False
            assert sp.run_themed_short(weekday=5, upload=True) is None


# ── Metadata footer ───────────────────────────────────────────────────────────

class TestMetadataFooter:
    def test_footer_contains_cutoff_and_sources(self):
        from generators.title_gen import build_metadata_footer
        footer = build_metadata_footer()
        assert "Data as of:" in footer
        assert "Sources:" in footer
        assert "Yahoo Finance" in footer
        assert "ET" in footer

    def test_footer_custom_sources(self):
        from generators.title_gen import build_metadata_footer
        footer = build_metadata_footer(sources=["Test Source A", "Test Source B"])
        assert "Test Source A, Test Source B" in footer
        assert "Yahoo Finance" not in footer

    def test_footer_includes_current_year(self):
        from generators.title_gen import build_metadata_footer
        from datetime import datetime
        assert str(datetime.now().year) in build_metadata_footer()


# ── upload_short manifest + video_type ────────────────────────────────────────

class TestUploadShortManifest:
    def test_upload_short_records_manifest(self, tmp_path):
        from uploader import uploader as umod
        original = umod._UPLOAD_MANIFEST_PATH
        umod._UPLOAD_MANIFEST_PATH = tmp_path / "m.jsonl"
        try:
            video = tmp_path / "s.mp4"
            video.write_bytes(b"\x00" * 5000)

            good = umod.UploadResult(
                success=True, video_id="short1", video_url="u",
                title="t", upload_time_seconds=1.0, file_size_mb=0.1,
            )
            mock_qt = MagicMock()
            mock_qt.can_upload.return_value = True
            up = umod.YouTubeUploader(mock_qt)
            with patch.object(umod, "upload_full", return_value=good), \
                 patch.object(up, "_check_quota", return_value=True), \
                 patch.object(up, "_enforce_upload_gap"):
                result = up.upload_short(video, "Title", "desc", ["tag"])

            assert result.success
            record = json.loads(umod._UPLOAD_MANIFEST_PATH.read_text().strip())
            assert record["video_id"] == "short1"
            assert record["video_type"] == "shorts"
        finally:
            umod._UPLOAD_MANIFEST_PATH = original


# ── Scheduler job registration (Phase 1 additions) ────────────────────────────

class TestPhase1SchedulerJobs:
    def test_new_jobs_registered_and_postmarket_moved(self):
        from scheduler import master_scheduler as ms

        registered: dict[str, object] = {}

        class FakeScheduler:
            def __init__(self, timezone=None):
                pass
            def add_job(self, fn, trigger, id=None, name=None, **kw):
                registered[id] = trigger
            def get_jobs(self):
                return []
            def start(self):
                raise KeyboardInterrupt
            def shutdown(self):
                pass

        import sys
        import types
        captured_crons: list[dict] = []

        def fake_cron(**kwargs):
            captured_crons.append(kwargs)
            return kwargs

        fake_blocking = types.ModuleType("apscheduler.schedulers.blocking")
        fake_blocking.BlockingScheduler = FakeScheduler
        fake_cron_mod = types.ModuleType("apscheduler.triggers.cron")
        fake_cron_mod.CronTrigger = fake_cron
        modules = {
            "apscheduler": types.ModuleType("apscheduler"),
            "apscheduler.schedulers": types.ModuleType("apscheduler.schedulers"),
            "apscheduler.schedulers.blocking": fake_blocking,
            "apscheduler.triggers": types.ModuleType("apscheduler.triggers"),
            "apscheduler.triggers.cron": fake_cron_mod,
        }
        with patch.dict(sys.modules, modules), patch.object(ms, "_setup_logging"):
            ms.start_scheduler()

        assert "midday_short" in registered
        assert "saturday_short" in registered
        # Post-market moved to 17:15 per spec
        postmarket = registered["weekday_postmarket"]
        assert postmarket["hour"] == 17 and postmarket["minute"] == 15
        # Midday short is 12:30 Mon-Fri
        midday = registered["midday_short"]
        assert midday["hour"] == 12 and midday["minute"] == 30
        assert midday["day_of_week"] == "mon-fri"
        # Saturday short is 11:00 Sat
        sat = registered["saturday_short"]
        assert sat["hour"] == 11 and sat["day_of_week"] == "sat"

    def test_themed_short_job_wrapper_handles_failure(self):
        from scheduler import master_scheduler as ms
        with patch("scheduler.short_pipeline.run_themed_short",
                   side_effect=Exception("boom")):
            ms.run_themed_short_job()   # must not raise


# ── Legal footer + disclaimer card (channel legal policy) ─────────────────────

class TestLegalFooter:
    def test_every_description_gets_legal_block(self):
        from uploader.uploader import UploadConfig
        cfg = UploadConfig(title="T", description="Today's market recap.", tags=[])
        desc = cfg._compliance_description()
        assert "FINANCIAL DISCLAIMER" in desc
        assert "COPYRIGHT & FAIR USE NOTICE" in desc
        assert "© 2026 Drift Wire326" in desc
        assert "AI-assisted narration" in desc
        assert "Narration is AI-generated." in desc

    def test_shorts_description_gets_legal_block(self):
        from uploader.uploader import UploadConfig
        cfg = UploadConfig(title="S", description="Quick update.", tags=[],
                           video_type="shorts")
        desc = cfg._compliance_description()
        assert "COPYRIGHT & FAIR USE NOTICE" in desc

    def test_legal_block_not_duplicated(self):
        from uploader.uploader import UploadConfig
        from config.settings import settings
        cfg = UploadConfig(
            title="T",
            description="Recap. " + settings.legal_footer,
            tags=[],
        )
        desc = cfg._compliance_description()
        assert desc.count("COPYRIGHT & FAIR USE NOTICE") == 1

    def test_description_stays_within_youtube_limit(self):
        from uploader.uploader import UploadConfig
        cfg = UploadConfig(title="T", description="x" * 6000, tags=[])
        desc = cfg._compliance_description()
        assert len(desc) <= 5000
        assert "COPYRIGHT & FAIR USE NOTICE" in desc   # footer survives truncation

    def test_disclaimer_card_renders(self, tmp_path):
        from builders import video_builder as vb
        from config.settings import settings
        original = settings.output_dir
        settings.output_dir = tmp_path
        try:
            card = vb._render_disclaimer_card(1280, 720)
            assert card is not None and card.exists()
            assert card.stat().st_size > 5000
        finally:
            settings.output_dir = original

    def test_card_lines_have_required_elements(self):
        from config.settings import DISCLAIMER_CARD_LINES
        joined = " ".join(DISCLAIMER_CARD_LINES)
        assert "NOT FINANCIAL ADVICE" in joined
        assert "Fair Use" in joined
        assert "Drift Wire326" in joined
        assert "2026" in joined


# ── Round channel-logo badge ──────────────────────────────────────────────────

class TestLogoOverlay:
    def _with_tmp_branding(self, tmp_path):
        from builders import logo_overlay as lo
        originals = (lo.BRANDING_DIR, lo.LOGO_PATH)
        lo.BRANDING_DIR = tmp_path
        lo.LOGO_PATH = tmp_path / "channel_logo.png"
        return lo, originals

    def _restore(self, lo, originals):
        lo.BRANDING_DIR, lo.LOGO_PATH = originals

    def _make_square_logo(self, path, size=160):
        from PIL import Image
        Image.new("RGB", (size, size), color=(90, 40, 200)).save(path)

    def test_round_logo_from_existing_file(self, tmp_path):
        lo, originals = self._with_tmp_branding(tmp_path)
        try:
            self._make_square_logo(lo.LOGO_PATH)
            with patch.object(lo, "ensure_channel_logo", return_value=lo.LOGO_PATH):
                round_png = lo.get_round_logo(120)
            assert round_png is not None and round_png.exists()

            from PIL import Image
            img = Image.open(round_png).convert("RGBA")
            assert img.size == (120, 120)
            # Corners must be transparent (circular crop)
            assert img.getpixel((2, 2))[3] == 0
            assert img.getpixel((117, 2))[3] == 0
            # Center must be opaque
            assert img.getpixel((60, 60))[3] > 200
        finally:
            self._restore(lo, originals)

    def test_round_logo_cached_per_size(self, tmp_path):
        lo, originals = self._with_tmp_branding(tmp_path)
        try:
            self._make_square_logo(lo.LOGO_PATH)
            with patch.object(lo, "ensure_channel_logo", return_value=lo.LOGO_PATH):
                first = lo.get_round_logo(100)
                second = lo.get_round_logo(100)
            assert first == second
            assert (tmp_path / "channel_logo_round_100.png").exists()
        finally:
            self._restore(lo, originals)

    def test_missing_logo_returns_none_gracefully(self, tmp_path):
        lo, originals = self._with_tmp_branding(tmp_path)
        try:
            with patch.object(lo, "ensure_channel_logo", return_value=None):
                assert lo.get_round_logo(100) is None
        finally:
            self._restore(lo, originals)

    def test_ensure_logo_uses_existing_file_without_api(self, tmp_path):
        lo, originals = self._with_tmp_branding(tmp_path)
        try:
            self._make_square_logo(lo.LOGO_PATH, size=400)   # >1KB
            with patch("uploader.uploader._get_authenticated_service") as mock_svc:
                result = lo.ensure_channel_logo()
            assert result == lo.LOGO_PATH
            mock_svc.assert_not_called()
        finally:
            self._restore(lo, originals)

    def test_ensure_logo_api_failure_is_non_fatal(self, tmp_path):
        lo, originals = self._with_tmp_branding(tmp_path)
        try:
            with patch("uploader.uploader._get_authenticated_service",
                       side_effect=Exception("no auth")):
                assert lo.ensure_channel_logo() is None
        finally:
            self._restore(lo, originals)


# ── Visual design v2: slides + B-roll ─────────────────────────────────────────

def _fake_market():
    from types import SimpleNamespace
    def snap(sym, price, pct):
        return SimpleNamespace(symbol=sym, name=sym, price=price, change_pct=pct)
    return SimpleNamespace(
        sp500=snap("SPY", 743.29, -0.99),
        nasdaq=snap("QQQ", 695.33, -1.50),
        dow=snap("DIA", 520.81, -0.74),
        vix=snap("^VIX", 18.77, 2.10),
        top_gainers=[snap("CVX", 187.38, 1.91), snap("XOM", 147.36, 0.97)],
        top_losers=[snap("META", 646.01, -2.79), snap("GS", 1065.22, -2.76)],
    )


class TestSlideRenderer:
    def _tmp_slides(self, tmp_path):
        from builders import slide_renderer as sr
        original = sr.SLIDES_DIR
        sr.SLIDES_DIR = tmp_path
        return sr, original

    def test_intro_slide_renders(self, tmp_path):
        sr, original = self._tmp_slides(tmp_path)
        try:
            out = sr.render_intro_slide("S&P -0.99%: Why Tech Sold Off Hard Today")
            assert out is not None and out.exists() and out.stat().st_size > 10000
        finally:
            sr.SLIDES_DIR = original

    def test_market_slide_renders(self, tmp_path):
        sr, original = self._tmp_slides(tmp_path)
        try:
            out = sr.render_market_slide(_fake_market())
            assert out is not None and out.exists()
        finally:
            sr.SLIDES_DIR = original

    def test_movers_slide_renders(self, tmp_path):
        sr, original = self._tmp_slides(tmp_path)
        try:
            out = sr.render_movers_slide(_fake_market())
            assert out is not None and out.exists()
        finally:
            sr.SLIDES_DIR = original

    def test_outro_slide_renders(self, tmp_path):
        sr, original = self._tmp_slides(tmp_path)
        try:
            out = sr.render_outro_slide()
            assert out is not None and out.exists()
        finally:
            sr.SLIDES_DIR = original

    def test_econ_slide_none_without_indicators(self, tmp_path):
        from types import SimpleNamespace
        sr, original = self._tmp_slides(tmp_path)
        try:
            assert sr.render_econ_slide(SimpleNamespace(indicators={})) is None
        finally:
            sr.SLIDES_DIR = original

    def test_visual_sequence_assembles(self, tmp_path):
        sr, original = self._tmp_slides(tmp_path)
        try:
            with patch("builders.broll_fetcher.get_broll_slides", return_value=[]):
                visuals = sr.build_visual_sequence(
                    _fake_market(), None, [], "Test Title"
                )
            # intro + market + movers + outro at minimum
            assert len(visuals) >= 4
            assert all(Path(v).exists() for v in visuals)
        finally:
            sr.SLIDES_DIR = original

    def test_sequence_falls_back_to_charts_when_slides_fail(self, tmp_path):
        sr, original = self._tmp_slides(tmp_path)
        try:
            chart = tmp_path / "index_chart.png"
            chart.write_bytes(b"\x89PNG" + b"\x00" * 500)
            with patch.object(sr, "render_intro_slide", return_value=None), \
                 patch.object(sr, "render_market_slide", return_value=None), \
                 patch.object(sr, "render_movers_slide", return_value=None), \
                 patch.object(sr, "render_econ_slide", return_value=None), \
                 patch.object(sr, "render_outro_slide", return_value=None), \
                 patch("builders.broll_fetcher.get_broll_slides", return_value=[]):
                visuals = sr.build_visual_sequence(_fake_market(), None, [chart], "T")
            assert visuals == [chart]
        finally:
            sr.SLIDES_DIR = original


class TestBrollFetcher:
    def test_no_key_returns_empty(self):
        from builders import broll_fetcher as bf
        with patch.object(bf.settings, "pexels_api_key", "", create=True):
            assert bf.get_broll_slides(["stock market"]) == []

    def test_fetch_failure_is_non_fatal(self):
        from builders import broll_fetcher as bf
        with patch.object(bf.settings, "pexels_api_key", "fake-key", create=True), \
             patch.object(bf, "fetch_photo", return_value=None):
            assert bf.get_broll_slides(["stock market"], count=2) == []

    def test_stylize_produces_branded_slide(self, tmp_path):
        from builders import broll_fetcher as bf
        from PIL import Image
        photo = tmp_path / "photo.jpg"
        Image.new("RGB", (1600, 900), color=(60, 90, 140)).save(photo)
        out = bf.stylize_broll(photo, caption="META stock")
        assert out is not None and out.exists()
        img = Image.open(out)
        from config.settings import settings
        assert img.size == (settings.video_width, settings.video_height)
