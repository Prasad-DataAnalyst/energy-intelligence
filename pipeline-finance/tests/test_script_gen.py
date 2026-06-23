"""Tests for script generation and title generation modules."""
import pytest
from unittest.mock import patch, MagicMock


MOCK_SCRIPT = """
[HOOK]
Markets just flipped red. The S&P 500 dropped 2.1% today — biggest single-day drop in 6 months.

[OPEN]
Welcome to DriftWire326. Today: market selloff, NVDA earnings, and what the Fed said.

[SEGMENT 1 — Market Overview]
The S&P 500 fell 2.1%, closing at 5,280. Nasdaq dropped 2.8%.
All 11 sectors finished in the red. VIX spiked to 24 — that's fear territory.

[SEGMENT 2 — Stock Spotlight]
NVIDIA crushed earnings. EPS of $6.12 vs estimate of $5.50 — a 11% beat.
Revenue: $26B, up 122% year over year. Stock popped 8% after hours.

[SEGMENT 3 — Macro Pulse]
10-year yield hit 4.45%, its highest since February. That's weighing on tech valuations.
The Fed's next meeting is July 29-30. Markets pricing in just a 12% chance of a cut.

[SEGMENT 4 — What to Watch]
Tomorrow: CPI data at 8:30 AM ET. Estimate: 2.8%. A hot print could push yields higher.
Also: Microsoft and Alphabet report after the close.

[CTA & CLOSE]
If this was useful, smash that subscribe button. Drop a comment — what do you think the Fed does next?
Next video: I'm breaking down the top 10 AI stocks for 2026.

[DISCLAIMER]
This video is for educational and entertainment purposes only.
Nothing here constitutes financial, investment, or legal advice.
Always do your own research before making any investment decisions.
"""


class TestScriptParsing:
    def test_parse_segments_extracts_sections(self):
        from generators.script_gen import _parse_segments
        segments = _parse_segments(MOCK_SCRIPT)
        assert "HOOK" in segments
        assert "OPEN" in segments
        assert "DISCLAIMER" in segments

    def test_parse_segments_hook_content(self):
        from generators.script_gen import _parse_segments
        segments = _parse_segments(MOCK_SCRIPT)
        assert "S&P 500" in segments["HOOK"]
        assert "2.1%" in segments["HOOK"]

    def test_estimate_duration_weekday(self):
        from generators.script_gen import _estimate_duration
        # 1200 words at 155 wpm ≈ 464 seconds
        dur = _estimate_duration(1200, "weekday")
        assert 400 <= dur <= 520

    def test_estimate_duration_shorts(self):
        from generators.script_gen import _estimate_duration
        # 135 words at 165 wpm ≈ 49 seconds
        dur = _estimate_duration(135, "shorts")
        assert 40 <= dur <= 65

    def test_estimate_duration_sunday(self):
        from generators.script_gen import _estimate_duration
        # 1500 words at 140 wpm ≈ 643 seconds
        dur = _estimate_duration(1500, "sunday")
        assert 550 <= dur <= 700

    @patch("generators.script_gen._call_claude")
    def test_generate_weekday_script(self, mock_claude):
        from generators.script_gen import generate_weekday_script
        mock_claude.return_value = (MOCK_SCRIPT, 2500)

        script = generate_weekday_script(
            market_narrative="S&P: -2.1%",
            earnings_narrative="NVDA: +11% beat",
            economic_narrative="CPI: 2.8%",
            tier="tier2",
            topic="Market Selloff",
            anchor_number="S&P -2.1%",
        )
        assert script.video_type == "weekday"
        assert script.word_count > 0
        assert "HOOK" in script.segments
        assert script.tokens_used == 2500

    @patch("generators.script_gen._call_claude")
    def test_generate_weekday_script_tier3(self, mock_claude):
        from generators.script_gen import generate_weekday_script
        mock_claude.return_value = (MOCK_SCRIPT, 1800)

        script = generate_weekday_script(
            market_narrative="S&P: +0.3%",
            earnings_narrative="Light earnings day",
            economic_narrative="No major releases",
            tier="tier3",
        )
        assert script.video_type == "weekday"
        assert script.tier == "tier3"

    @patch("generators.script_gen._call_claude")
    def test_generate_sunday_script(self, mock_claude):
        from generators.script_gen import generate_sunday_script
        mock_claude.return_value = (MOCK_SCRIPT, 3000)

        script = generate_sunday_script(
            topic="Options Trading 101",
            theme="investment_banking",
            week_context="Options volume at record highs in 2026",
            audience_level="beginner-intermediate",
        )
        assert script.video_type == "sunday"
        assert script.model is not None

    @patch("generators.script_gen._call_claude")
    def test_generate_shorts_script(self, mock_claude):
        from generators.script_gen import generate_shorts_script
        mock_claude.return_value = (MOCK_SCRIPT, 800)

        script = generate_shorts_script(
            topic="NVDA Earnings Beat",
            anchor_number="11% EPS beat",
            tier="tier1",
        )
        assert script.video_type == "shorts"
        assert script.style == "shorts_cards"

    @patch("generators.script_gen._call_claude")
    def test_script_save(self, mock_claude, tmp_path):
        from generators.script_gen import generate_weekday_script
        mock_claude.return_value = (MOCK_SCRIPT, 1000)

        script = generate_weekday_script("market data", "earnings", "economic")
        saved_path = script.save(tmp_path)

        assert saved_path.exists()
        content = saved_path.read_text()
        assert "weekday" in content or "Type:" in content

    @patch("generators.script_gen._call_claude")
    def test_sunday_bonus_theme(self, mock_claude):
        from generators.script_gen import generate_sunday_script
        mock_claude.return_value = (MOCK_SCRIPT, 2200)

        script = generate_sunday_script(
            topic="Bitcoin vs Gold",
            theme="rotating_bonus",
            bonus_theme="crypto",
        )
        assert script.video_type == "sunday"
        assert script.tier == "tier3"


