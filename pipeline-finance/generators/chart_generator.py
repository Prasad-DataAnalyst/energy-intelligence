"""
Chart generator — produces branded financial charts as PNG frames for video.
Uses matplotlib + mplfinance. All charts match DriftWire326 brand colors.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # non-interactive backend for server environments
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.ticker import FuncFormatter
import numpy as np

try:
    import yfinance as yf
    import mplfinance as mpf
    HAS_MPF = True
except ImportError:
    HAS_MPF = False
    logging.getLogger(__name__).warning("mplfinance not installed — candlestick charts disabled")

from config.settings import settings

logger = logging.getLogger(__name__)

OUTPUT_DIR = settings.output_dir / "charts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Brand Style ────────────────────────────────────────────────────────────
BRAND = {
    "bg": "#0A0A0F",
    "surface": "#12121A",
    "primary": "#FF0033",
    "accent": "#FFD700",
    "green": "#00CC66",
    "blue": "#0088FF",
    "text": "#FFFFFF",
    "text2": "#9999BB",
    "grid": "#1E1E2E",
}

plt.rcParams.update({
    "figure.facecolor": BRAND["bg"],
    "axes.facecolor": BRAND["surface"],
    "axes.edgecolor": BRAND["grid"],
    "axes.labelcolor": BRAND["text2"],
    "xtick.color": BRAND["text2"],
    "ytick.color": BRAND["text2"],
    "text.color": BRAND["text"],
    "grid.color": BRAND["grid"],
    "grid.linestyle": "--",
    "grid.alpha": 0.5,
    "font.family": "DejaVu Sans",
})


@dataclass
class ChartFile:
    chart_type: str
    path: Path
    title: str
    generated_at: str


def _add_brand_watermark(ax: plt.Axes, alpha: float = 0.15) -> None:
    ax.text(0.98, 0.02, "@DriftWire326",
            transform=ax.transAxes,
            fontsize=9, color=BRAND["text"], alpha=alpha,
            ha="right", va="bottom", style="italic")


def _save_chart(fig: plt.Figure, name: str) -> Path:
    path = OUTPUT_DIR / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight",
                facecolor=BRAND["bg"], edgecolor="none")
    plt.close(fig)
    logger.info("Chart saved → %s", path)
    return path


def generate_index_performance_chart(
    performance: dict[str, float],  # {"S&P 500": 1.2, "Nasdaq": -0.5, ...}
    title: str = "Market Performance Today",
) -> ChartFile:
    """Horizontal bar chart of index/sector performance."""
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(BRAND["bg"])

    labels = list(performance.keys())
    values = list(performance.values())
    colors = [BRAND["green"] if v >= 0 else BRAND["primary"] for v in values]

    bars = ax.barh(labels, values, color=colors, height=0.6, zorder=3)
    ax.axvline(0, color=BRAND["text2"], linewidth=0.8, alpha=0.5)
    ax.grid(axis="x", zorder=0)

    for bar, val in zip(bars, values):
        x_pos = val + (0.05 if val >= 0 else -0.05)
        ha = "left" if val >= 0 else "right"
        ax.text(x_pos, bar.get_y() + bar.get_height() / 2,
                f"{val:+.2f}%", va="center", ha=ha,
                color=BRAND["text"], fontsize=10, fontweight="bold")

    ax.set_title(title, color=BRAND["text"], fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel("Daily Change (%)", color=BRAND["text2"])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:+.1f}%"))
    _add_brand_watermark(ax)
    fig.tight_layout()

    path = _save_chart(fig, "index_performance")
    return ChartFile("index_performance", path, title, datetime.now().isoformat())


def generate_candlestick_chart(
    symbol: str,
    period: str = "1mo",
    interval: str = "1d",
) -> Optional[ChartFile]:
    """OHLCV candlestick chart for a given ticker."""
    if not HAS_MPF:
        logger.error("mplfinance not available — skipping candlestick")
        return None

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval)
        if hist.empty:
            logger.warning("No data for %s", symbol)
            return None

        mc = mpf.make_marketcolors(
            up=BRAND["green"], down=BRAND["primary"],
            edge="inherit", wick="inherit",
            volume={"up": BRAND["green"], "down": BRAND["primary"]},
        )
        style = mpf.make_mpf_style(
            marketcolors=mc,
            facecolor=BRAND["surface"],
            figcolor=BRAND["bg"],
            gridcolor=BRAND["grid"],
            gridstyle="--",
            gridaxis="both",
            y_on_right=True,
            rc={"font.family": "DejaVu Sans", "text.color": BRAND["text"]},
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = OUTPUT_DIR / f"candlestick_{symbol}_{timestamp}.png"

        fig, axes = mpf.plot(
            hist, type="candle", style=style,
            title=f"{symbol} — {period}",
            volume=True,
            figsize=(12, 6),
            returnfig=True,
            tight_layout=True,
        )
        _add_brand_watermark(axes[0])
        fig.savefig(path, dpi=150, bbox_inches="tight",
                    facecolor=BRAND["bg"])
        plt.close(fig)
        logger.info("Candlestick chart saved → %s", path)
        return ChartFile("candlestick", path, f"{symbol} Price Chart", datetime.now().isoformat())

    except Exception as exc:
        logger.error("Candlestick chart failed for %s: %s", symbol, exc)
        return None


def generate_sector_heatmap(sector_performance: dict[str, float]) -> ChartFile:
    """Treemap-style sector heatmap — bigger box = larger sector weight."""
    sector_weights = {
        "Technology": 28, "Healthcare": 13, "Financials": 13, "Consumer Disc.": 10,
        "Communication": 9, "Industrials": 9, "Consumer Staples": 7,
        "Energy": 5, "Real Estate": 3, "Materials": 2, "Utilities": 2,
    }

    fig, ax = plt.subplots(figsize=(12, 6))
    x, y, w = 0, 0, 12

    items = []
    for sector, perf in sector_performance.items():
        weight = sector_weights.get(sector, 3)
        items.append((sector, perf, weight))

    items.sort(key=lambda i: i[2], reverse=True)
    colors = plt.cm.RdYlGn(np.interp([i[1] for i in items], [-3, 3], [0, 1]))

    n = len(items)
    cols = 4
    rows = (n + cols - 1) // cols
    cell_w = w / cols
    cell_h = 6 / rows

    for idx, (sector, perf, weight) in enumerate(items):
        col = idx % cols
        row = idx // cols
        cx, cy = col * cell_w, (rows - row - 1) * cell_h
        color = plt.cm.RdYlGn(np.interp(perf, [-3, 3], [0, 1]))
        rect = mpatches.FancyBboxPatch(
            (cx + 0.05, cy + 0.05), cell_w - 0.1, cell_h - 0.1,
            boxstyle="round,pad=0.05",
            facecolor=color, edgecolor=BRAND["bg"], linewidth=1.5,
        )
        ax.add_patch(rect)
        ax.text(cx + cell_w / 2, cy + cell_h / 2 + 0.1,
                sector, ha="center", va="center",
                fontsize=9, fontweight="bold", color="#000000" if abs(perf) < 1.5 else BRAND["text"])
        ax.text(cx + cell_w / 2, cy + cell_h / 2 - 0.2,
                f"{perf:+.2f}%", ha="center", va="center",
                fontsize=11, fontweight="bold", color="#000000" if abs(perf) < 1.5 else BRAND["text"])

    ax.set_xlim(0, w)
    ax.set_ylim(0, 6)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Sector Performance Heatmap", color=BRAND["text"], fontsize=14, fontweight="bold", pad=15)
    _add_brand_watermark(ax)
    fig.patch.set_facecolor(BRAND["bg"])
    fig.tight_layout()

    path = _save_chart(fig, "sector_heatmap")
    return ChartFile("sector_heatmap", path, "Sector Performance Heatmap", datetime.now().isoformat())


def generate_gainers_losers_chart(
    gainers: list[tuple[str, float]],
    losers: list[tuple[str, float]],
) -> ChartFile:
    """Side-by-side bars of top gainers and losers."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.patch.set_facecolor(BRAND["bg"])

    g_symbols = [g[0] for g in gainers[:5]]
    g_values = [g[1] for g in gainers[:5]]
    l_symbols = [l[0] for l in losers[:5]]
    l_values = [l[1] for l in losers[:5]]

    ax1.barh(g_symbols, g_values, color=BRAND["green"], height=0.6, zorder=3)
    ax1.set_title("Top Gainers 🟢", color=BRAND["green"], fontsize=12, fontweight="bold")
    ax1.grid(axis="x", zorder=0)
    for i, val in enumerate(g_values):
        ax1.text(val + 0.1, i, f"+{val:.2f}%", va="center", color=BRAND["green"], fontweight="bold")

    ax2.barh(l_symbols, l_values, color=BRAND["primary"], height=0.6, zorder=3)
    ax2.set_title("Top Losers 🔴", color=BRAND["primary"], fontsize=12, fontweight="bold")
    ax2.grid(axis="x", zorder=0)
    for i, val in enumerate(l_values):
        ax2.text(val - 0.1, i, f"{val:.2f}%", va="center", ha="right", color=BRAND["primary"], fontweight="bold")

    for ax in [ax1, ax2]:
        _add_brand_watermark(ax)
        ax.set_facecolor(BRAND["surface"])

    fig.suptitle(f"Market Movers — {datetime.now().strftime('%b %d, %Y')}",
                 color=BRAND["text"], fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()

    path = _save_chart(fig, "gainers_losers")
    return ChartFile("gainers_losers", path, "Market Movers", datetime.now().isoformat())


def generate_all_charts(market_summary) -> list[ChartFile]:
    """Generate full chart pack from a MarketSummary object."""
    charts = []

    perf = {
        "S&P 500": market_summary.sp500.change_pct,
        "Nasdaq": market_summary.nasdaq.change_pct,
        "Dow": market_summary.dow.change_pct,
        "Russell 2k": market_summary.russell2000.change_pct,
    }
    charts.append(generate_index_performance_chart(perf))
    charts.append(generate_sector_heatmap(market_summary.sector_performance))

    gainers = [(s.symbol, s.change_pct) for s in market_summary.top_gainers]
    losers = [(s.symbol, s.change_pct) for s in market_summary.top_losers]
    charts.append(generate_gainers_losers_chart(gainers, losers))

    candle = generate_candlestick_chart("SPY", period="1mo")
    if candle:
        charts.append(candle)

    logger.info("Generated %d charts", len(charts))
    return charts
