"""Tests for market, economic, and earnings scrapers."""
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestMarketScraper:
    def test_ticker_snapshot_sentiment(self):
        from scrapers.market_scraper import TickerSnapshot
        snap = TickerSnapshot(
            symbol="SPY", name="SPDR S&P 500 ETF", price=540.0,
            change=12.0, change_pct=2.3, volume=80_000_000,
            avg_volume=60_000_000, market_cap=None,
            day_high=542.0, day_low=535.0,
            week_52_high=580.0, week_52_low=420.0, pe_ratio=None,
        )
        assert snap.sentiment == "strongly_bullish"
        assert snap.volume_ratio == pytest.approx(1.33, abs=0.01)

    def test_ticker_snapshot_bearish(self):
        from scrapers.market_scraper import TickerSnapshot
        snap = TickerSnapshot(
            symbol="QQQ", name="Invesco QQQ", price=440.0,
            change=-12.0, change_pct=-2.65, volume=50_000_000,
            avg_volume=45_000_000, market_cap=None,
            day_high=445.0, day_low=438.0,
            week_52_high=500.0, week_52_low=350.0, pe_ratio=None,
        )
        assert snap.sentiment == "strongly_bearish"

    def test_ticker_snapshot_neutral(self):
        from scrapers.market_scraper import TickerSnapshot
        snap = TickerSnapshot(
            symbol="DIA", name="SPDR Dow Jones ETF", price=390.0,
            change=0.5, change_pct=0.13, volume=10_000_000,
            avg_volume=12_000_000, market_cap=None,
            day_high=391.0, day_low=389.0,
            week_52_high=420.0, week_52_low=310.0, pe_ratio=None,
        )
        assert snap.sentiment == "neutral"

    @patch("scrapers.market_scraper.yf.Ticker")
    def test_fetch_ticker_returns_snapshot(self, mock_ticker):
        import pandas as pd
        from scrapers.market_scraper import _fetch_ticker

        mock_hist = pd.DataFrame(
            {"Close": [530.0, 540.0], "Volume": [75_000_000, 80_000_000],
             "High": [542.0, 542.0], "Low": [528.0, 535.0]},
        )
        mock_info = MagicMock()
        mock_info.display_name = "S&P 500 ETF"
        mock_info.three_month_average_volume = 60_000_000
        mock_info.market_cap = None
        mock_info.fifty_two_week_high = 580.0
        mock_info.fifty_two_week_low = 420.0
        mock_info.pe_ratio = None

        mock_ticker.return_value.fast_info = mock_info
        mock_ticker.return_value.history.return_value = mock_hist

        result = _fetch_ticker("SPY")
        assert result is not None
        assert result.symbol == "SPY"
        assert result.price == pytest.approx(540.0)
        assert result.change_pct == pytest.approx(1.887, abs=0.01)

    @patch("scrapers.market_scraper.yf.Ticker")
    def test_fetch_ticker_retries_on_failure(self, mock_ticker):
        from scrapers.market_scraper import _fetch_ticker
        mock_ticker.return_value.fast_info = MagicMock(side_effect=Exception("API Error"))
        mock_ticker.return_value.history.return_value = MagicMock(empty=True)
        result = _fetch_ticker("INVALID")
        assert result is None

    def test_market_summary_to_narrative(self):
        from scrapers.market_scraper import TickerSnapshot, MarketSummary

        def make_snap(sym, price, pct):
            return TickerSnapshot(
                symbol=sym, name=sym, price=price, change=price * pct / 100,
                change_pct=pct, volume=1_000_000, avg_volume=900_000,
                market_cap=None, day_high=price + 2, day_low=price - 2,
                week_52_high=price + 50, week_52_low=price - 100, pe_ratio=None,
            )

        summary = MarketSummary(
            date="2026-06-22 Monday",
            sp500=make_snap("SPY", 540.0, 1.5),
            nasdaq=make_snap("QQQ", 440.0, 2.1),
            dow=make_snap("DIA", 395.0, 0.8),
            russell2000=make_snap("IWM", 200.0, -0.3),
            vix=make_snap("^VIX", 16.5, -5.0),
            ten_year_yield=make_snap("^TNX", 4.25, 0.1),
            gold=make_snap("GLD", 220.0, 0.5),
            bitcoin=make_snap("BTC-USD", 95000, 3.2),
            top_gainers=[make_snap("NVDA", 1100, 5.0)],
            top_losers=[make_snap("INTC", 20, -3.0)],
            high_volume=[make_snap("TSLA", 250, 4.0)],
            sector_performance={"Technology": 2.1, "Healthcare": -0.5},
            market_breadth={"advancing": 8, "declining": 3, "unchanged": 0},
        )
        narrative = summary.to_narrative()
        assert "S&P 500" in narrative
        assert "+1.50%" in narrative or "1.50%" in narrative
        assert "TOP GAINERS" in narrative
        assert "SECTOR PERFORMANCE" in narrative