class TestTitleGeneration:
    def test_score_title_optimal(self):
        from generators.title_gen import _score_title
        title = "Top 10 S&P 500 Stocks Crashed Today — Market Recap 2026"
        score = _score_title(title)
        assert score.total_score > 50
        assert score.number_score > 0
        assert score.keyword_score > 0

    def test_score_title_too_short(self):
        from generators.title_gen import _score_title
        score = _score_title("Stocks")
        assert score.length_score < 20
        assert any("short" in f.lower() or "length" in f.lower() for f in score.feedback)

    def test_score_title_with_numbers(self):
        from generators.title_gen import _score_title
        title_with_num = "S&P 500 Drops 3% — Here Is What Happened"
        title_no_num = "Stock Market Drops Today — Here Is What Happened"
        with_num = _score_title(title_with_num)
        no_num = _score_title(title_no_num)
        assert with_num.number_score > no_num.number_score

    def test_score_title_curiosity_trigger(self):
        from generators.title_gen import _score_title
        score = _score_title("The Secret Wall Street Strategy Nobody Talks About")
        assert score.curiosity_score > 0

    @patch("generators.title_gen._generate_titles_via_claude")
    @patch("generators.title_gen._generate_description_via_claude")
    def test_generate_title_set_returns_winner(self, mock_desc, mock_titles):
        from generators.title_gen import generate_title_set
        mock_titles.return_value = [
            "S&P 500 Crashes 3% — Wall Street Panic Explained",
            "Stock Market Drops Today",
            "Top 5 Stocks That Surged 10% Despite Market Crash 2026",
        ]
        mock_desc.return_value = "Great video description here."

        result = generate_title_set(
            topic="Market Crash",
            anchor_number="S&P -3%",
            video_type="weekday",
        )
        assert result.winner is not None
        assert len(result.titles) == 3
        assert result.winner.total_score >= result.titles[-1].total_score
        assert result.ab_test_pair is not None

    @patch("generators.title_gen._generate_titles_via_claude")
    @patch("generators.title_gen._generate_description_via_claude")
    def test_fallback_titles_when_claude_fails(self, mock_desc, mock_titles):
        from generators.title_gen import generate_title_set
        mock_titles.return_value = []
        mock_desc.return_value = None

        result = generate_title_set(
            topic="Market Recap",
            anchor_number="S&P +1%",
            video_type="weekday",
        )
        assert len(result.titles) > 0
        assert result.winner is not None

    @patch("generators.title_gen._generate_titles_via_claude")
    @patch("generators.title_gen._generate_description_via_claude")
    def test_legacy_key_stat_param_accepted(self, mock_desc, mock_titles):
        """Backward compat: key_stat= still works as an alias for anchor_number."""
        from generators.title_gen import generate_title_set
        mock_titles.return_value = ["S&P 500 Drops 2% Today — Market Recap"]
        mock_desc.return_value = None

        result = generate_title_set(
            topic="Market Recap",
            key_stat="S&P -2%",
            video_type="weekday_recap",
        )
        assert result.winner is not None

    @patch("generators.title_gen._generate_titles_via_claude")
    @patch("generators.title_gen._generate_description_via_claude")
    def test_shorts_title_route(self, mock_desc, mock_titles):
        from generators.title_gen import generate_title_set
        mock_titles.return_value = ["NVDA +8% TODAY"]
        mock_desc.return_value = None

        result = generate_title_set(
            topic="NVDA Earnings",
            anchor_number="+8%",
            video_type="shorts",
        )
        assert result.winner is not None
        assert result.video_type == "shorts"
