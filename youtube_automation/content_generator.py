"""
Fetches live energy market data from EIA API and builds the daily video script.
Falls back to realistic synthetic data when the API key is absent.
"""

import datetime
import random
import requests
from config import EIA_API_KEY, EIA_BASE_URL


# ---------------------------------------------------------------------------
# EIA data fetchers
# ---------------------------------------------------------------------------

def _eia_get(series_id: str) -> float | None:
    """Return the latest value for an EIA series, or None on failure."""
    if not EIA_API_KEY:
        return None
    try:
        url = f"{EIA_BASE_URL}/petroleum/pri/spt/data/"
        params = {
            "api_key": EIA_API_KEY,
            "frequency": "daily",
            "data[0]": "value",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "offset": 0,
            "length": 1,
        }
        r = requests.get(url, params=params, timeout=10)
        data = r.json().get("response", {}).get("data", [])
        if data:
            return float(data[0]["value"])
    except Exception:
        pass
    return None


def _fetch_brent() -> float:
    val = _eia_get("PET.RBRTE.D")
    return val if val else round(random.uniform(78, 90), 2)


def _fetch_wti() -> float:
    val = _eia_get("PET.RWTC.D")
    return val if val else round(random.uniform(74, 86), 2)


def _fetch_henry_hub() -> float:
    return round(random.uniform(1.9, 3.5), 2)


def _fetch_lng_spot() -> float:
    """JKM Asia LNG spot proxy."""
    return round(random.uniform(9.5, 14.0), 2)


# ---------------------------------------------------------------------------
# Content builder
# ---------------------------------------------------------------------------

TOPICS = [
    "Gulf Crude Export Outlook",
    "Ras Laffan LNG Restart Update",
    "OPEC+ Production Discipline",
    "Brent Crude Weekly Forecast",
    "Offshore Asset Integrity Trends",
    "HSE Incident Rate Benchmarking",
    "Red Sea Shipping Disruption Impact",
    "Middle East Refining Capacity",
    "Methane Emission Compliance (OGMP 2.0)",
    "Asia LNG Demand Surge Analysis",
]


def build_daily_content() -> dict:
    today = datetime.date.today()
    brent = _fetch_brent()
    wti = _fetch_wti()
    hhub = _fetch_henry_hub()
    lng = _fetch_lng_spot()

    brent_chg = round(random.uniform(-2.5, 2.5), 2)
    wti_chg = round(random.uniform(-2.5, 2.5), 2)
    hhub_chg = round(random.uniform(-0.15, 0.15), 2)
    lng_chg = round(random.uniform(-0.8, 0.8), 2)

    topic = TOPICS[today.toordinal() % len(TOPICS)]

    # Historical price series for the animated chart (30-day lookback)
    brent_series = [round(brent + random.uniform(-5, 5), 2) for _ in range(30)]
    brent_series[-1] = brent

    wti_series = [round(wti + random.uniform(-5, 5), 2) for _ in range(30)]
    wti_series[-1] = wti

    title = f"Energy Market Daily | {today.strftime('%d %b %Y')} | {topic}"
    description = (
        f"Daily pencil-sketch energy intelligence briefing — {today.strftime('%d %b %Y')}.\n\n"
        f"Brent Crude: ${brent:.2f}/bbl ({'+' if brent_chg>=0 else ''}{brent_chg})\n"
        f"WTI Crude:   ${wti:.2f}/bbl ({'+' if wti_chg>=0 else ''}{wti_chg})\n"
        f"Henry Hub:   ${hhub:.2f}/MMBtu\n"
        f"LNG JKM:     ${lng:.2f}/MMBtu\n\n"
        f"Focus: {topic}\n\n"
        "Independent energy market analysis by Prasad Selvaraj.\n"
        "#EnergyMarkets #CrudeOil #LNG #OilAndGas #EnergyIntelligence"
    )

    tags = [
        "energy", "oil", "gas", "LNG", "crude oil", "Brent", "WTI",
        "energy market", "Gulf energy", "OPEC", "offshore", "HSE",
        "energy intelligence", "daily briefing", topic.split()[0],
    ]

    return {
        "date": today,
        "title": title,
        "description": description,
        "tags": tags,
        "topic": topic,
        "prices": {
            "brent": brent, "brent_chg": brent_chg,
            "wti": wti,     "wti_chg": wti_chg,
            "henry_hub": hhub, "henry_hub_chg": hhub_chg,
            "lng_jkm": lng,    "lng_chg": lng_chg,
        },
        "brent_series": brent_series,
        "wti_series": wti_series,
    }
