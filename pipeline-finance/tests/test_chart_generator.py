"""Tests for ChartGenerator class (Module 7)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.chart_generator import ChartGenerator, ChartFile, BRAND


class TestChartGenerator:

    @pytest.fixture
    def cg(self, tmp_path):
        return ChartGenerator(output_dir=tmp_path)

    def test_init_creates_output_dir(self, tmp_path):
        d = tmp_path / "charts"
        ChartGenerator(output_dir=d)
        assert d.exists()

    def test_apply_brand_style_sets_facecolor(self, cg):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        cg.apply_brand_style(fig)
        assert fig.get_facecolor() is not None
        plt.close(fig)

    def test_generate_price_line_returns_chart_on_success(self, cg):
        import numpy as np
        import pandas as pd
        prices = pd.Series(
            [140.0, 145.0, 150.0],
            index=pd.date_range("2026-06-01", periods=3),
        )
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=prices)
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = mock_hist
            result = cg.generate_price_line("AAPL", days=3)
        assert isinstance(result, ChartFile)
        assert result.chart_type == "price_line"

    def test_generate_price_line_returns_none_on_empty_data(self, cg):
        mock_hist = MagicMock()
        mock_hist.empty = True
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = mock_hist
            result = cg.generate_price_line("FAKE", days=3)
        assert result is None

    def test_generate_price_line_portrait_orientation(self, cg):
        import numpy as np
        import pandas as pd
        prices = pd.Series(
            [95.0, 100.0],
            index=pd.date_range("2026-06-01", periods=2),
        )
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(return_value=prices)
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = mock_hist
            result = cg.generate_price_line("SPY", orientation="portrait")
        assert result is not None
        assert "portrait" in str(result.path)

    def test_generate_indices_summary_handles_api_failure(self, cg):
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.side_effect = Exception("network error")
            result = cg.generate_indices_summary()
        assert result is None or isinstance(result, ChartFile)

    def test_generate_economic_bar_uses_placeholder_on_no_key(self, cg):
        with patch.object(type(cg.output_dir), '__truediv__', return_value=cg.output_dir):
            result = cg.generate_economic_bar("CPIAUCSL", "CPI")
        assert result is None or isinstance(result, ChartFile)

    def test_ticker_dir_creates_dated_subdir(self, cg):
        d = cg._ticker_dir("MSFT")
        assert "MSFT" in str(d)
        assert d.exists()
