"""Tests for the compliance filter module."""
import pytest
from unittest.mock import patch, MagicMock


CLEAN_SCRIPT = """
[HOOK]
The S&P 500 just had its worst week since March 2020. Here's what happened and what it means.

[OPEN]
Welcome back to DriftWire326. I'm covering three key things today: the market selloff,
earnings season results, and what the Fed is signaling for next month.

[SEGMENT 1]
Markets tanked this week. The S&P fell 4.2%, the Nasdaq dropped 5.1%. Tech stocks led the decline,
with semiconductors down an average of 8%. This is significant context for investors watching
their portfolios — but remember, volatility is normal in market cycles.

[DISCLAIMER]
This video is for educational and entertainment purposes only.
Nothing here constitutes financial, investment, or legal advice.
Always do your own research before making any investment decisions.
"""

BAD_SCRIPT_ADVICE = """
[HOOK]
I'm going to tell you exactly which stocks to buy right now.

[CONTENT]
You should buy NVDA immediately — guaranteed return of 50% by year end.
This is 100% safe and risk free. You can't lose with this investment.
"""

BAD_SCRIPT_MISSING_DISCLAIMER = """
[HOOK]
S&P 500 fell 2% today — here's the breakdown.

[CONTENT]
Markets saw heavy selling pressure today as inflation data came in hot.
The Fed is expected to hold rates steady at next month's meeting.
Investors reacted by rotating out of growth stocks into value plays.
"""


class TestComplianceFilter:
    def test_clean_script_passes(self):
        from generators.compliance_filter import check_compliance
        result = check_compliance(CLEAN_SCRIPT, use_ai=False)
        # Clean script should have no hard blocks
        hard_blocks = [i for i in result.issues if "[HARD BLOCK]" in i]
        assert len(hard_blocks) == 0

    def test_disclaimer_detected(self):
        from generators.compliance_filter import check_compliance
        result = check_compliance(CLEAN_SCRIPT, use_ai=False)
        assert result.disclaimer_present is True

    def test_missing_disclaimer_caught(self):
        from generators.compliance_filter import check_compliance
        result = check_compliance(BAD_SCRIPT_MISSING_DISCLAIMER, use_ai=False)
        assert result.disclaimer_present is False
        assert any("Disclaimer" in i or "disclaimer" in i for i in result.issues)

    def test_bad_script_fails(self):
        from generators.compliance_filter import check_compliance
        result = check_compliance(BAD_SCRIPT_ADVICE, use_ai=False)
        assert result.passed is False
        assert result.risk_level in ("medium", "high")
        assert len(result.issues) > 0

    def test_guaranteed_return_blocked(self):
        from generators.compliance_filter import check_compliance
        script = "This investment has a guaranteed return of 50% annually. " + CLEAN_SCRIPT
        result = check_compliance(script, use_ai=False)
        blocked = [i for i in result.issues if "guaranteed" in i.lower()]
        assert len(blocked) > 0

    def test_risk_free_blocked(self):
        from generators.compliance_filter import check_compliance
        script = "Bonds are completely risk free assets. " + CLEAN_SCRIPT
        result = check_compliance(script, use_ai=False)
        blocked = [i for i in result.issues if "risk" in i.lower()]
        assert len(blocked) > 0

    def test_direct_buy_advice_blocked(self):
        from generators.compliance_filter import check_compliance
        script = "You should buy NVDA stock immediately. " + CLEAN_SCRIPT
        result = check_compliance(script, use_ai=False)
        blocked = [i for i in result.issues if "buy" in i.lower() or "advice" in i.lower()]
        assert len(blocked) > 0

    def test_auto_fix_adds_disclaimer(self):
        from generators.compliance_filter import check_compliance, auto_fix_script
        result = check_compliance(BAD_SCRIPT_MISSING_DISCLAIMER, use_ai=False)
        fixed = auto_fix_script(BAD_SCRIPT_MISSING_DISCLAIMER, result)
        assert "educational" in fixed.lower() or "own research" in fixed.lower()

    def test_auto_fix_replaces_bad_phrases(self):
        from generators.compliance_filter import check_compliance, auto_fix_script
        script = "You should buy this stock now. It is risk free. " + CLEAN_SCRIPT
        result = check_compliance(script, use_ai=False)
        fixed = auto_fix_script(script, result)
        assert "you should buy" not in fixed.lower()
        assert "risk free" not in fixed.lower()

    def test_flagged_phrases_contain_positions(self):
        from generators.compliance_filter import check_compliance
        script = "This is 100% safe. " + CLEAN_SCRIPT
        result = check_compliance(script, use_ai=False)
        assert len(result.flagged_phrases) > 0
        for phrase, pos in result.flagged_phrases:
            assert isinstance(phrase, str)
            assert isinstance(pos, int)
            assert pos >= 0

    def test_result_summary_format(self):
        from generators.compliance_filter import check_compliance
        result = check_compliance(CLEAN_SCRIPT, use_ai=False)
        summary = result.summary
        assert "Compliance Check" in summary
        assert ("PASSED" in summary or "FAILED" in summary)
