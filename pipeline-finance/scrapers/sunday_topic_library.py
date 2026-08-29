"""
scrapers/sunday_topic_library.py — DriftWire326
Manages the Sunday educational topic pool: weighted random selection,
12-week cooldown tracking, and market-context relevance scoring.
"""
import json
import logging
import os
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from monitor.usage_ledger import record

logger = logging.getLogger(__name__)

# The topic pool: read-only, version-controlled, ships with the code.
_LIBRARY_PATH = Path(__file__).parent / "sunday_topic_library.json"

# Which topics have run and when. This has to live outside the repo.
#
# Rotation history used to be written back into the library JSON, which git
# tracks — and deploy/update.sh does `git reset --hard`, so every deploy
# silently wiped it and the 12-week cooldown restarted from nothing. Two
# deploys in one afternoon reset it twice. Keeping it under logs/ means it
# survives deploys and is picked up by the weekly state backup.
_STATE_NAME = "sunday_topic_state.json"


# ── Data helpers ─────────────────────────────────────────────────────────────

def _load() -> dict:
    return json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))


def _state_path() -> Path:
    from config.settings import settings
    return settings.logs_dir / _STATE_NAME


def _load_state() -> dict:
    """
    {topic_id: iso date last used}.

    Falls back to any history still sitting in the library file, so a machine
    upgrading from the old layout keeps whatever rotation survived.
    """
    path = _state_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("last_used", {})
        except Exception as exc:
            logger.warning("Topic state unreadable (%s) — starting fresh", exc)
            return {}
    try:
        return _load().get("last_used", {}) or {}
    except Exception:
        return {}


def _save_state(last_used: dict) -> None:
    """Never raises: losing a cooldown entry must not fail a publish."""
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"last_used": last_used}, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("Could not record topic rotation (non-fatal): %s", exc)


# ── Topic selection ──────────────────────────────────────────────────────────

def get_available_topics(cooldown_weeks: int = 12) -> list[dict]:
    """Return topics not used within the cooldown window."""
    library = _load()
    cutoff = (date.today() - timedelta(weeks=cooldown_weeks)).isoformat()
    last_used: dict = _load_state()
    available = [
        t for t in library["topics"]
        if last_used.get(t["id"], "1970-01-01") < cutoff
    ]
    if not available:
        logger.warning("All Sunday topics in cooldown — using full pool")
        available = library["topics"]
    return available


def pick_topic(week_market_summary: str = "", use_ai: bool = False) -> dict:
    """
    Select this week's Sunday topic.

    Strategy:
    1. If use_ai=True and ANTHROPIC_API_KEY set → ask Claude via
       SUNDAY_TOPIC_SELECTOR_PROMPT for market-relevant selection.
    2. Otherwise → weighted random from the eligible (non-cooldown) pool.

    Returns the chosen topic dict and marks it as used.
    """
    library = _load()
    weights_map: dict = library["rotation_schedule"]["weights"]
    cooldown_weeks: int = library["rotation_schedule"]["cooldown_weeks"]
    available = get_available_topics(cooldown_weeks)

    chosen: Optional[dict] = None

    if use_ai and week_market_summary:
        chosen = _ai_pick(available, week_market_summary)

    if chosen is None:
        # Weighted random fallback
        pool: list[dict] = []
        for topic in available:
            weight = weights_map.get(topic.get("estimated_views", "medium"), 1)
            pool.extend([topic] * weight)
        chosen = random.choice(pool)

    mark_used(chosen["id"])
    logger.info("Sunday topic selected: %s (%d of %d eligible)",
                chosen["title"], len(available), len(library["topics"]))
    return chosen


def _ai_pick(available: list[dict], week_market_summary: str) -> Optional[dict]:
    """Ask Claude which topic is most relevant to this week's market events."""
    try:
        import anthropic
        import re, json as _json
        from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
        from config.prompts import SUNDAY_TOPIC_SELECTOR_PROMPT

        if not ANTHROPIC_API_KEY:
            return None

        topics_list = "\n".join(
            f"- {t['id']}: {t['title']}" for t in available
        )
        prompt = SUNDAY_TOPIC_SELECTOR_PROMPT.format(
            theme="contextual",
            week_market_summary=week_market_summary[:800],
            available_topics_list=topics_list,
        )
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        record(resp, "sunday_topics")
        raw = resp.content[0].text
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = _json.loads(match.group())
            topic_id = result.get("selected_topic", "").strip()
            # Match returned id to available list (id or title prefix)
            for t in available:
                if t["id"] == topic_id or t["title"].startswith(topic_id[:20]):
                    logger.info("AI topic pick: %s (confidence %.2f)",
                                t["id"], result.get("confidence", 0))
                    return t
    except Exception as exc:
        logger.warning("AI topic selection failed: %s — falling back to weighted random", exc)
    return None


def get_topics_for_theme(theme: str) -> list[dict]:
    """
    Return topics that match a theme keyword.
    Used by the Sunday scheduler to filter the pool to the week's theme.
    Themes: investment_banking, insurance_protection, savings_wealth, rotating_bonus
    """
    theme_keywords = {
        "investment_banking": [
            "options", "short", "bond", "sp500", "etf", "fed", "earnings",
            "ipo", "buyback", "split", "index", "market_cap", "algo", "hft",
            "quant", "factor", "dark_pool", "order", "arbitrage", "yield",
            "rate", "bid_ask", "market_open", "payment_order",
        ],
        "insurance_protection": [
            "recession", "inflation", "risk", "portfolio", "crash", "margin",
            "bank_failure", "bank_run", "systemic", "black_swan", "circuit",
            "drawdown", "stagflation", "sovereign", "everyone_sells",
        ],
        "savings_wealth": [
            "dividend", "roth", "savings", "compound", "passive", "expense",
            "target_date", "dollar_cost", "diversification", "reit",
            "index_vs_active", "credit_score", "ladder",
        ],
        "rotating_bonus": [
            "crypto", "bitcoin", "ai_", "real_estate", "token", "stablecoin",
            "cbdc", "defi", "programmable", "cash", "settlement", "gold",
            "oil", "dollar", "currency", "commodity", "capital_flows",
        ],
    }
    keywords = theme_keywords.get(theme, [])
    library = _load()
    if not keywords:
        return library["topics"]
    # Match the id or any tag: ids are terse, tags carry the searchable words.
    matched = [
        t for t in library["topics"]
        if any(kw in t["id"] for kw in keywords)
        or any(kw.replace("_", " ") in tag.lower()
               for kw in keywords for tag in t.get("tags", []))
    ]
    return matched or library["topics"]


def mark_used(topic_id: str) -> None:
    """Record that a topic ran today, starting its cooldown."""
    last_used = _load_state()
    last_used[topic_id] = date.today().isoformat()
    _save_state(last_used)


def reset_cooldowns() -> None:
    """Clear all cooldown history — for development and manual overrides."""
    _save_state({})
    logger.info("All Sunday topic cooldowns reset")
