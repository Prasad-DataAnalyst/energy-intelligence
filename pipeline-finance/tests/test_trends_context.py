"""
tests/test_trends_context.py — what the audience is searching for.

scrapers/trends_scraper.py has existed since the project started with zero
callers, so every script was written purely from price data. That is a
large part of why every recap came out sounding the same.
"""
import json
from datetime import date, timedelta

import pytest


@pytest.fixture
def logs(tmp_path):
    from config.settings import settings
    settings.logs_dir = tmp_path
    return tmp_path


def _cache(logs, queries, when=None):
    (logs / "trending_queries.json").write_text(json.dumps({
        "date": (when or date.today()).isoformat(), "queries": queries}))


class TestTrendingContext:
    def test_cached_queries_are_formatted_for_the_prompt(self, logs):
        from generators.script_gen import _trending_context
        _cache(logs, ["nvidia stock", "cpi report"])
        block = _trending_context()
        assert "nvidia stock" in block and "cpi report" in block
        assert "SEARCHING" in block

    def test_the_prompt_tells_claude_to_ignore_unrelated_terms(self, logs):
        """
        Rising searches are noisy. Without this the script can be dragged
        onto a topic the day's data says nothing about.
        """
        from generators.script_gen import _trending_context
        _cache(logs, ["nvidia stock"])
        assert "Ignore" in _trending_context()

    def test_the_list_is_capped(self, logs):
        from generators.script_gen import _trending_context
        _cache(logs, [f"query {i}" for i in range(30)])
        assert "query 6" not in _trending_context(limit=6)

    def test_todays_cache_is_reused(self, logs, monkeypatch):
        """
        Two runs a weekday share one fetch — the day's searches do not change
        enough between them to be worth the rate-limit budget.
        """
        from generators import script_gen
        _cache(logs, ["cached term"])

        def explode(*args, **kwargs):
            raise AssertionError("should not have hit the network")

        monkeypatch.setattr("scrapers.trends_scraper.TrendsScraper", explode)
        assert "cached term" in script_gen._trending_context()

    def test_a_stale_cache_is_not_reused(self, logs):
        from generators.script_gen import _trending_context
        _cache(logs, ["yesterday's news"], when=date.today() - timedelta(days=3))
        assert "yesterday's news" not in _trending_context()

    def test_a_corrupt_cache_is_survivable(self, logs):
        from generators.script_gen import _trending_context
        (logs / "trending_queries.json").write_text("{not json")
        assert isinstance(_trending_context(), str)     # must not raise


class TestTrendsNeverBlocksPublishing:
    """
    Google Trends is unofficial and rate-limited. A pipeline that publishes
    to a clock must never fail with it or wait on it.
    """

    def test_a_scraper_that_raises_yields_no_context(self, logs, monkeypatch):
        from generators import script_gen

        class Boom:
            def get_rising_queries(self, **kwargs):
                raise RuntimeError("429 rate limited")

        monkeypatch.setattr("scrapers.trends_scraper.TrendsScraper", lambda: Boom())
        assert script_gen._trending_context() == ""

    def test_no_results_yields_no_context(self, logs, monkeypatch):
        from generators import script_gen

        class Empty:
            def get_rising_queries(self, **kwargs):
                return []

        monkeypatch.setattr("scrapers.trends_scraper.TrendsScraper", lambda: Empty())
        assert script_gen._trending_context() == ""

    def test_a_successful_fetch_is_cached_for_the_day(self, logs, monkeypatch):
        from generators import script_gen

        class Fine:
            def get_rising_queries(self, **kwargs):
                return [{"query": "fed rate cut", "value": 900}]

        monkeypatch.setattr("scrapers.trends_scraper.TrendsScraper", lambda: Fine())
        assert "fed rate cut" in script_gen._trending_context()
        cached = json.loads((logs / "trending_queries.json").read_text())
        assert cached["date"] == date.today().isoformat()
        assert cached["queries"] == ["fed rate cut"]

    def test_an_unwritable_cache_still_returns_context(self, tmp_path, monkeypatch):
        from config.settings import settings
        from generators import script_gen
        blocked = tmp_path / "logs"
        blocked.write_text("a file where a directory should be")
        settings.logs_dir = blocked

        class Fine:
            def get_rising_queries(self, **kwargs):
                return [{"query": "cpi report", "value": 500}]

        monkeypatch.setattr("scrapers.trends_scraper.TrendsScraper", lambda: Fine())
        assert "cpi report" in script_gen._trending_context()

    def test_the_seed_list_stays_short(self):
        """
        The scraper sleeps a second between keywords to respect the rate
        limit, so every extra seed is another second of a pipeline that
        publishes to a clock.
        """
        from generators.script_gen import _TREND_SEEDS
        assert len(_TREND_SEEDS) <= 3


class TestContextComposition:
    def test_trends_are_appended_to_the_data_narratives(self, logs, monkeypatch):
        from generators import script_gen
        captured = {}

        def fake_call(system, user_prompt):
            captured["prompt"] = user_prompt
            return "[HOOK]\nHi.\n[MARKET RECAP]\nThe S&P rose 0.5%.", 100

        monkeypatch.setattr(script_gen, "_call_claude", fake_call)
        monkeypatch.setattr(script_gen, "_trending_context", lambda: "TRENDBLOCK")
        script_gen.generate_weekday_script("MARKETNARR", "EARNNARR", "ECONNARR")
        for fragment in ("MARKETNARR", "EARNNARR", "ECONNARR", "TRENDBLOCK"):
            assert fragment in captured["prompt"], fragment

    def test_an_empty_trend_block_leaves_no_blank_gap(self, logs, monkeypatch):
        """Joining an empty string in would leave a stray blank run in the prompt."""
        from generators import script_gen
        captured = {}

        def fake_call(system, user_prompt):
            captured["prompt"] = user_prompt
            return "[HOOK]\nHi.\n[MARKET RECAP]\nFlat.", 100

        monkeypatch.setattr(script_gen, "_call_claude", fake_call)
        monkeypatch.setattr(script_gen, "_trending_context", lambda: "")
        script_gen.generate_weekday_script("MARKETNARR", "EARNNARR", "ECONNARR")
        assert "\n\n\n" not in captured["prompt"]
