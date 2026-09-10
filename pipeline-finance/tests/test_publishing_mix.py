"""
tests/test_publishing_mix.py — what gets published, in which format.

The traffic data: Shorts drove 50% of this channel's views from 6 of its 17
weekly uploads, so per video they were worth several times the long-form.
Separately, the two weekday long-form videos covered the same market day —
near-duplicate content, which is what YouTube's inauthentic-content policy
targets for demonetisation.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPremarketFormatIsConfigurable:
    """
    Moving the 8am slot to a Short is a bet on traffic data that only began
    recording the day it was made. It must be revertible without a deploy.
    """

    def test_the_setting_defaults_to_short(self):
        from config.settings import settings
        assert settings.premarket_format in ("short", "long")

    def test_the_slot_name_follows_the_format(self, monkeypatch):
        """
        The health report prints "next scheduled content" from these names.
        Naming it a video while it publishes a Short would misreport what
        the channel is about to do.
        """
        import importlib
        from config.settings import settings
        from scheduler import master_scheduler

        monkeypatch.setattr(settings, "premarket_format", "short")
        importlib.reload(master_scheduler)
        assert "Short" in master_scheduler.CONTENT_SLOT_NAMES["weekday_premarket"]

        monkeypatch.setattr(settings, "premarket_format", "long")
        importlib.reload(master_scheduler)
        assert master_scheduler.CONTENT_SLOT_NAMES["weekday_premarket"] == \
            "Pre-market video"

        monkeypatch.setattr(settings, "premarket_format", "short")
        importlib.reload(master_scheduler)


class TestPremarketShort:

    def test_the_premarket_theme_is_forward_looking(self):
        """
        The whole point of splitting the two weekday videos is that they
        tell different stories. A morning Short recapping yesterday would
        recreate the duplicate-content problem it exists to solve.
        """
        from scheduler.short_pipeline import PREMARKET_THEME
        brief = PREMARKET_THEME["brief"].lower()
        assert "forward-looking" in brief
        assert "never what already happened" in brief

    def test_it_is_chosen_by_slot_not_by_weekday(self, monkeypatch):
        """
        "What to watch before the bell" works every weekday, and the futures
        data behind it only exists in the morning.
        """
        from scheduler import short_pipeline
        # setitem, not setattr: the theme dict captured the function object
        # at import, so replacing the module attribute leaves the dict
        # pointing at the original — and the test reaches Yahoo for real.
        monkeypatch.setitem(short_pipeline.PREMARKET_THEME, "context",
                            lambda: "FUTURES BEFORE THE OPEN: ES +0.3%.")
        monkeypatch.setattr(short_pipeline, "get_todays_theme",
                            lambda wd=None: {"name": "midday theme"})
        captured = {}

        def fake_generate(theme, context):
            captured["theme"] = theme["name"]
            return None

        monkeypatch.setattr(short_pipeline, "generate_short_script", fake_generate)
        for weekday in range(5):
            captured.clear()
            short_pipeline.run_themed_short(weekday=weekday, upload=False,
                                            slot="premarket")
            assert captured["theme"] == "Before the Bell", weekday

    def test_the_midday_slot_still_uses_the_weekday_rotation(self, monkeypatch):
        from scheduler import short_pipeline
        captured = {}

        monkeypatch.setattr(short_pipeline, "get_todays_theme",
                            lambda wd=None: {"name": "Econ Explained",
                                             "context": lambda: "ctx",
                                             "title": "t", "brief": "b"})

        def fake_generate(theme, context):
            captured["theme"] = theme["name"]
            return None

        monkeypatch.setattr(short_pipeline, "generate_short_script", fake_generate)
        short_pipeline.run_themed_short(weekday=2, upload=False)
        assert captured["theme"] == "Econ Explained"

    def test_no_premarket_data_skips_rather_than_publishing_nothing(self, monkeypatch):
        from scheduler import short_pipeline
        monkeypatch.setitem(short_pipeline.PREMARKET_THEME, "context", lambda: "")
        assert short_pipeline.run_themed_short(upload=False, slot="premarket") is None

    def test_a_scraper_failure_does_not_raise(self, monkeypatch):
        """Nothing in the morning slot may take the daemon down with it."""
        from scheduler import short_pipeline

        class Boom:
            def get_premarket_data(self):
                raise RuntimeError("yahoo unreachable")

        monkeypatch.setattr("scrapers.market_scraper.MarketScraper", lambda: Boom())
        assert short_pipeline._premarket_summary() == ""

    def test_futures_and_movers_both_reach_the_summary(self, monkeypatch):
        from scheduler import short_pipeline

        class Fake:
            def get_premarket_data(self):
                return {
                    "futures": {"ES=F": {"name": "S&P 500 futures",
                                         "change_pct": 0.34}},
                    "futures_direction": "higher",
                    "top_movers": [{"symbol": "NVDA", "change_pct": -2.1}],
                }

        monkeypatch.setattr("scrapers.market_scraper.MarketScraper", lambda: Fake())
        summary = short_pipeline._premarket_summary()
        assert "S&P 500 futures +0.34%" in summary
        assert "NVDA -2.10%" in summary
        assert "higher" in summary


class TestEvergreenRatio:
    """
    30% of views come from search. A recap is worthless 24 hours after it
    publishes; an explainer answering a recurring question earns views for
    years. One evergreen long-form against ten recaps was the wrong ratio.
    """

    def test_a_midweek_evergreen_slot_exists(self):
        from scheduler.master_scheduler import CONTENT_SLOTS
        assert "midweek_evergreen" in CONTENT_SLOTS

    def test_it_does_not_share_an_hour_with_the_recap(self):
        """Two uploads an hour apart compete on the same subscribers' feeds."""
        from scheduler.master_scheduler import CONTENT_SLOTS
        recap = CONTENT_SLOTS["weekday_postmarket"]
        evergreen = CONTENT_SLOTS["midweek_evergreen"]
        assert evergreen["hour"] - recap["hour"] >= 1

    def test_evergreen_long_form_doubled(self):
        from scheduler.master_scheduler import CONTENT_SLOTS
        evergreen = [k for k in CONTENT_SLOTS
                     if k in ("midweek_evergreen", "sunday_educational")]
        assert len(evergreen) == 2

    def test_every_slot_has_a_display_name(self):
        """The health report prints these; a missing one shows a raw job id."""
        from scheduler.master_scheduler import CONTENT_SLOTS, CONTENT_SLOT_NAMES
        assert set(CONTENT_SLOTS) == set(CONTENT_SLOT_NAMES)
