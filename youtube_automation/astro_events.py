#!/usr/bin/env python3
"""
astro_events.py
Detects REAL astronomical events (computed with the Swiss Ephemeris, not
scraped or guessed) so the daily topic video can ride US search spikes on
the exact right day: full/new moons, solar & lunar eclipses, Mercury/Venus/
Mars retrograde stations, and sun sign-ingresses ("Leo season starts").

US-audience conventions, deliberately:
- Signs here are TROPICAL (mainstream US/Western astrology), unlike
  astro_chart.py's sidereal/Lahiri (used for the Vedic-style sports charts).
  A US viewer searching "full moon in capricorn tonight" means the tropical
  sign, so event titles must match that.
- "Today" is the US-Eastern calendar day, not UTC: a full moon at 02:00 UTC
  is the *previous evening* in the US, and the video must say "tonight" on
  the day Americans experience it. The day window helper below converts an
  ET day to its UTC julian-day span (zoneinfo, with a fixed UTC-5 fallback).

Usage:
  python3 astro_events.py 20260710            # event (if any) for that ET day
  python3 astro_events.py 20260710 --next 60  # list events in the next N days
"""
import sys
from datetime import datetime, timedelta, timezone

import swisseph as swe

SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]

_STATION_PLANETS = [("Mercury", swe.MERCURY), ("Venus", swe.VENUS), ("Mars", swe.MARS)]


def _jd(dt_utc: datetime) -> float:
    return swe.julday(dt_utc.year, dt_utc.month, dt_utc.day,
                      dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600)


def _et_day_window_jd(date_tag: str) -> tuple:
    """(jd_start, jd_end) of the US-Eastern calendar day, in UT julian days."""
    d = datetime.strptime(date_tag, "%Y%m%d")
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        start = datetime(d.year, d.month, d.day, tzinfo=et).astimezone(timezone.utc)
        end = (datetime(d.year, d.month, d.day, tzinfo=et) + timedelta(days=1)).astimezone(timezone.utc)
        start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    except Exception:
        start = d + timedelta(hours=5)          # EST fallback (UTC-5)
        end = start + timedelta(days=1)
    return _jd(start), _jd(end)


def _tropical_lon(jd: float, planet: int) -> float:
    return swe.calc_ut(jd, planet)[0][0] % 360


def _tropical_speed(jd: float, planet: int) -> float:
    return swe.calc_ut(jd, planet)[0][3]


