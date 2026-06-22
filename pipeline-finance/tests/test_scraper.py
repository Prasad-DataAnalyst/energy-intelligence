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
        result = _fetch_ticker("INVALID", retries=2)
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
        from scrapers.economic_scraper import EconomicIndicator, _detect_surprises
        indicators = {
            "CPIAUCSL": EconomicIndicator(
                series_id="CPIAUCSL", name="CPI", value=2.5, previous_value=3.0,
                change=-0.5, change_pct=-16.7, period="2026-05",
                unit="Percent", frequency="Monthly", source="TEST",
            )
        }
        surprises = _detect_surprises(indicators)
        assert len(surprises) == 1
        assert "CPI" in surprises[0]

    @patch.dict("os.environ", {"FRED_API_KEY": ""})
    def test_mock_fallback_when_no_api_key(self):
        from scrapers.economic_scraper import scrape_economic_data
        snapshot = scrape_economic_data()
        assert "FEDFUNDS" in snapshot.indicators
        assert snapshot.indicators["FEDFUNDS"].source == "MOCK"


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
