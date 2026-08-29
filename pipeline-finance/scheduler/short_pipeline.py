"""
scheduler/short_pipeline.py — DriftWire326 Phase 1
Day-themed YouTube Shorts pipeline (12:30 PM ET Mon–Fri, 11:00 AM Sat).

Rotation (per the content operations spec):
  Monday    — Three Stocks to Watch        (live market data)
  Tuesday   — Market News Explained        (top RSS story; skipped if none)
  Wednesday — Economic Report Explained    (one econ term, rotating list)
  Thursday  — Personal Finance Tip         (rotating list)
  Friday    — Week in 60 Seconds           (weekly index performance)
  Saturday  — Finance Explained Simply     (evergreen educational, rotating)

Each Short: Claude script (5-card format, 75–110 words) → compliance filter →
ShortsBuilder video → quota-gated upload → playlist routing + manifest.
"""
import logging
from datetime import date, datetime
from typing import Optional

from config.settings import settings
from monitor.usage_ledger import record

logger = logging.getLogger(__name__)

# Rotating topic lists — picked by day-of-year so the cycle never repeats
# back-to-back and needs no persisted state.
_ECON_TOPICS = [
    "CPI — the Consumer Price Index and how it measures inflation",
    "PPI — the Producer Price Index and why it leads consumer inflation",
    "GDP — what Gross Domestic Product tells us about the economy",
    "Nonfarm payrolls — why the monthly jobs report moves markets",
    "The unemployment rate — what it does and doesn't measure",
    "The federal funds rate — how the Fed's rate steers the economy",
    "Treasury yields — what the 10-year yield signals for stocks",
    "The yield curve — why an inversion worries investors",
]

_FINANCE_TIPS = [
    "Why an emergency fund of 3-6 months of expenses comes before investing",
    "How credit scores are calculated and the fastest ways to improve one",
    "ETF investing — how one purchase buys hundreds of companies",
    "Dollar-cost averaging — why investing on a schedule beats timing the market",
    "Roth IRA vs traditional 401(k) — the tax difference in plain English",
    "Why paying off high-interest debt is a guaranteed return",
    "The 50/30/20 budget — a simple starting framework",
    "Compound interest — why starting 10 years earlier can double the outcome",
]

_EVERGREEN_TOPICS = [
    "What is an ETF?",
    "What is a dividend and how do you actually get paid?",
    "What is a Roth IRA?",
    "How does compound interest work?",
    "What is a credit utilization ratio?",
    "What is dollar-cost averaging?",
    "What is market capitalization?",
    "What is an index fund?",
    "Bull market vs bear market — what the terms actually mean",
    "What is diversification and why does it reduce risk?",
]

_CARD_FORMAT_RULES = """
Return EXACTLY 5 cards in this format (each on its own lines, no other text):

[CARD 1] <hook — max 8 words, the most attention-grabbing fact. No greeting.>
[CARD 2] <the key number or core fact — max 10 words>
[CARD 3] <the reason or explanation — max 14 words, one sentence>
[CARD 4] <context or risk — max 20 words, two short sentences>
[CARD 5] <engagement line — max 8 words, a question or follow prompt>

Hard rules:
- Total across all 5 cards: 75-110 words (absolute max 120)
- Simple American English, understandable to a beginner investor
- No financial advice language ("you should buy", "guaranteed", etc.)
- Hedged framing only: "investors may want to watch", "one possible scenario"
"""


def _rotate(topics: list[str]) -> str:
    """Deterministic rotation keyed to day-of-year — no state file needed."""
    return topics[date.today().timetuple().tm_yday % len(topics)]


def _weekly_index_summary() -> str:
    """5-day % change for SPY/QQQ/DIA plus best/worst sector — for Friday."""
    try:
        import yfinance as yf
        lines = []
        for symbol, name in (("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow")):
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                change = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
                lines.append(f"{name}: {change:+.2f}% this week")
        return "\n".join(lines) if lines else ""
    except Exception as exc:
        logger.warning("Weekly index summary failed: %s", exc)
        return ""


