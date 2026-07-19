"""
builders/slide_renderer.py — DriftWire326 visual design v2
Branded PIL-rendered slides that give the video narrative flow instead of
raw analysis charts back-to-back:

    intro title card → market dashboard (big numbers) → index chart →
    movers board → gainers/losers chart → economic panel → candlestick →
    outro card

Every renderer is best-effort: any failure returns None and the video
falls back to whatever visuals exist.

Design language: dark navy background, rounded surface panels, green/red
market accents, DejaVu type. All slides render at the configured video
resolution so nothing is scaled or blurred.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

SLIDES_DIR = settings.output_dir / "slides"

# Brand palette
BG       = (10, 10, 15)
SURFACE  = (20, 21, 30)
SURFACE2 = (28, 30, 42)
TEXT     = (240, 242, 245)
MUTED    = (140, 148, 165)
GREEN    = (0, 196, 107)
RED      = (255, 75, 75)
ACCENT   = (120, 190, 255)
GOLD     = (255, 196, 0)


def _size() -> tuple[int, int]:
    return (settings.video_width, settings.video_height)


def _font(px: int, bold: bool = True):
    from PIL import ImageFont
    name = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    try:
        return ImageFont.truetype(name, px)
    except Exception:
        return ImageFont.load_default()


def _canvas():
    from PIL import Image, ImageDraw
    w, h = _size()
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    # Subtle top brand bar
    draw.rectangle((0, 0, w, 8), fill=(90, 40, 200))
    return img, draw, w, h


def _panel(draw, box, fill=SURFACE, radius=18):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _brand_footer(draw, w, h):
    draw.text((w // 2, h - 34), "@DriftWire326  •  Daily U.S. Market Briefings",
              font=_font(int(h * 0.028), bold=False), fill=MUTED, anchor="mm")


def _paste_logo(img, w, h, size_ratio: float = 0.085, pos: str = "top-right"):
    try:
        from builders.logo_overlay import get_round_logo
        from PIL import Image
        size = int(h * size_ratio)
        logo_path = get_round_logo(size)
        if logo_path:
            logo = Image.open(logo_path).convert("RGBA")
            xy = (w - size - 28, 24) if pos == "top-right" else ((w - size) // 2, int(h * 0.16))
            img.paste(logo, xy, logo)
    except Exception:
        pass


def _save(img, name: str) -> Path:
    SLIDES_DIR.mkdir(parents=True, exist_ok=True)
    out = SLIDES_DIR / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    img.save(out)
    return out


def _pct_color(value: float):
    return GREEN if value >= 0 else RED


def _fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


# ── Slides ────────────────────────────────────────────────────────────────────

def render_intro_slide(title: str, date_label: Optional[str] = None) -> Optional[Path]:
    """Opening title card: date, headline, brand."""
    try:
        img, draw, w, h = _canvas()
        date_label = date_label or datetime.now().strftime("%A, %B %d, %Y")

        _paste_logo(img, w, h, size_ratio=0.16, pos="center-top")
        draw.text((w // 2, int(h * 0.12)), "DAILY MARKET BRIEFING",
                  font=_font(int(h * 0.038)), fill=ACCENT, anchor="mm")
        draw.text((w // 2, int(h * 0.40)), date_label,
                  font=_font(int(h * 0.030), bold=False), fill=MUTED, anchor="mm")

        # Headline, wrapped to two lines max
        words, lines, line = title.split(), [], ""
        for word in words:
            trial = f"{line} {word}".strip()
            if len(trial) > 34 and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        y = int(h * 0.52)
        for text_line in lines[:2]:
            draw.text((w // 2, y), text_line,
                      font=_font(int(h * 0.062)), fill=TEXT, anchor="mm")
            y += int(h * 0.085)

        _brand_footer(draw, w, h)
        return _save(img, "intro")
    except Exception as exc:
        logger.warning("Intro slide failed: %s", exc)
        return None


def render_market_slide(market) -> Optional[Path]:
    """Dashboard: S&P / Nasdaq / Dow / VIX as four big-number panels."""
    try:
        img, draw, w, h = _canvas()
        draw.text((w // 2, int(h * 0.10)), "WHERE THE MARKET CLOSED",
                  font=_font(int(h * 0.042)), fill=TEXT, anchor="mm")
        _paste_logo(img, w, h)

        tiles = [
            ("S&P 500", market.sp500.price, market.sp500.change_pct),
            ("NASDAQ", market.nasdaq.price, market.nasdaq.change_pct),
            ("DOW", market.dow.price, market.dow.change_pct),
            ("VIX", market.vix.price, market.vix.change_pct),
        ]
        cols, rows = 2, 2
        margin_x, top, gap = int(w * 0.08), int(h * 0.18), int(w * 0.03)
        tile_w = (w - 2 * margin_x - gap) // cols
        tile_h = int(h * 0.28)

        for i, (label, price, pct) in enumerate(tiles):
            row, col = divmod(i, cols)
            x0 = margin_x + col * (tile_w + gap)
            y0 = top + row * (tile_h + int(h * 0.04))
            _panel(draw, (x0, y0, x0 + tile_w, y0 + tile_h))
            cx = x0 + tile_w // 2
            draw.text((cx, y0 + int(tile_h * 0.22)), label,
                      font=_font(int(h * 0.030)), fill=MUTED, anchor="mm")
            draw.text((cx, y0 + int(tile_h * 0.52)), f"{price:,.2f}",
                      font=_font(int(h * 0.052)), fill=TEXT, anchor="mm")
            draw.text((cx, y0 + int(tile_h * 0.80)), _fmt_pct(pct),
                      font=_font(int(h * 0.040)), fill=_pct_color(pct), anchor="mm")

        _brand_footer(draw, w, h)
        return _save(img, "market")
    except Exception as exc:
        logger.warning("Market slide failed: %s", exc)
        return None


def render_movers_slide(market) -> Optional[Path]:
    """Two-column board: top gainers vs top losers."""
    try:
        img, draw, w, h = _canvas()
        draw.text((w // 2, int(h * 0.10)), "TODAY'S BIGGEST MOVERS",
                  font=_font(int(h * 0.042)), fill=TEXT, anchor="mm")
        _paste_logo(img, w, h)

        margin_x, top = int(w * 0.06), int(h * 0.18)
        col_w = (w - 3 * margin_x) // 2
        col_h = int(h * 0.62)

        for col, (heading, color, movers) in enumerate([
            ("GAINERS", GREEN, list(market.top_gainers)[:4]),
            ("LOSERS", RED, list(market.top_losers)[:4]),
        ]):
            x0 = margin_x + col * (col_w + margin_x)
            _panel(draw, (x0, top, x0 + col_w, top + col_h))
            draw.text((x0 + col_w // 2, top + int(h * 0.055)), heading,
                      font=_font(int(h * 0.034)), fill=color, anchor="mm")
            y = top + int(h * 0.13)
            for snap in movers:
                arrow = "▲" if snap.change_pct >= 0 else "▼"
                draw.text((x0 + int(col_w * 0.08), y), f"{arrow} {snap.symbol}",
                          font=_font(int(h * 0.034)), fill=TEXT, anchor="lm")
                draw.text((x0 + int(col_w * 0.92), y), _fmt_pct(snap.change_pct),
                          font=_font(int(h * 0.034)), fill=_pct_color(snap.change_pct),
                          anchor="rm")
                y += int(h * 0.115)

        _brand_footer(draw, w, h)
        return _save(img, "movers")
    except Exception as exc:
        logger.warning("Movers slide failed: %s", exc)
        return None


def render_econ_slide(economic) -> Optional[Path]:
    """Economic indicators panel — name, value, change per row."""
    try:
        indicators = list(getattr(economic, "indicators", {}).values())[:5]
        if not indicators:
            return None

        img, draw, w, h = _canvas()
        draw.text((w // 2, int(h * 0.10)), "THE ECONOMIC PICTURE",
                  font=_font(int(h * 0.042)), fill=TEXT, anchor="mm")
        _paste_logo(img, w, h)

        margin_x, top = int(w * 0.08), int(h * 0.18)
        row_h = int(h * 0.125)
        for i, ind in enumerate(indicators):
            y0 = top + i * (row_h + int(h * 0.018))
            _panel(draw, (margin_x, y0, w - margin_x, y0 + row_h),
                   fill=SURFACE if i % 2 == 0 else SURFACE2, radius=14)
            cy = y0 + row_h // 2
            draw.text((margin_x + int(w * 0.025), cy), ind.name,
                      font=_font(int(h * 0.032)), fill=TEXT, anchor="lm")
            value_text = f"{ind.value:g} {ind.unit}".strip()
            draw.text((w - margin_x - int(w * 0.16), cy), value_text,
                      font=_font(int(h * 0.032)), fill=ACCENT, anchor="rm")
            if ind.change is not None:
                draw.text((w - margin_x - int(w * 0.025), cy), f"{ind.change:+g}",
                          font=_font(int(h * 0.030)),
                          fill=_pct_color(ind.change), anchor="rm")

        _brand_footer(draw, w, h)
        return _save(img, "econ")
    except Exception as exc:
        logger.warning("Econ slide failed: %s", exc)
        return None


def render_outro_slide() -> Optional[Path]:
    """Closing card: what to watch + subscribe prompt."""
    try:
        img, draw, w, h = _canvas()
        _paste_logo(img, w, h, size_ratio=0.15, pos="center-top")
        draw.text((w // 2, int(h * 0.44)), "NEW BRIEFINGS EVERY MARKET DAY",
                  font=_font(int(h * 0.046)), fill=TEXT, anchor="mm")
        draw.text((w // 2, int(h * 0.56)), "8 AM pre-market  •  5 PM close  •  Sunday deep-dives",
                  font=_font(int(h * 0.030), bold=False), fill=MUTED, anchor="mm")
        _panel(draw, (w // 2 - int(w * 0.14), int(h * 0.66), w // 2 + int(w * 0.14), int(h * 0.76)),
               fill=(190, 30, 45), radius=16)
        draw.text((w // 2, int(h * 0.71)), "SUBSCRIBE",
                  font=_font(int(h * 0.038)), fill=TEXT, anchor="mm")
        _brand_footer(draw, w, h)
        return _save(img, "outro")
    except Exception as exc:
        logger.warning("Outro slide failed: %s", exc)
        return None


def build_visual_sequence(market, economic, chart_paths: list, title: str) -> list:
    """
    Assemble the ordered visual timeline for a weekday video: branded
    slides + topical Pexels B-roll photos + the most video-friendly charts.
    Falls back gracefully: any element that fails to render is skipped,
    and if nearly everything fails the original chart list is returned.
    """
    charts = {p.name.split("_")[0]: p for p in chart_paths if p and p.exists()}

    # Topical photo B-roll (requires PEXELS_API_KEY; [] otherwise)
    broll: list = []
    try:
        from builders.broll_fetcher import get_broll_slides
        story = ""
        try:
            movers = list(market.top_gainers) + list(market.top_losers)
            biggest = max(movers, key=lambda s: abs(s.change_pct))
            story = f"{biggest.name} company"
        except Exception:
            pass
        terms = [t for t in (story, "stock market analysis") if t]
        broll = get_broll_slides(terms, count=3)
    except Exception as exc:
        logger.warning("B-roll unavailable: %s", exc)

    def _pick(index: int):
        return broll[index] if index < len(broll) else None

    sequence = [
        render_intro_slide(title),
        _pick(1),                                   # generic market scene
        render_market_slide(market),
        charts.get("index"),
        _pick(0),                                   # story-specific photo
        render_movers_slide(market),
        charts.get("gainers"),
        render_econ_slide(economic) if economic is not None else None,
        charts.get("candlestick"),
        _pick(2),
        render_outro_slide(),
    ]
    visuals = [p for p in sequence if p is not None]
    if len(visuals) < 3:
        logger.warning("Slide rendering mostly failed — using raw charts")
        return list(chart_paths)
    logger.info("Visual sequence: %d slides/photos/charts", len(visuals))
    return visuals
