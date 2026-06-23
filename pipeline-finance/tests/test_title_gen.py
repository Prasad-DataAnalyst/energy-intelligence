"""Tests for TitleGenerator class (Module 9)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.title_gen import TitleGenerator, TitleSet, TitleScore, _score_title


class TestTitleGenerator:

    @pytest.fixture
    def tg(self):
        return TitleGenerator()

    def test_base_tags_always_present(self, tg):
        tags = tg.generate_tags()
        for base in tg.BASE_TAGS:
            assert base in tags

    def test_generate_tags_adds_extras(self, tg):
        tags = tg.generate_tags(extra=["AAPL", "earnings"])
        assert "AAPL" in tags
        assert "earnings" in tags

    def test_generate_tags_no_duplicates(self, tg):
        tags = tg.generate_tags(extra=["stocks"])  # "stocks" already in BASE_TAGS
        assert tags.count("stocks") == 1

    def test_shorts_title_hard_cap_40_chars(self, tg):
        long_title = "A" * 50
        mock_ts = MagicMock(spec=TitleSet)
        mock_ts.winner = MagicMock(spec=TitleScore)
        mock_ts.winner.title = long_title
        with patch("generators.title_gen.generate_title_set", return_value=mock_ts):
            result = tg.generate_shorts_title("long topic", "#1.23B", "hook text")
        assert len(result) <= 40

    def test_shorts_title_preserves_short_title(self, tg):
        short_title = "SPY Crashes 5%"
        mock_ts = MagicMock()
        mock_ts.winner = MagicMock()
        mock_ts.winner.title = short_title
        with patch("generators.title_gen.generate_title_set", return_value=mock_ts):
            result = tg.generate_shorts_title("SPY crash", "5%")
        assert result == short_title

    def test_generate_description_appends_disclaimer(self, tg):
        with patch("generators.title_gen._generate_description_via_claude", return_value="Body text"):
            from config.settings import settings
            desc = tg.generate_description("AAPL Crashes", "script summary", "weekday")
        assert settings.disclaimer_text in desc

    def test_generate_description_appends_ai_disclosure(self, tg):
        with patch("generators.title_gen._generate_description_via_claude", return_value="Body"):
            desc = tg.generate_description("Title", "Summary", "weekday")
        assert "Narration is AI-generated." in desc

    def test_generate_description_no_double_disclaimer(self, tg):
        from config.settings import settings
        body_with_disclaimer = f"Content.\n\n{settings.disclaimer_text}"
        with patch("generators.title_gen._generate_description_via_claude", return_value=body_with_disclaimer):
            desc = tg.generate_description("Title", "Summary", "weekday")
        assert desc.count(settings.disclaimer_text) == 1

    def test_run_promise_check_returns_original_on_match(self, tg):
        title = "Stock Market Crash Explained"
        script = "stock market crash fall explained today"
        with patch("generators.compliance_filter.ComplianceFilter.run_promise_match", return_value=True):
            result = tg.run_promise_check(title, script, "crash", "")
        assert result == title

    def test_run_promise_check_regenerates_on_mismatch(self, tg):
        title = "Totally Unrelated Title"
        script = "apple earnings beat expectations"
        mock_ts = MagicMock()
        mock_ts.winner = MagicMock()
        mock_ts.winner.title = "AAPL Earnings Beat"
        with patch("generators.compliance_filter.ComplianceFilter.run_promise_match", side_effect=[False, True]):
            with patch.object(tg, "generate_main_title", return_value=mock_ts):
                result = tg.run_promise_check(title, script, "AAPL earnings")
        assert result == "AAPL Earnings Beat"

    def test_shorts_char_limit_constant(self):
        assert TitleGenerator.SHORTS_CHAR_LIMIT == 40
