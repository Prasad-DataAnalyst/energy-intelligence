"""
Chart generator — produces branded financial charts as PNG frames for video.
Uses matplotlib + mplfinance. All charts match DriftWire326 brand colors.
"""
import logging
import subprocess
import tempfile
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


# ── ChartGenerator class ─────────────────────────────────────────────────────

class ChartGenerator:
    """Class-based chart generation for DriftWire326 pipeline."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _ticker_dir(self, ticker: str) -> Path:
        today = datetime.now().strftime("%Y%m%d")
        d = self.output_dir / f"{ticker}_{today}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def apply_brand_style(self, fig: plt.Figure) -> None:
        """Apply DriftWire326 dark brand colors to a figure and all its axes."""
        fig.patch.set_facecolor(BRAND["bg"])
        for ax in fig.get_axes():
            ax.set_facecolor(BRAND["surface"])
            ax.tick_params(colors=BRAND["text2"])
            ax.xaxis.label.set_color(BRAND["text2"])
            ax.yaxis.label.set_color(BRAND["text2"])
            ax.title.set_color(BRAND["text"])
            for spine in ax.spines.values():
                spine.set_edgecolor(BRAND["grid"])
            ax.grid(True, color=BRAND["grid"], linestyle="--", alpha=0.5)

    def generate_price_line(
        self,
        ticker: str,
        days: int = 30,
        orientation: str = "landscape",
    ) -> Optional[ChartFile]:
        """
        Line chart for ticker over `days` days.
        orientation: 'landscape' (1920×1080) or 'portrait' (1080×1920 for Shorts).
        Line color: green if price went up, red if down.
        """
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            hist = t.history(period=f"{days}d")
            if hist.empty:
                logger.warning("No price data for %s", ticker)
                return None

            prices = hist["Close"]
            up = prices.iloc[-1] >= prices.iloc[0]
            line_color = BRAND["green"] if up else BRAND["primary"]

            if orientation == "portrait":
                figsize = (6, 10.67)  # ~1080×1920 ratio
            else:
                figsize = (16, 9)    # 1920×1080 ratio

            fig, ax = plt.subplots(figsize=figsize)
            self.apply_brand_style(fig)

            ax.plot(prices.index, prices.values, color=line_color, linewidth=2.5, zorder=3)
            ax.fill_between(prices.index, prices.values, prices.min(),
                            color=line_color, alpha=0.15, zorder=2)

            ax.set_title(f"{ticker} — {days}d Price", fontsize=14, fontweight="bold")
            ax.set_xlabel("")
            ax.set_ylabel("Price (USD)")
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.2f}"))
            _add_brand_watermark(ax)
            fig.tight_layout()

            out_dir = self._ticker_dir(ticker)
            path = out_dir / f"price_line_{ticker}_{orientation}_{datetime.now().strftime('%H%M%S')}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BRAND["bg"])
            plt.close(fig)
            logger.info("Price line chart saved → %s", path)
            return ChartFile("price_line", path, f"{ticker} Price", datetime.now().isoformat())

        except Exception as exc:
            logger.error("generate_price_line failed for %s: %s", ticker, exc)
            return None

    def generate_candlestick(
        self,
        ticker: str,
        days: int = 14,
    ) -> Optional[ChartFile]:
        """Landscape candlestick chart with volume bars."""
        if not HAS_MPF:
            logger.warning("mplfinance not available — falling back to line chart")
            return self.generate_price_line(ticker, days)

        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            hist = t.history(period=f"{days}d")
            if hist.empty:
                logger.warning("No data for %s", ticker)
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

            out_dir = self._ticker_dir(ticker)
            path = out_dir / f"candlestick_{ticker}_{datetime.now().strftime('%H%M%S')}.png"

            fig, axes = mpf.plot(
                hist, type="candle", style=style,
                title=f"{ticker} — {days}d",
                volume=True, figsize=(16, 9),
                returnfig=True, tight_layout=True,
            )
            _add_brand_watermark(axes[0])
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BRAND["bg"])
            plt.close(fig)
            logger.info("Candlestick saved → %s", path)
            return ChartFile("candlestick", path, f"{ticker} Candlestick", datetime.now().isoformat())

        except Exception as exc:
            logger.error("generate_candlestick failed for %s: %s", ticker, exc)
            return None

    def generate_animated_line(self, ticker: str) -> Optional[Path]:
        """
        Render a price-line-draws-itself animation over 3 seconds.
        Returns MP4 path or None if ffmpeg unavailable.
        Generates 30fps * 3s = 90 PNG frames then encodes with ffmpeg.
        """
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            hist = t.history(period="30d")
            if hist.empty:
                return None

            prices = hist["Close"].values
            up = prices[-1] >= prices[0]
            line_color = BRAND["green"] if up else BRAND["primary"]

            fps = 30
            duration = 3
            n_frames = fps * duration
            n_points = len(prices)

            with tempfile.TemporaryDirectory() as tmp:
                frame_dir = Path(tmp)
                for frame_idx in range(n_frames):
                    # draw progressively more points each frame
                    pts = max(2, int((frame_idx + 1) / n_frames * n_points))
                    fig, ax = plt.subplots(figsize=(16, 9))
                    self.apply_brand_style(fig)
                    ax.plot(range(pts), prices[:pts], color=line_color, linewidth=2.5)
                    ax.set_xlim(0, n_points)
                    ax.set_ylim(prices.min() * 0.98, prices.max() * 1.02)
                    ax.set_title(f"{ticker} — 30d Price", fontsize=14, fontweight="bold")
                    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.2f}"))
                    _add_brand_watermark(ax)
                    fig.tight_layout()
                    fig.savefig(frame_dir / f"frame_{frame_idx:04d}.png",
                                dpi=100, bbox_inches="tight", facecolor=BRAND["bg"])
                    plt.close(fig)

                out_dir = self._ticker_dir(ticker)
                mp4_path = out_dir / f"animated_{ticker}_{datetime.now().strftime('%H%M%S')}.mp4"
                cmd = [
                    "ffmpeg", "-y", "-framerate", str(fps),
                    "-i", str(frame_dir / "frame_%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "23", str(mp4_path),
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=60)
                if result.returncode != 0:
                    logger.error("ffmpeg animation failed: %s", result.stderr.decode())
                    return None
                logger.info("Animated line MP4 → %s", mp4_path)
                return mp4_path

        except Exception as exc:
            logger.error("generate_animated_line failed for %s: %s", ticker, exc)
            return None

    def generate_economic_bar(self, series_id: str, label: str) -> Optional[ChartFile]:
        """
        Bar chart comparing latest vs previous value for a FRED series.
        Falls back to placeholder bars if FRED unavailable.
        """
        try:
            from config.settings import settings
            fred_key = getattr(settings, "fred_api_key", "")
            latest_val = prev_val = None

            if fred_key:
                import urllib.request
                import json as _json
                url = (
                    f"https://api.stlouisfed.org/fred/series/observations"
                    f"?series_id={series_id}&api_key={fred_key}"
                    f"&file_type=json&sort_order=desc&limit=2"
                )
                with urllib.request.urlopen(url, timeout=10) as r:
                    data = _json.loads(r.read())
                obs = [o for o in data.get("observations", []) if o["value"] != "."]
                if len(obs) >= 2:
                    latest_val = float(obs[0]["value"])
                    prev_val = float(obs[1]["value"])

            if latest_val is None:
                latest_val, prev_val = 3.2, 3.4  # placeholder

            fig, ax = plt.subplots(figsize=(8, 6))
            self.apply_brand_style(fig)

            colors = [BRAND["green"] if latest_val <= prev_val else BRAND["primary"],
                      BRAND["text2"]]
            bars = ax.bar(["Latest", "Previous"], [latest_val, prev_val],
                          color=colors, width=0.5, zorder=3)

            for bar, val in zip(bars, [latest_val, prev_val]):
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                        f"{val:.2f}", ha="center", va="bottom",
                        color=BRAND["text"], fontsize=12, fontweight="bold")

            ax.set_title(f"{label} ({series_id})", fontsize=13, fontweight="bold")
            ax.set_ylabel(label)
            _add_brand_watermark(ax)
            fig.tight_layout()

            path = self.output_dir / f"econ_bar_{series_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BRAND["bg"])
            plt.close(fig)
            logger.info("Economic bar chart → %s", path)
            return ChartFile("economic_bar", path, label, datetime.now().isoformat())

        except Exception as exc:
            logger.error("generate_economic_bar failed for %s: %s", series_id, exc)
            return None

    def generate_indices_summary(self) -> Optional[ChartFile]:
        """4-panel price-line chart: SPY, QQQ, DIA, IWM (landscape 1920×1080)."""
        tickers = ["SPY", "QQQ", "DIA", "IWM"]
        labels = ["S&P 500", "Nasdaq 100", "Dow Jones", "Russell 2000"]

        try:
            import yfinance as yf
            fig, axes = plt.subplots(2, 2, figsize=(16, 9))
            self.apply_brand_style(fig)
            fig.suptitle(
                f"Market Indices Summary — {datetime.now().strftime('%b %d, %Y')}",
                fontsize=14, fontweight="bold", color=BRAND["text"],
            )

            for ax, sym, lbl in zip(axes.flat, tickers, labels):
                try:
                    hist = yf.Ticker(sym).history(period="30d")["Close"]
                    up = hist.iloc[-1] >= hist.iloc[0]
                    color = BRAND["green"] if up else BRAND["primary"]
                    ax.plot(hist.index, hist.values, color=color, linewidth=2)
                    ax.fill_between(hist.index, hist.values, hist.min(), color=color, alpha=0.1)
                    chg = (hist.iloc[-1] / hist.iloc[0] - 1) * 100
                    ax.set_title(f"{lbl}  {chg:+.2f}%", fontsize=11, fontweight="bold",
                                 color=color)
                except Exception:
                    ax.text(0.5, 0.5, f"{sym}\nN/A", transform=ax.transAxes,
                            ha="center", va="center", color=BRAND["text2"])
                ax.set_facecolor(BRAND["surface"])
                ax.tick_params(colors=BRAND["text2"], labelsize=8)
                ax.grid(True, color=BRAND["grid"], linestyle="--", alpha=0.4)
                _add_brand_watermark(ax, alpha=0.10)

            fig.tight_layout()
            path = self.output_dir / f"indices_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BRAND["bg"])
            plt.close(fig)
            logger.info("Indices summary chart → %s", path)
            return ChartFile("indices_summary", path, "Market Indices Summary", datetime.now().isoformat())

        except Exception as exc:
            logger.error("generate_indices_summary failed: %s", exc)
            return None


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