class TestMarketScraperClass:
    """Tests for the new MarketScraper class."""

    def _make_snap(self, sym="SPY", price=540.0, pct=1.5):
        from scrapers.market_scraper import TickerSnapshot
        return TickerSnapshot(
            symbol=sym, name=sym, price=price, change=price * pct / 100,
            change_pct=pct, volume=1_000_000, avg_volume=900_000,
            market_cap=None, day_high=price + 2, day_low=price - 2,
            week_52_high=price + 50, week_52_low=price - 100, pe_ratio=None,
        )

    def test_get_market_status_holiday(self):
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        # Patch datetime to a known 2026 holiday (New Year's Day)
        with patch("scrapers.market_scraper.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.strftime.return_value = "2026-01-01"
            mock_now.weekday.return_value = 3   # Thursday (not weekend)
            mock_dt.now.return_value = mock_now
            status = scraper.get_market_status()
        assert status == "holiday"

    def test_get_market_status_weekend(self):
        from scrapers.market_scraper import MarketScraper
        import zoneinfo
        scraper = MarketScraper()
        with patch("scrapers.market_scraper.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.strftime.return_value = "2026-06-27"  # Saturday, not a holiday
            mock_now.weekday.return_value = 5              # Saturday
            mock_dt.now.return_value = mock_now
            status = scraper.get_market_status()
        assert status == "closed"

    def test_score_topic_from_snapshot_high(self):
        """Tier-1 high-coverage ticker should score > 60."""
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        snap = self._make_snap("NVDA", pct=6.5)   # tier1 move, high-coverage name
        with patch.object(scraper, "get_market_status", return_value="open"):
            score = scraper.score_topic(snap)
        assert 0 <= score <= 100
        assert score > 60

    def test_score_topic_from_snapshot_low(self):
        """Tiny move on unknown ticker should score < 40."""
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        snap = self._make_snap("XYZ", pct=0.05)
        snap.symbol = "XYZ"
        with patch.object(scraper, "get_market_status", return_value="closed"):
            score = scraper.score_topic(snap)
        assert 0 <= score <= 100
        assert score < 40

    def test_score_topic_from_dict(self):
        """score_topic accepts a plain dict, not just TickerSnapshot."""
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        ticker_dict = {
            "ticker": "AAPL",
            "change_pct": 4.2,
            "volume": 2_000_000,
            "avg_volume": 1_000_000,
        }
        with patch.object(scraper, "get_market_status", return_value="open"):
            score = scraper.score_topic(ticker_dict)
        assert isinstance(score, float)
        assert 0 <= score <= 100

    def test_score_topic_formula_weights(self):
        """Verify the four-component formula sums correctly."""
        from scrapers.market_scraper import MarketScraper
        scraper = MarketScraper()
        # 10% move (change_pct_score=100), 5x vol (volume_ratio_score=100),
        # high coverage ticker, open market
        snap = self._make_snap("AAPL", pct=10.0)
        snap.symbol = "AAPL"
        snap.volume = 5_000_000
        snap.avg_volume = 1_000_000

        with patch.object(scraper, "get_market_status", return_value="open"):
            score = scraper.score_topic(snap)
        # All four components at (or near) max → score should be very high
        assert score > 90

    @patch("scrapers.market_scraper._fetch_ticker")
    def test_get_market_close_data_structure(self, mock_fetch, tmp_path):
        """get_market_close_data returns required keys and saves JSON."""
        from scrapers.market_scraper import MarketScraper, TickerSnapshot

        def _snap(sym, pct):
            return TickerSnapshot(
                symbol=sym, name=sym, price=100.0, change=pct,
                change_pct=pct, volume=1_000_000, avg_volume=900_000,
                market_cap=None, day_high=102.0, day_low=98.0,
                week_52_high=120.0, week_52_low=80.0, pe_ratio=None,
            )

        mock_fetch.side_effect = lambda sym: _snap(sym, 1.2)

        scraper = MarketScraper(output_dir=tmp_path)
        with patch.object(scraper, "_fetch_one", side_effect=lambda sym: _snap(sym, 1.2)):
            result = scraper.get_market_close_data()

        assert "indices" in result
        assert "sector_performance" in result
        assert "market_tier" in result
        assert "narrative" in result
        assert "market_breadth" in result

    @patch("scrapers.market_scraper.yf.download")
    def test_get_ticker_history_returns_dataframe(self, mock_download, tmp_path):
        """get_ticker_history returns a DataFrame with OHLCV columns."""
        import pandas as pd
        from scrapers.market_scraper import MarketScraper

        mock_df = pd.DataFrame({
            "Open":   [530.0, 535.0],
            "High":   [540.0, 538.0],
            "Low":    [528.0, 532.0],
            "Close":  [537.0, 536.0],
            "Volume": [70_000_000, 72_000_000],
        })

        with patch("scrapers.market_scraper.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = mock_df
            scraper = MarketScraper(output_dir=tmp_path)
            hist = scraper.get_ticker_history("SPY", days=2)

        assert list(hist.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert len(hist) == 2

    def test_ticker_snapshot_to_dict(self):
        """TickerSnapshot.to_dict() includes all required fields."""
        snap = self._make_snap("TSLA", 250.0, 3.5)
        d = snap.to_dict()
        for key in ("symbol", "price", "change_pct", "volume", "volume_ratio",
                    "sentiment", "story_tier"):
            assert key in d


class TestEconomicScraper:
    def test_indicator_narrative(self):
        from scrapers.economic_scraper import EconomicIndicator
        ind = EconomicIndicator(
            series_id="CPIAUCSL", name="CPI Inflation", value=2.8,
            previous_value=3.1, change=-0.3, change_pct=-9.68,
            period="2026-05", unit="Percent", frequency="Monthly", source="FRED",
        )
        assert ind.trend == "falling"
        assert "fell" in ind.narrative
        assert "2.8" in ind.narrative

    def test_surprise_detection(self):
        from scrapers.economic_scraper import EconomicIndicator, _build_surprises
        indicators = {
            "CPIAUCSL": EconomicIndicator(
                series_id="CPIAUCSL", name="CPI", value=2.5, previous_value=3.0,
                change=-0.5, change_pct=-16.7, period="2026-05",
                unit="Percent", frequency="Monthly", source="TEST",
            )
        }
        surprises = _build_surprises(indicators)
        assert len(surprises) == 1
        assert "CPI" in surprises[0]

    @patch.dict("os.environ", {"FRED_API_KEY": ""})
    def test_mock_fallback_when_no_api_key(self):
        from scrapers.economic_scraper import scrape_economic_data
        snapshot = scrape_economic_data()
        assert "FEDFUNDS" in snapshot.indicators
        assert snapshot.indicators["FEDFUNDS"].source == "MOCK"


class TestEconomicScraperClass:
    """Tests for the new EconomicScraper class."""

    def test_score_fed_decision(self):
        """Fed decision should score 100."""
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        event = {"title": "Federal Reserve issues FOMC statement", "series_id": "", "name": ""}
        assert scraper.score_economic_event(event) == 100

    def test_score_cpi_release(self):
        """CPI release should score 90."""
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        event = {"series_id": "CPIAUCSL", "name": "CPI Inflation", "title": ""}
        assert scraper.score_economic_event(event) == 90

    def test_score_gdp_release(self):
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        assert scraper.score_economic_event({"series_id": "GDP", "name": "Real GDP", "title": ""}) == 85

    def test_score_jobs_report(self):
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        assert scraper.score_economic_event({"title": "Nonfarm Payrolls Rise", "series_id": "", "name": ""}) == 80

    def test_score_housing_starts(self):
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        assert scraper.score_economic_event({"series_id": "HOUST", "name": "Housing Starts", "title": ""}) == 50

    def test_score_economic_release_object(self):
        """score_economic_event accepts an EconomicRelease dataclass."""
        from scrapers.economic_scraper import EconomicScraper, EconomicRelease
        scraper = EconomicScraper(api_key="")
        release = EconomicRelease(
            series_id="UNRATE", name="Unemployment Rate",
            latest_value=4.1, previous_value=4.0,
            change=0.1, change_pct=2.5,
            significance_score=80, release_date="2026-06-23", unit="Percent",
        )
        score = scraper.score_economic_event(release)
        assert score == 80

    def test_economic_release_dataclass(self):
        from scrapers.economic_scraper import EconomicRelease
        r = EconomicRelease(
            series_id="CPIAUCSL", name="CPI Inflation",
            latest_value=2.9, previous_value=2.7,
            change=0.2, change_pct=7.4,
            significance_score=90, release_date="2026-06-23", unit="Percent",
        )
        assert r.is_market_moving is True
        d = r.to_dict()
        for key in ("series_id", "name", "latest_value", "change", "significance_score",
                    "release_date", "is_market_moving"):
            assert key in d

    def test_no_api_key_returns_empty_releases(self):
        """get_todays_releases returns [] gracefully when no API key."""
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        result = scraper.get_todays_releases()
        assert result == []

    @patch("scrapers.economic_scraper._fetch_rss", return_value=[])
    def test_get_fed_statements_empty_on_failure(self, mock_rss):
        """get_fed_statements returns [] without raising when RSS unavailable."""
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        result = scraper.get_fed_statements()
        assert result == []

    @patch("scrapers.economic_scraper._fetch_rss", return_value=[])
    def test_get_treasury_news_empty_on_failure(self, mock_rss):
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        result = scraper.get_treasury_news()
        assert result == []

    def test_no_api_key_returns_estimated_calendar(self):
        """get_economic_calendar_week falls back to frequency estimation."""
        from scrapers.economic_scraper import EconomicScraper
        scraper = EconomicScraper(api_key="")
        # Mock _estimate_calendar to verify it's called
        with patch.object(scraper, "_estimate_calendar", return_value=[]) as mock_est:
            result = scraper.get_economic_calendar_week()
        mock_est.assert_called_once()

    def test_rss_date_parsing_valid(self):
        """_parse_rss_date correctly parses RFC 2822 dates."""
        from scrapers.economic_scraper import _parse_rss_date
        dt = _parse_rss_date("Tue, 23 Jun 2026 14:30:00 +0000")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6

    def test_rss_date_parsing_invalid(self):
        """_parse_rss_date returns None on garbage input."""
        from scrapers.economic_scraper import _parse_rss_date
        assert _parse_rss_date("not a date") is None
        assert _parse_rss_date("") is None


class TestEarningsScraper:
    def test_earnings_event_beat_miss(self):
        from scrapers.earnings_scraper import EarningsEvent
        event = EarningsEvent(
            symbol="AAPL", company="Apple Inc.", report_date="2026-07-30",
            report_time="AMC", eps_estimate=1.50, eps_actual=1.68,
            revenue_estimate=None, revenue_actual=None,
            surprise_pct=12.0, guidance="raised", after_hours_move=4.5,
        )
        assert event.beat_miss == "strong_beat"
        assert "STRONG BEAT" in event.headline
        assert "AAPL" in event.headline

    def test_earnings_event_miss(self):
        from scrapers.earnings_scraper import EarningsEvent
        event = EarningsEvent(
            symbol="META", company="Meta", report_date="2026-07-25",
            report_time="AMC", eps_estimate=4.00, eps_actual=3.70,
            revenue_estimate=None, revenue_actual=None,
            surprise_pct=-7.5, guidance="lowered", after_hours_move=-8.0,
        )
        assert event.beat_miss == "strong_miss"


class TestEarningsScraperClass:
    """Tests for the new EarningsScraper class."""

    def _make_event(self, sym="AAPL", surprise=12.0, ah_move=5.0):
        from scrapers.earnings_scraper import EarningsEvent
        return EarningsEvent(
            symbol=sym, company="Apple Inc.", report_date="2026-07-30",
            report_time="AMC", eps_estimate=1.50, eps_actual=1.68,
            revenue_estimate=None, revenue_actual=None,
            surprise_pct=surprise, guidance="raised", after_hours_move=ah_move,
        )

    def test_score_high_surprise_large_cap(self):
        """50% EPS surprise + 20% AH move + $100B+ cap → score ~100."""
        from scrapers.earnings_scraper import EarningsScraper
        scraper = EarningsScraper(api_key="")
        event = self._make_event("AAPL", surprise=50.0, ah_move=20.0)
        with patch.object(scraper, "_market_cap_score", return_value=100):
            score = scraper.score_earnings_event(event)
        # eps_score=min(100,100)*0.40=40; mc=100*0.30=30; ah=min(100,100)*0.30=30 → 100
        assert score == pytest.approx(100.0, abs=0.1)

    def test_score_no_data_returns_mc_only(self):
        """Event with no surprise or AH data scores only on market cap."""
        from scrapers.earnings_scraper import EarningsScraper
        scraper = EarningsScraper(api_key="")
        event = self._make_event("XYZ", surprise=None, ah_move=None)
        with patch.object(scraper, "_market_cap_score", return_value=20):
            score = scraper.score_earnings_event(event)
        # eps=0, mc=20*0.30=6, ah=0 → 6
        assert score == pytest.approx(6.0, abs=0.1)

    def test_score_mid_beat(self):
        """Moderate beat (10% surprise, $10-100B cap, 5% AH) → mid-range score."""
        from scrapers.earnings_scraper import EarningsScraper
        scraper = EarningsScraper(api_key="")
        event = self._make_event("JPM", surprise=10.0, ah_move=5.0)
        with patch.object(scraper, "_market_cap_score", return_value=60):
            score = scraper.score_earnings_event(event)
        # eps=min(100,20)*0.40=8; mc=60*0.30=18; ah=min(100,25)*0.30=7.5 → 33.5
        assert 25 <= score <= 50

    def test_filter_sp500_only_keeps_known(self):
        """AAPL and MSFT pass; XYZFAKE does not."""
        from scrapers.earnings_scraper import EarningsScraper
        scraper = EarningsScraper(api_key="")
        scraper._sp500_cache = {"AAPL", "MSFT", "NVDA"}
        events = [
            self._make_event("AAPL"),
            self._make_event("MSFT"),
            self._make_event("XYZFAKE"),
        ]
        filtered = scraper.filter_sp500_only(events)
        syms = [e.symbol for e in filtered]
        assert "AAPL" in syms
        assert "MSFT" in syms
        assert "XYZFAKE" not in syms
        assert len(filtered) == 2

    def test_filter_sp500_only_empty_list(self):
        from scrapers.earnings_scraper import EarningsScraper
        scraper = EarningsScraper(api_key="")
        scraper._sp500_cache = {"AAPL"}
        assert scraper.filter_sp500_only([]) == []

    def test_get_company_news_no_key_returns_empty(self):
        """Without an API key, get_company_news returns []."""
        from scrapers.earnings_scraper import EarningsScraper
        scraper = EarningsScraper(api_key="fake_override")
        scraper.api_key = ""   # force no-key path
        result = scraper.get_company_news("AAPL")
        assert result == []

    @patch("scrapers.earnings_scraper.requests.get")
    def test_get_todays_earnings_returns_calendar(self, mock_get, tmp_path):
        """Without API key + empty EDGAR → returns empty EarningsCalendar."""
        from scrapers.earnings_scraper import EarningsScraper, EarningsCalendar
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"hits": {"hits": []}}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        scraper = EarningsScraper(api_key="", output_dir=tmp_path)
        result = scraper.get_todays_earnings()
        assert isinstance(result, EarningsCalendar)
        assert isinstance(result.recent_beats, list)
        assert isinstance(result.upcoming, list)
        assert isinstance(result.recent_misses, list)

    def test_get_earnings_week_returns_list(self, tmp_path):
        """get_earnings_week returns a list even with no API key."""
        from scrapers.earnings_scraper import EarningsScraper
        scraper = EarningsScraper(api_key="", output_dir=tmp_path)
        with patch.object(scraper, "_finnhub_get", return_value=None):
            with patch("scrapers.earnings_scraper.yf.Ticker") as mock_ticker:
                mock_ticker.return_value.calendar = MagicMock(empty=True)
                result = scraper.get_earnings_week()
        assert isinstance(result, list)

    @patch("scrapers.earnings_scraper.requests.get")
    @patch("scrapers.earnings_scraper.time.sleep")
    def test_edgar_8k_retry_on_timeout(self, mock_sleep, mock_get):
        """SEC EDGAR failure retries once after 60s, then returns []."""
        import requests as req_module
        from scrapers.earnings_scraper import EarningsScraper
        mock_get.side_effect = req_module.exceptions.Timeout("timed out")
        scraper = EarningsScraper(api_key="")
        result = scraper._edgar_todays_8k("2026-06-23")
        assert result == []
        mock_sleep.assert_called_once_with(60)

    @patch("scrapers.earnings_scraper.requests.get")
    def test_edgar_8k_parses_hits(self, mock_get):
        """_edgar_todays_8k extracts symbol/company from EDGAR response."""
        from scrapers.earnings_scraper import EarningsScraper
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "ticker": "AAPL",
                            "entity_name": "Apple Inc.",
                            "file_date": "2026-06-23",
                            "form_type": "8-K",
                            "period_of_report": "2026-06-23",
                        }
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp
        scraper = EarningsScraper(api_key="")
        result = scraper._edgar_todays_8k("2026-06-23")
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["company"] == "Apple Inc."
