"""
tests/test_journal_regressions.py — failures found in the production journal.

Every case here was firing on every publish and reported only as a warning,
so nothing surfaced them. They are grouped because they share that shape:
silently degraded output, a healthy-looking run, and a log nobody reads.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestTrendsSurvivesUrllib3v2:
    """
    Every Trends lookup failed with:

        Retry.__init__() got an unexpected keyword argument 'method_whitelist'

    pytrends builds urllib3.Retry(method_whitelist=...) when asked to retry,
    and urllib3 renamed that argument to allowed_methods in 2.0. So the
    scraper raised before reaching the network, on every keyword, since the
    day it was wired in.
    """

    def test_the_client_does_not_ask_pytrends_to_retry(self, monkeypatch):
        import scrapers.trends_scraper as ts

        captured = {}

        class FakeTrendReq:
            def __init__(self, **kwargs):
                # Mirrors urllib3 >= 2.0: pytrends would pass method_whitelist
                # through to Retry, which no longer accepts it.
                if "retries" in kwargs or "backoff_factor" in kwargs:
                    raise TypeError(
                        "Retry.__init__() got an unexpected keyword "
                        "argument 'method_whitelist'")
                captured.update(kwargs)

        module = type(sys)("pytrends.request")
        module.TrendReq = FakeTrendReq
        monkeypatch.setitem(sys.modules, "pytrends", type(sys)("pytrends"))
        monkeypatch.setitem(sys.modules, "pytrends.request", module)

        client = ts._get_pytrends_client()      # must not raise
        assert client is not None
        assert "retries" not in captured
        assert "backoff_factor" not in captured

    def test_retrying_is_done_in_our_own_code(self, monkeypatch):
        import scrapers.trends_scraper as ts
        monkeypatch.setattr(ts.time, "sleep", lambda _: None)

        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("429 rate limited")
            return {"ok": True}

        assert ts._query_with_retry(flaky) == {"ok": True}
        assert attempts["n"] == 3

    def test_a_persistent_failure_still_raises(self, monkeypatch):
        import scrapers.trends_scraper as ts
        monkeypatch.setattr(ts.time, "sleep", lambda _: None)

        def always():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            ts._query_with_retry(always, attempts=2)


class TestEndScreensAreNotAttempted:
    """
    videos.update has no "endscreen" part. YouTube rejected the request at
    validation — reason: unknownPart — which is not an eligibility problem,
    as the old warning claimed, and no scope or verification changes it.
    """

    def test_no_api_call_is_made(self):
        from channel_manager.end_screen_manager import EndScreenManager
        svc = MagicMock()
        manager = EndScreenManager(youtube_service=svc)
        assert manager.add_end_screen("vid1", 150.0) is False
        svc.videos.assert_not_called()

    def test_the_invalid_part_is_gone_from_the_source(self):
        import inspect
        from channel_manager import end_screen_manager
        source = inspect.getsource(end_screen_manager.EndScreenManager.add_end_screen)
        assert 'part="endscreen"' not in source

    def test_the_explanation_is_logged_once_not_per_publish(self, caplog):
        import logging
        from channel_manager import end_screen_manager
        from channel_manager.end_screen_manager import EndScreenManager

        end_screen_manager._EXPLAINED = False
        manager = EndScreenManager(youtube_service=MagicMock())
        with caplog.at_level(logging.INFO, logger=end_screen_manager.__name__):
            manager.add_end_screen("vid1", 150.0)
            manager.add_end_screen("vid2", 150.0)
            manager.add_end_screen("vid3", 150.0)
        explained = [r for r in caplog.records if "Studio" in r.getMessage()]
        assert len(explained) == 1


class TestFredHasARealisticTimeout:
    """
    One morning's run timed out every series — CPI, fed funds, breakevens
    and the 10-year — and the video went out with no economic context. The
    retry loop was working; the ten-second budget was the problem.
    """

    def test_the_read_budget_is_longer_than_the_connect_budget(self):
        from scrapers.economic_scraper import FRED_TIMEOUT
        connect, read = FRED_TIMEOUT
        assert read >= 30
        assert connect < read

    def test_every_fred_call_uses_the_shared_budget(self):
        """A stray timeout=10 on one call site reintroduces the failure."""
        import inspect
        from scrapers import economic_scraper
        source = inspect.getsource(economic_scraper)
        for line in source.splitlines():
            if "FRED_OBS_URL" in line or "FRED_INFO_URL" in line:
                continue
        # No FRED request may carry a hardcoded ten-second timeout.
        assert "timeout=10," not in source.replace("timeout=10,  # rss", "")
