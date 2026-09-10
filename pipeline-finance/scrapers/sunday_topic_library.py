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


def _demand_score(topic: dict, terms: set) -> int:
    """
    How many of today's rising search terms this topic actually covers.

    Matched against the whole entry — title, subtopics, concepts and tags —
    because a searcher typing "0dte" should reach the options topic even
    though the title says "Calls, Puts, and How Wall Street Really Bets".
    """
    if not terms:
        return 0
    haystack = " ".join(str(part).lower() for part in (
        [topic.get("title", ""), topic.get("current_relevance", "")]
        + list(topic.get("subtopics") or [])
        + list(topic.get("key_concepts") or [])
        + list(topic.get("tags") or [])
    ))
    return sum(1 for term in terms if term in haystack)


def _demand_ranked(available: list, terms: set) -> list:
    """
    The eligible topics that best match today's demand, or all of them.

    Returns the joint best rather than a single winner: an evergreen library
    of 103 topics will often have several equally relevant to the same
    query, and collapsing to one would make the rotation deterministic and
    the channel repetitive — which is the problem the cooldown exists to
    prevent.
    """
    if not terms or not available:
        return available
    scored = [(_demand_score(topic, terms), topic) for topic in available]
    best = max(score for score, _ in scored)
    if best == 0:
        return available
    return [topic for score, topic in scored if score == best]


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
        # Bias the pool toward what people are searching for this week.
        # Evergreen explainers earn search traffic for years where a daily
        # recap is worthless after a day, so which explainer gets made is
        # worth deciding on measured demand rather than a fixed rotation.
        demand = _todays_demand_terms()
        pool_source = _demand_ranked(available, demand)
        if demand and len(pool_source) < len(available):
            logger.info("Search demand narrowed %d eligible topics to %d",
                        len(available), len(pool_source))

        pool: list[dict] = []
        for topic in pool_source:
            weight = weights_map.get(topic.get("estimated_views", "medium"), 1)
            pool.extend([topic] * weight)
        chosen = random.choice(pool)

    mark_used(chosen["id"])
    logger.info("Sunday topic selected: %s (%d of %d eligible)",
                chosen["title"], len(available), len(library["topics"]))
    return chosen


def _todays_demand_terms() -> set:
    """Today's rising search terms, or an empty set — never an exception."""
    try:
        from scrapers.trends_scraper import todays_rising_queries, demand_terms
        return demand_terms(todays_rising_queries(8))
    except Exception as exc:
        logger.warning("Search demand unavailable for topic choice "
                       "(non-fatal): %s", exc)
        return set()


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
        rising = []
        try:
            from scrapers.trends_scraper import todays_rising_queries
            rising = todays_rising_queries(8)
        except Exception as exc:
            logger.debug("Rising queries unavailable for AI pick: %s", exc)
        if rising:
            prompt += (
                "\n\nRISING SEARCHES THIS WEEK: "
                + ", ".join(str(q) for q in rising)
                + "\nPrefer a topic that answers one of these, but only where "
                  "the match is genuine — a mismatched explainer earns the "
                  "click and loses the viewer."
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