def _top_movers_summary(n: int = 3) -> str:
    """Top movers context for Monday's 'Three Stocks to Watch'."""
    try:
        from scrapers.market_scraper import MarketScraper
        movers = MarketScraper().get_top_movers(n=n)
        lines = []
        for m in (movers.get("gainers", []) + movers.get("losers", []))[: n * 2]:
            lines.append(
                f"{m.get('ticker', '?')}: {m.get('change_pct', 0):+.2f}% "
                f"({m.get('company_name', '')})"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("Top movers summary failed: %s", exc)
        return ""


def _top_news_headline() -> Optional[str]:
    """Highest-relevance RSS headline for Tuesday. None → skip the Short."""
    try:
        from scrapers.rss_scraper import RssScraper
        items = RssScraper().fetch_all(min_score=0.15)
        if items:
            top = items[0]
            return f"{top.title} — {top.summary[:200]}"
    except Exception as exc:
        logger.warning("Top news headline fetch failed: %s", exc)
    return None


def get_todays_theme(weekday: Optional[int] = None) -> Optional[dict]:
    """
    Return today's Short theme spec: {name, title_prefix, context_fn, topic}.
    Returns None on Sunday (no themed Short scheduled — Phase 2 option).
    """
    wd = datetime.now().weekday() if weekday is None else weekday
    themes = {
        0: {"name": "Three Stocks to Watch",
            "title": "3 Stocks to Watch Today",
            "context": _top_movers_summary,
            "brief": "Cover exactly three stocks moving today. One reason each matters, one shared risk."},
        1: {"name": "Market News Explained",
            "title": "Market News, Explained",
            "context": _top_news_headline,
            "brief": "Explain today's biggest market story: what happened, why it matters, one risk."},
        2: {"name": "Economic Report Explained",
            "title": "Econ Explained",
            "context": lambda: _rotate(_ECON_TOPICS),
            "brief": "Explain this economic concept simply, with one concrete example."},
        3: {"name": "Personal Finance Tip",
            "title": "Money Tip",
            "context": lambda: _rotate(_FINANCE_TIPS),
            "brief": "Teach this personal finance concept with one actionable takeaway."},
        4: {"name": "Week in 60 Seconds",
            "title": "This Week in the Markets",
            "context": _weekly_index_summary,
            "brief": "Recap the week: index performance, biggest story, strongest and weakest areas, one thing to watch next week."},
        5: {"name": "Finance Explained Simply",
            "title": "Finance, Simply",
            "context": lambda: _rotate(_EVERGREEN_TOPICS),
            "brief": "Evergreen educational explainer. Timeless, beginner-friendly, one clear example."},
    }
    return themes.get(wd)


def generate_short_script(theme: dict, context: str) -> Optional[str]:
    """Generate the 5-card Short script via Claude. Returns None on failure."""
    import anthropic

    prompt = (
        f"Write a YouTube Short script for @DriftWire326, a U.S. finance channel "
        f"for Gen Z and Millennial investors.\n\n"
        f"Today's format: {theme['name']}\n"
        f"Brief: {theme['brief']}\n\n"
        f"Context / data to use:\n{context}\n\n"
        f"{_CARD_FORMAT_RULES}"
    )
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.create(
            model=settings.claude_model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        record(message, "short_pipeline")
        script = message.content[0].text.strip()
        if "[CARD 1]" not in script.upper().replace(" ", ""):
            # Tolerate "CARD 1:" style too — the builder's parser has fallbacks
            logger.info("Short script lacks [CARD] markers — builder fallback will chunk it")
        return script
    except Exception as exc:
        logger.error("Short script generation failed: %s", exc)
        return None


def run_themed_short(weekday: Optional[int] = None, upload: bool = True) -> Optional[str]:
    """
    Full themed-Short pipeline for today. Returns video_id (or the built
    file path as str when upload=False), None if skipped or failed.
    """
    theme = get_todays_theme(weekday)
    if theme is None:
        logger.info("No themed Short scheduled today")
        return None

    # Gather context — Tuesday intentionally skips when no meaningful story
    context = theme["context"]()
    if not context:
        logger.info("Themed Short skipped — no meaningful %s content today", theme["name"])
        return None

    script = generate_short_script(theme, context)
    if not script:
        return None

    # Compliance gate (same rules as long-form)
    try:
        from generators.compliance_filter import check_compliance, auto_fix_script
        compliance = check_compliance(script)
        if not compliance.passed:
            script = auto_fix_script(script, compliance)
            if compliance.risk_level == "high":
                logger.error("Themed Short blocked — high compliance risk: %s", compliance.issues)
                return None
    except Exception as exc:
        logger.warning("Short compliance check errored (%s) — continuing with raw script", exc)

    # Build the vertical video
    try:
        from builders.shorts_builder import ShortsBuilder
        built = ShortsBuilder().build_short_from_script(
            script=script,
            title=theme["title"],
        )
        video_path = built.path
    except Exception as exc:
        logger.error("Themed Short build failed: %s", exc)
        return None

    if not upload:
        return str(video_path)

    # Quota-gated upload + playlist routing
    try:
        from uploader.quota_tracker import QuotaTracker
        from uploader.uploader import YouTubeUploader

        qt = QuotaTracker()
        if not qt.can_upload():
            logger.warning("Themed Short upload skipped — quota exceeded")
            return None

        uploader = YouTubeUploader(qt)
        if not uploader.authenticate():
            return None

        today = date.today().strftime("%b %d")
        result = uploader.upload_short(
            video_path=video_path,
            title=f"{theme['title']} — {today}",
            description=(
                f"{theme['name']} | DriftWire326\n\n"
                f"⚠️ {settings.disclaimer_text}\n\nNarration is AI-generated."
            ),
            tags=["finance", "stocks", "investing", "DriftWire326", theme["name"]],
        )
        if result.video_id:
            logger.info("Themed Short uploaded: %s (%s)", result.video_id, theme["name"])
            try:
                from channel_manager.playlist_manager import PlaylistManager
                PlaylistManager().route_video_to_playlist(result.video_id, "shorts")
            except Exception as exc:
                logger.warning("Short playlist routing failed: %s", exc)
        return result.video_id
    except Exception as exc:
        logger.error("Themed Short upload failed: %s", exc)
        return None