def _sign_of(lon: float) -> str:
    return SIGNS[int(lon // 30) % 12]


def _elongation(jd: float) -> float:
    return (_tropical_lon(jd, swe.MOON) - _tropical_lon(jd, swe.SUN)) % 360


def _phase_crossing_in(jd0: float, jd1: float, target: float):
    """jd of the moment the sun-moon elongation crosses `target` degrees
    inside [jd0, jd1), or None. Elongation increases monotonically ~12°/day,
    wrapping 360→0 at the new moon, so an hourly scan can't miss a crossing."""
    step = 1 / 24
    t, e_prev = jd0, _elongation(jd0)
    while t + step <= jd1 + 1e-9:
        t2 = t + step
        e2 = _elongation(t2)
        seg = (e2 - e_prev) % 360           # forward motion this hour (<1°)
        gap = (target - e_prev) % 360       # forward distance to target
        if gap < seg:                        # target crossed within this hour
            return t + step * (gap / seg if seg else 0)
        t, e_prev = t2, e2
    return None


def _eclipse_in(jd0: float, jd1: float, solar: bool):
    """(jd_max, type_str) if a solar/lunar eclipse peaks inside [jd0, jd1)."""
    try:
        if solar:
            res, tret = swe.sol_eclipse_when_glob(jd0 - 0.5)
        else:
            res, tret = swe.lun_eclipse_when(jd0 - 0.5)
    except Exception:
        return None
    jd_max = tret[0]
    if not (jd0 <= jd_max < jd1):
        return None
    if res & swe.ECL_TOTAL:
        kind = "Total"
    elif solar and (res & swe.ECL_ANNULAR):
        kind = "Annular"
    elif not solar and (res & getattr(swe, "ECL_PENUMBRAL", 0)):
        kind = "Penumbral"
    else:
        kind = "Partial"
    return jd_max, kind


def _station_in(jd0: float, jd1: float, planet: int):
    """'retrograde'/'direct' if the planet's motion flips sign in the window."""
    s0, s1 = _tropical_speed(jd0, planet), _tropical_speed(jd1, planet)
    if s0 > 0 > s1:
        return "retrograde"
    if s0 < 0 < s1:
        return "direct"
    return None


def _sun_ingress_in(jd0: float, jd1: float):
    """New tropical sun sign if the sun changes sign in the window."""
    a, b = _sign_of(_tropical_lon(jd0, swe.SUN)), _sign_of(_tropical_lon(jd1, swe.SUN))
    return b if a != b else None


def event_for(date_tag: str):
    """The single highest-priority real astro event on this US-Eastern day,
    as {kind, title, angle, category}, or None on a quiet day. Titles are
    written the way Americans actually search these events."""
    jd0, jd1 = _et_day_window_jd(date_tag)

    ev = _eclipse_in(jd0, jd1, solar=True)
    if ev:
        jd_max, kind = ev
        sign = _sign_of(_tropical_lon(jd_max, swe.SUN))
        return {"kind": "solar_eclipse",
                "title": f"{kind} Solar Eclipse Today: What the Eclipse in {sign} Means for Every Zodiac Sign",
                "angle": (f"A real {kind.lower()} solar eclipse peaks today with the Sun in {sign} "
                          f"(tropical). Explain what solar eclipses mean in astrology — fated new "
                          f"beginnings, resets — and give every sign a short takeaway for eclipse season."),
                "category": "lunar"}

    ev = _eclipse_in(jd0, jd1, solar=False)
    if ev:
        jd_max, kind = ev
        sign = _sign_of(_tropical_lon(jd_max, swe.MOON))
        return {"kind": "lunar_eclipse",
                "title": f"{kind} Lunar Eclipse Tonight: The Full Moon Eclipse in {sign} Explained",
                "angle": (f"A real {kind.lower()} lunar eclipse peaks tonight with the Moon in {sign} "
                          f"(tropical). Explain what lunar eclipses mean — culminations, dramatic "
                          f"release — and what tonight's eclipse stirs up for every zodiac sign."),
                "category": "lunar"}

    for name, planet in _STATION_PLANETS:
        st = _station_in(jd0, jd1, planet)
        if st == "retrograde":
            sign = _sign_of(_tropical_lon(jd1, planet))
            return {"kind": f"{name.lower()}_retrograde",
                    "title": f"{name} Retrograde Starts Today in {sign}: Survival Guide for Every Sign",
                    "angle": (f"{name} stations retrograde today in {sign} (tropical) — a real, "
                              f"computed event. Explain what this retrograde affects, the classic "
                              f"do's and don'ts, dates, and a short survival tip for each sign."),
                    "category": "planets"}
        if st == "direct":
            sign = _sign_of(_tropical_lon(jd1, planet))
            return {"kind": f"{name.lower()}_direct",
                    "title": f"{name} Goes Direct Today: The {sign} Fog Finally Lifts for Every Sign",
                    "angle": (f"{name} stations direct today in {sign} (tropical). Explain the "
                              f"post-retrograde shift, what starts flowing again, and what each "
                              f"sign should (and shouldn't) rush into this week."),
                    "category": "planets"}

    t = _phase_crossing_in(jd0, jd1, 180)
    if t:
        sign = _sign_of(_tropical_lon(t, swe.MOON))
        return {"kind": "full_moon",
                "title": f"Tonight's Full Moon in {sign}: What It Means for Every Zodiac Sign",
                "angle": (f"A real full moon peaks tonight in {sign} (tropical). Explain this full "
                          f"moon's themes, its emotional peak-and-release energy, simple rituals, "
                          f"and a short specific takeaway for each of the 12 signs."),
                "category": "lunar"}

    t = _phase_crossing_in(jd0, jd1, 0)
    if t:
        sign = _sign_of(_tropical_lon(t, swe.MOON))
        return {"kind": "new_moon",
                "title": f"New Moon in {sign} Today: The Perfect Reset for Your Zodiac Sign",
                "angle": (f"A real new moon lands today in {sign} (tropical). Explain new-moon "
                          f"intention-setting, this {sign} new moon's themes, and one fresh-start "
                          f"focus for every zodiac sign."),
                "category": "lunar"}

    sign = _sun_ingress_in(jd0, jd1)
    if sign:
        return {"kind": "sun_ingress",
                "title": f"{sign} Season Starts Today: What the Sun in {sign} Means for Every Sign",
                "angle": (f"The Sun enters {sign} (tropical) today, starting {sign} season. Explain "
                          f"the season's collective vibe and what the next month emphasizes for "
                          f"each zodiac sign."),
                "category": "signs"}

    return None


def upcoming(date_tag: str, days: int = 30) -> list:
    """[(YYYYMMDD, event), ...] for the next `days` ET days — preview/testing."""
    d = datetime.strptime(date_tag, "%Y%m%d")
    out = []
    for i in range(days):
        tag = (d + timedelta(days=i)).strftime("%Y%m%d")
        ev = event_for(tag)
        if ev:
            out.append((tag, ev))
    return out


def main():
    date_tag = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    if "--next" in sys.argv:
        days = int(sys.argv[sys.argv.index("--next") + 1])
        events = upcoming(date_tag, days)
        print(f"{len(events)} event day(s) in the next {days} days:")
        for tag, ev in events:
            print(f"  {tag}  [{ev['kind']}]  {ev['title']}")
    else:
        ev = event_for(date_tag)
        print(f"{date_tag}: " + (f"[{ev['kind']}] {ev['title']}" if ev else "no major astro event"))


if __name__ == "__main__":
    main()
