"""
modules/horoscope/content_engine.py
Deterministic, API-free horoscope content generator for all 12 zodiac signs.

Features:
  - Date + sign seeded RNG for reproducible-yet-varied readings
  - Moon phase calculation (pure math, no API)
  - Day-of-week planetary ruler influence
  - Seasonal and monthly themes
  - 5 reading types: daily, weekly, monthly, yearly, special_event
  - Full voiceover scripts (daily ~60w, weekly ~150w, monthly ~300w)

All generation is zero-API-key — pure Python.
"""

from __future__ import annotations

import datetime
import hashlib
import math
import random
from typing import Any

from modules.horoscope.zodiac_data import (
    ZODIAC_SIGNS,
    SIGNS_ORDER,
    SIGNS_BY_NAME,
    PLANETARY_RULERS_BY_WEEKDAY,
    WEEKDAY_NAMES,
    get_sign,
)

# ---------------------------------------------------------------------------
# Moon phase calculation (no API)
# ---------------------------------------------------------------------------

# Known new moon reference: Jan 1, 2000 at 18:14 UTC
_KNOWN_NEW_MOON = datetime.date(2000, 1, 6)
_LUNAR_CYCLE_DAYS = 29.53058867

MOON_PHASES = [
    (0.0,   1.85,  "New Moon",        "🌑", "powerful beginnings, set intentions"),
    (1.85,  7.38,  "Waxing Crescent", "🌒", "take inspired action, build momentum"),
    (7.38,  9.22,  "First Quarter",   "🌓", "push through challenges, decisive energy"),
    (9.22,  14.77, "Waxing Gibbous",  "🌔", "refine plans, heightened productivity"),
    (14.77, 16.61, "Full Moon",       "🌕", "peak energy, release what no longer serves"),
    (16.61, 22.15, "Waning Gibbous",  "🌖", "gratitude, share your gifts with others"),
    (22.15, 23.99, "Last Quarter",    "🌗", "release, forgive, clear the path ahead"),
    (23.99, 29.53, "Waning Crescent", "🌘", "rest, reflect, prepare for rebirth"),
]


def get_moon_phase(date: datetime.date) -> dict:
    """Calculate moon phase for a given date using a simple lunar cycle formula."""
    delta = (date - _KNOWN_NEW_MOON).days
    age = delta % _LUNAR_CYCLE_DAYS
    illumination = (1 - math.cos(2 * math.pi * age / _LUNAR_CYCLE_DAYS)) / 2

    for low, high, name, emoji, meaning in MOON_PHASES:
        if low <= age < high:
            return {
                "name": name,
                "emoji": emoji,
                "age_days": round(age, 1),
                "illumination_pct": round(illumination * 100, 1),
                "meaning": meaning,
            }
    # Edge wrap
    return {
        "name": "Waning Crescent",
        "emoji": "🌘",
        "age_days": round(age, 1),
        "illumination_pct": round(illumination * 100, 1),
        "meaning": "rest, reflect, prepare for rebirth",
    }


# ---------------------------------------------------------------------------
# Seasonal themes
# ---------------------------------------------------------------------------

SEASONAL_THEMES: dict[int, dict] = {
    1:  {"season": "Winter",  "theme": "renewal and fresh starts", "energy": "introspective"},
    2:  {"season": "Winter",  "theme": "dreaming and planning",    "energy": "visionary"},
    3:  {"season": "Spring",  "theme": "emergence and beginnings", "energy": "pioneering"},
    4:  {"season": "Spring",  "theme": "growth and abundance",     "energy": "expansive"},
    5:  {"season": "Spring",  "theme": "blossoming and joy",       "energy": "celebratory"},
    6:  {"season": "Summer",  "theme": "vitality and expression",  "energy": "radiant"},
    7:  {"season": "Summer",  "theme": "deep feeling and home",    "energy": "nurturing"},
    8:  {"season": "Summer",  "theme": "creativity and courage",   "energy": "confident"},
    9:  {"season": "Autumn",  "theme": "harvest and reflection",   "energy": "discerning"},
    10: {"season": "Autumn",  "theme": "balance and transformation","energy": "alchemical"},
    11: {"season": "Autumn",  "theme": "depth and truth-seeking",  "energy": "penetrating"},
    12: {"season": "Winter",  "theme": "completion and wisdom",    "energy": "philosophical"},
}

# ---------------------------------------------------------------------------
# Rich content pools for varied readings
# ---------------------------------------------------------------------------

ENERGY_OPENERS: list[str] = [
    "The cosmos align to amplify your",
    "Celestial currents are flowing toward your",
    "The stars ignite a powerful surge in your",
    "Universal energies are spotlighting your",
    "A cosmic wave of potential surrounds your",
    "The universe is orchestrating a shift in your",
    "Galactic forces are converging around your",
    "The heavens are opening pathways through your",
    "Stellar currents are activating your",
    "Planetary wisdom is flowing into your",
]

LOVE_TEMPLATES: dict[str, list[str]] = {
    "passionate": [
        "Your heart is ablaze — magnetism runs high. Singles may encounter a soul-stirring connection, while couples rediscover the spark that first drew them together.",
        "Venus blesses your romantic life with undeniable intensity. A bold declaration of feelings could change everything. Don't hold back what your heart truly wants to say.",
        "The fire between you and a significant person burns brighter now. Lean into vulnerability — true intimacy emerges when you drop your armor.",
    ],
    "harmonious": [
        "A rare harmony settles over your love life. Misunderstandings dissolve as both hearts choose understanding over being right.",
        "Romantic connections deepen through meaningful conversations. What you share in quiet moments together plants seeds that bloom for years.",
        "Partnership energy is beautifully balanced. Compromise flows naturally, and shared goals feel within reach. This is a time for building, not fighting.",
    ],
    "transformative": [
        "A relationship is calling you to evolve beyond old patterns. What feels like a challenge is actually an invitation to love more authentically.",
        "The romantic chapter you're entering requires honesty — with yourself and others. Releasing what's outgrown makes room for something extraordinary.",
        "Old wounds surface to be healed, not reopened. Approach tender moments with grace, and transformation becomes your greatest love story.",
    ],
    "communicative": [
        "Words carry extra power in love today. A heartfelt conversation could resolve months of tension or deepen a bond beyond measure.",
        "Express your true feelings without editing yourself. The person who's right for you will celebrate your authentic voice.",
        "Clarity in communication unlocks romantic potential. Say what you mean, mean what you say — and watch connections flourish.",
    ],
    "nurturing": [
        "Love asks you to show up with care and presence. Small gestures of tenderness carry more weight than grand gestures right now.",
        "Emotional safety is the foundation of intimacy. Create space where your loved ones feel truly seen and heard.",
        "Your capacity to nurture draws love toward you. The more you give from a full heart, the more returns to you tenfold.",
    ],
}

CAREER_TEMPLATES: dict[str, list[str]] = {
    "leadership": [
        "Your natural authority shines in professional spaces. Step into leadership roles that have been waiting for your vision and courage.",
        "This is your moment to champion an idea or project that carries your signature. Others look to you now — show them what's possible.",
        "A professional opportunity requires you to lead with confidence. Trust that your instincts have been forged by experience.",
    ],
    "innovation": [
        "Fresh ideas arrive like lightning. Document every breakthrough — your most creative insights want to be captured and acted upon.",
        "The professional landscape rewards original thinking right now. Dare to propose the unconventional solution that others have overlooked.",
        "Creative energy surges through your work life. Innovative approaches to old problems attract attention and opportunities.",
    ],
    "collaboration": [
        "Teamwork amplifies individual effort now. Reach out to allies and collaborators — together you'll achieve what none could alone.",
        "Professional relationships deepen through shared goals. Invest time in understanding your colleagues' strengths.",
        "A partnership or collaboration opportunity carries significant potential. Say yes to connecting — synergy opens doors.",
    ],
    "patience": [
        "The seeds you've planted professionally need a little more time. Trust the underground work happening beneath the surface.",
        "Progress feels slower than desired, but cosmic timing protects you from premature moves. Continue with disciplined consistency.",
        "Behind-the-scenes preparation now ensures your public success later. Every detail you tend to compounds into excellence.",
    ],
    "financial": [
        "Financial clarity arrives when you review your resources with fresh eyes. An overlooked opportunity or asset is ready to work harder for you.",
        "Money matters benefit from strategic thinking over emotional reactions. A clear financial plan becomes your greatest tool.",
        "An unexpected income opportunity or professional recognition with financial reward may emerge. Stay open and ready.",
    ],
}

HEALTH_TEMPLATES: dict[str, list[str]] = {
    "vitality": [
        "Life force energy courses through you with unusual strength. Honor this vitality with movement, nourishment, and joyful activity.",
        "Your body is communicating an upsurge of energy. Channel this vitality into physical pursuits that make you feel truly alive.",
        "Robust health energy surrounds you now. This is an excellent time to start fitness goals, nutritional upgrades, or wellness routines.",
    ],
    "restoration": [
        "Your body wisely calls for rest and recovery. Honor this request — genuine restoration fuels your next great surge forward.",
        "Quality sleep and quiet moments aren't indulgences; they're investments. Allow your system to regenerate at its own sacred pace.",
        "Listen to subtle signals from your body. A day of intentional rest could restore energy that weeks of pushing cannot.",
    ],
    "mindful": [
        "Mind-body connection is especially powerful now. Meditation, breathwork, or gentle yoga could unlock unexpected levels of clarity and calm.",
        "Mental wellness deserves the same attention as physical health. Release mental tension through conscious breathing and present-moment awareness.",
        "Your nervous system craves calm. Carve out moments of stillness — they become wellsprings of renewed strength.",
    ],
    "active": [
        "Movement is medicine now. A brisk walk, dance session, or workout clears mental fog and energizes your entire system.",
        "Physical activity unlocks emotional clarity today. Get your body moving and watch insights arrive naturally.",
        "Active pursuits bring double rewards — health benefits plus elevated mood. Find the movement that brings you joy.",
    ],
    "nutritional": [
        "Your body signals what it truly needs — listen with intention. Nourishing foods become fuel for your highest potential.",
        "This is a powerful time to upgrade your nutritional habits. Small, sustainable changes create lasting transformation.",
        "Water, whole foods, and mindful eating amplify your energy and clarity in measurable ways right now.",
    ],
}

SPIRITUAL_MESSAGES: list[str] = [
    "The universe is always conspiring in your favor. Trust the path even when the destination is not yet visible.",
    "Your intuition is your most reliable compass. When logic and gut feeling speak at once — listen to both.",
    "Every experience, even the difficult ones, carries a gift. Ask: what is this teaching me, and how does it serve my growth?",
    "You are exactly where you are meant to be. The timing of your life is divinely orchestrated.",
    "Alignment comes not from forcing, but from releasing. Let go of what controls you and discover what truly guides you.",
    "Your soul knows the answer before your mind forms the question. Get quiet enough to hear its whisper.",
    "Gratitude is a cosmic signal that says 'I am open to receive more.' Amplify your frequency with thankfulness.",
    "The gap between where you are and where you want to be is bridged by daily acts of faith.",
    "You carry ancient wisdom within you. In quiet moments, allow that wisdom to rise and guide your steps.",
    "What you seek is seeking you. Raise your awareness, and discover that the doors are already open.",
]

LUCKY_TIP_TEMPLATES: list[str] = [
    "Wear your lucky color {color} to amplify your energy field today.",
    "The number {number} carries special resonance for you right now — notice where it appears.",
    "Schedule important conversations or decisions on {lucky_day} for optimal outcomes.",
    "Carrying {gemstone} in your pocket or wearing it close to your heart enhances your natural magnetism.",
    "Align your morning intention with your element — {element} energy is your foundation today.",
    "A {color}-colored candle lit during quiet reflection will amplify your desired manifestations.",
    "Begin your day by affirming: '{affirmation}' — these words carry vibrational power.",
    "The number {number} represents your cosmic alignment today — trust moments when it appears.",
    "Channel the wisdom of your ruling planet {planet} through conscious decisions that honor your highest values.",
    "Let {lucky_day} be your day for bold moves — planetary timing supports your ambitions then.",
]

OVERALL_MESSAGES: list[str] = [
    "This period invites you to step into your power with both humility and confidence.",
    "The stars are setting the stage for a meaningful chapter in your ongoing story.",
    "Trust the unfolding — what appears uncertain is actually perfectly timed.",
    "Your greatest growth awaits just beyond your current comfort zone.",
    "Alignment between your inner truth and outer actions creates unstoppable momentum.",
    "The universe rewards those who show up fully, consistently, and authentically.",
    "A powerful wave of opportunity is cresting — be ready to ride it with intention.",
    "Your energy is your signature in the cosmic field. Broadcast it with clarity.",
    "The seeds of transformation you plant now will bear remarkable fruit.",
    "Ancient cosmic cycles are working in your favor — trust the divine timing of your life.",
]

# Monthly themes for horoscope depth
MONTHLY_COSMIC_THEMES: dict[int, str] = {
    1:  "Saturn's discipline meets Jupiter's optimism — structure your visions into actionable blueprints",
    2:  "Neptune's mystical tide amplifies intuition — dreams hold coded messages worth deciphering",
    3:  "Mars ignites springtime ambition — channel restless energy into bold, decisive action",
    4:  "Venus and Jupiter collaborate — expansion through beauty, relationships, and authentic values",
    5:  "Mercury's mental agility peaks — communications, contracts, and learning accelerate dramatically",
    6:  "Solar energy crests — express your authentic self loudly, creatively, and without apology",
    7:  "Lunar depths invite emotional mastery — what you feel reveals what you're ready to release",
    8:  "Leo's solar fire meets Saturn's accountability — creative ambition backed by consistent effort",
    9:  "Mercury retrograde echoes — review, revise, and refine before launching new initiatives",
    10: "Plutonian transformation accelerates — what must die in you so something greater can be born?",
    11: "Jupiter expands horizons — travel, philosophy, and higher learning open unexpected doors",
    12: "Saturn completes its cycle — honor what you've built, release what's expired, prepare for rebirth",
}


# ---------------------------------------------------------------------------
# Seeded RNG helper
# ---------------------------------------------------------------------------

def _make_rng(sign_name: str, date: datetime.date, reading_type: str = "daily") -> random.Random:
    """Create a deterministic RNG seeded by sign + date + type."""
    seed_str = f"{sign_name}:{date.isoformat()}:{reading_type}"
    seed_int = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed_int)


def _pick(rng: random.Random, items: list) -> Any:
    return rng.choice(items)


def _pick_n(rng: random.Random, items: list, n: int) -> list:
    return rng.sample(items, min(n, len(items)))


# ---------------------------------------------------------------------------
# Core reading generators
# ---------------------------------------------------------------------------

def _generate_daily_script(sign: dict, date: datetime.date, rng: random.Random,
                            moon: dict, weekday_planet: str) -> str:
    """Generate a ~60-word voiceover script for a daily reading."""
    opener = _pick(rng, ENERGY_OPENERS)
    element = sign["element"]
    keyword_love = _pick(rng, sign["keywords"]["love"])
    keyword_career = _pick(rng, sign["keywords"]["career"])
    lucky_num = _pick(rng, sign["lucky_numbers"])
    lucky_color = _pick(rng, sign["lucky_colors"])
    affirmation = sign["positive_affirmation"]

    script = (
        f"{sign['name']} — {opener} {element.lower()} essence today. "
        f"With the {moon['name']} casting {moon['meaning']}, your {keyword_love} nature "
        f"shines in love, while your {keyword_career} abilities open professional doors. "
        f"{weekday_planet} energizes your ruling planet {sign['ruling_planet']}, amplifying your gifts. "
        f"Lucky number: {lucky_num}. Lucky color: {lucky_color}. "
        f"Today's affirmation: {affirmation}"
    )
    return script


def _generate_weekly_script(sign: dict, date: datetime.date, rng: random.Random,
                             moon: dict, weekday_planet: str) -> str:
    """Generate a ~150-word voiceover script for a weekly reading."""
    element = sign["element"]
    modality = sign["modality"]
    planet = sign["ruling_planet"]
    love_kw = _pick(rng, sign["keywords"]["love"])
    career_kw = _pick(rng, sign["keywords"]["career"])
    health_kw = _pick(rng, sign["keywords"]["health"])
    lucky_num = _pick(rng, sign["lucky_numbers"])
    lucky_color = _pick(rng, sign["lucky_colors"])
    lucky_day = _pick(rng, sign["lucky_days"])

    love_msg = _pick(rng, LOVE_TEMPLATES.get(love_kw, LOVE_TEMPLATES["harmonious"]))
    career_msg = _pick(rng, CAREER_TEMPLATES.get(career_kw, CAREER_TEMPLATES["innovation"]))
    overall = _pick(rng, OVERALL_MESSAGES)

    script = (
        f"{sign['name']} — This week, the {modality.lower()} power of your {element.lower()} "
        f"nature is activated under {planet}'s guidance. "
        f"The {moon['name']} phase at {moon['illumination_pct']}% illumination sets the tone: "
        f"{moon['meaning']}.\n\n"
        f"In love: {love_msg}\n\n"
        f"In career: {career_msg}\n\n"
        f"For your wellbeing, {health_kw} takes center stage this week. "
        f"Your luckiest day is {lucky_day}. Wear {lucky_color} and watch for the number {lucky_num}. "
        f"\n\n{overall} {sign['positive_affirmation']}"
    )
    return script


def _generate_monthly_script(sign: dict, date: datetime.date, rng: random.Random,
                              moon: dict, weekday_planet: str) -> str:
    """Generate a ~300-word voiceover script for a monthly reading."""
    month_theme = MONTHLY_COSMIC_THEMES.get(date.month, "cosmic cycles bring transformation")
    seasonal = SEASONAL_THEMES.get(date.month, {"theme": "change", "energy": "dynamic"})
    element = sign["element"]
    planet = sign["ruling_planet"]
    archetype = sign["archetype"]

    love_kw = _pick(rng, sign["keywords"]["love"])
    career_kw = _pick(rng, sign["keywords"]["career"])
    health_kw = _pick(rng, sign["keywords"]["health"])
    spirit_kw = _pick(rng, sign["keywords"]["spirituality"])
    lucky_nums = _pick_n(rng, sign["lucky_numbers"], 3)
    lucky_colors = _pick_n(rng, sign["lucky_colors"], 2)
    lucky_days = sign["lucky_days"]

    love_msg = _pick(rng, LOVE_TEMPLATES.get(love_kw, LOVE_TEMPLATES["harmonious"]))
    career_msg = _pick(rng, CAREER_TEMPLATES.get(career_kw, CAREER_TEMPLATES["innovation"]))
    health_msg = _pick(rng, HEALTH_TEMPLATES.get(health_kw, HEALTH_TEMPLATES["vitality"]))
    spirit_msg = _pick(rng, SPIRITUAL_MESSAGES)
    overall = _pick(rng, OVERALL_MESSAGES)

    month_name = date.strftime("%B")

    script = (
        f"{sign['name']} — {archetype}, {month_name} arrives with a clear cosmic mandate: "
        f"{month_theme}.\n\n"
        f"As a {element} sign guided by {planet}, you're uniquely positioned to harness the "
        f"{seasonal['energy']} energy that defines this month's theme of {seasonal['theme']}. "
        f"The lunar cycle throughout {month_name} asks you to be both {love_kw} and {career_kw} — "
        f"twin pillars that support everything you're building.\n\n"
        f"LOVE & RELATIONSHIPS this month: {love_msg} Your compatible signs — "
        f"{', '.join(sign['compatible_signs'][:2])} — offer powerful mirrors and partnerships.\n\n"
        f"CAREER & FINANCES: {career_msg} The spirit of {spirit_kw} infuses your professional path "
        f"with meaning beyond mere accomplishment.\n\n"
        f"HEALTH & WELLBEING: {health_msg} Pay special attention to your {sign['body_part'].lower()}, "
        f"as {planet} activates this area of your body.\n\n"
        f"SPIRITUAL GUIDANCE: {spirit_msg}\n\n"
        f"LUCKY TIMING: Your power days this month are {lucky_days[0]} and "
        f"{lucky_days[1] if len(lucky_days) > 1 else lucky_days[0]}. "
        f"Lucky numbers: {', '.join(str(n) for n in lucky_nums)}. "
        f"Wear {lucky_colors[0]} for abundance and {lucky_colors[1] if len(lucky_colors) > 1 else lucky_colors[0]} "
        f"for clarity.\n\n"
        f"{overall} {sign['positive_affirmation']}"
    )
    return script


def _generate_yearly_script(sign: dict, year: int, rng: random.Random) -> str:
    """Generate a ~480-word voiceover script for a yearly reading."""
    element = sign["element"]
    planet = sign["ruling_planet"]
    archetype = sign["archetype"]
    modality = sign["modality"]
    tarot = sign["tarot_card"]

    love_kws = _pick_n(rng, sign["keywords"]["love"], 2)
    career_kws = _pick_n(rng, sign["keywords"]["career"], 2)
    health_kw = _pick(rng, sign["keywords"]["health"])
    spirit_kws = _pick_n(rng, sign["keywords"]["spirituality"], 2)

    love_msgs = [_pick(rng, LOVE_TEMPLATES.get(kw, LOVE_TEMPLATES["harmonious"])) for kw in love_kws]
    career_msgs = [_pick(rng, CAREER_TEMPLATES.get(kw, CAREER_TEMPLATES["innovation"])) for kw in career_kws]
    health_msg = _pick(rng, HEALTH_TEMPLATES.get(health_kw, HEALTH_TEMPLATES["vitality"]))
    spirit_msg = _pick(rng, SPIRITUAL_MESSAGES)
    overall = _pick(rng, OVERALL_MESSAGES)
    lucky_tip = _pick(rng, LUCKY_TIP_TEMPLATES).format(
        color=sign["lucky_colors"][0],
        number=sign["lucky_numbers"][0],
        lucky_day=sign["lucky_days"][0],
        gemstone=sign["lucky_gemstone"],
        element=element,
        affirmation=sign["positive_affirmation"],
        planet=planet,
    )

    quarterly_themes = [
        f"Q1 (Jan–Mar): Foundation-building and inner renewal. {element} energy asks you to consolidate what you've learned.",
        f"Q2 (Apr–Jun): Emergence and bold action. {planet} supports visible progress and meaningful relationships.",
        f"Q3 (Jul–Sep): Expansion and harvest. Your {modality.lower()} nature finds its fullest expression.",
        f"Q4 (Oct–Dec): Reflection and completion. Wisdom gained becomes the compass for the year ahead.",
    ]

    script = (
        f"{sign['name']} — {archetype}, {year} is a year that will be remembered in your soul's story.\n\n"
        f"Your cosmic blueprint for {year} is shaped by {planet}'s influence, the {element} element's power, "
        f"and your {modality.lower()} nature — a combination that makes {year} a year of {', '.join([love_kws[0], career_kws[0]])}.\n\n"
        f"Your tarot guide for this year is {tarot} — a card that speaks of {sign['archetype'].lower()}'s deepest potential.\n\n"
        f"YEARLY OVERVIEW BY QUARTER:\n"
        + "\n".join(quarterly_themes) + "\n\n"
        f"LOVE & RELATIONSHIPS in {year}: {love_msgs[0]} As the year progresses, {love_msgs[1]} "
        f"Compatible signs {', '.join(sign['compatible_signs'])} will play significant roles in your story.\n\n"
        f"CAREER & FINANCES in {year}: {career_msgs[0]} By mid-year, {career_msgs[1]} "
        f"Your shadow side — {sign['shadow_side']} — calls for conscious awareness so it doesn't dim your shine.\n\n"
        f"HEALTH & WELLBEING in {year}: {health_msg} Your {sign['body_part'].lower()} deserves consistent care "
        f"and intentional nourishment throughout the year.\n\n"
        f"SPIRITUAL PATH in {year}: {spirit_msg} The cosmic invitation is to embody {', '.join(spirit_kws)} "
        f"as lived practices, not just aspirations.\n\n"
        f"LUCKY ACTIVATORS for {year}: {lucky_tip}\n\n"
        f"{overall} {sign['positive_affirmation']}\n\n"
        f"This is your year, {sign['name']}. Step into it fully."
    )
    return script


def _generate_special_event_script(sign: dict, date: datetime.date, rng: random.Random,
                                   event_name: str, event_type: str) -> str:
    """Generate a ~90-word voiceover script for a special astronomical event."""
    element = sign["element"]
    planet = sign["ruling_planet"]
    love_kw = _pick(rng, sign["keywords"]["love"])
    career_kw = _pick(rng, sign["keywords"]["career"])
    spirit_kw = _pick(rng, sign["keywords"]["spirituality"])
    lucky_num = _pick(rng, sign["lucky_numbers"])

    event_influences = {
        "retrograde": f"Mercury retrograde asks you to review, revisit, and refine — especially around {career_kw}.",
        "full_moon": f"This Full Moon illuminates your deepest feelings about {love_kw}, bringing clarity and release.",
        "new_moon": f"The New Moon seeds powerful intentions around {spirit_kw}, planting cosmic roots for 6 months ahead.",
        "eclipse": f"This eclipse accelerates your evolution — changes happening now are divinely timed and irreversible.",
        "solstice": f"The solstice resets your energy field — what you choose to focus on now carries magnified power.",
        "equinox": f"The equinox brings cosmic balance — align your {element.lower()} nature with this harmonic moment.",
    }

    influence = event_influences.get(event_type, f"This celestial event amplifies your {element.lower()} energy significantly.")
    spirit_msg = _pick(rng, SPIRITUAL_MESSAGES)

    script = (
        f"{sign['name']} — The {event_name} activates your chart in profound ways. "
        f"{influence} Your ruling planet {planet} receives this energy and channels it into your life. "
        f"In love and connection, your {love_kw} qualities become magnetic. "
        f"Professionally, {career_kw} energy surges — act on inspiration quickly. "
        f"{spirit_msg} "
        f"Lucky number {lucky_num} acts as your cosmic anchor through this period. "
        f"{sign['positive_affirmation']}"
    )
    return script


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_sign(
    sign_name: str,
    reading_type: str,
    date: datetime.date | None = None,
    event_name: str = "",
    event_type: str = "",
) -> dict:
    """
    Generate a horoscope reading for a single sign.

    Args:
        sign_name:    One of the 12 zodiac sign names (case-insensitive).
        reading_type: 'daily', 'weekly', 'monthly', 'yearly', 'special_event'
        date:         Target date (defaults to today).
        event_name:   Used for special_event type (e.g. "Full Moon in Scorpio").
        event_type:   Used for special_event type (e.g. "full_moon", "retrograde").

    Returns:
        dict with keys: sign, symbol, title, intro, love, career, health,
                        lucky_number, lucky_color, lucky_tip, overall_message,
                        affirmation, moon_phase, script_text
    """
    if date is None:
        date = datetime.date.today()

    sign = get_sign(sign_name)
    rng = _make_rng(sign_name, date, reading_type)
    moon = get_moon_phase(date)

    weekday = date.weekday()  # 0=Monday
    weekday_planet = PLANETARY_RULERS_BY_WEEKDAY[weekday % 7]
    weekday_name = WEEKDAY_NAMES[weekday % 7]

    # Select content pools
    love_kw = _pick(rng, sign["keywords"]["love"])
    career_kw = _pick(rng, sign["keywords"]["career"])
    health_kw = _pick(rng, sign["keywords"]["health"])
    lucky_num = _pick(rng, sign["lucky_numbers"])
    lucky_color = _pick(rng, sign["lucky_colors"])

    love_text = _pick(rng, LOVE_TEMPLATES.get(love_kw, LOVE_TEMPLATES["harmonious"]))
    career_text = _pick(rng, CAREER_TEMPLATES.get(career_kw, CAREER_TEMPLATES["innovation"]))
    health_text = _pick(rng, HEALTH_TEMPLATES.get(health_kw, HEALTH_TEMPLATES["vitality"]))
    overall_msg = _pick(rng, OVERALL_MESSAGES)
    spirit_msg = _pick(rng, SPIRITUAL_MESSAGES)

    lucky_tip = _pick(rng, LUCKY_TIP_TEMPLATES).format(
        color=lucky_color,
        number=lucky_num,
        lucky_day=_pick(rng, sign["lucky_days"]),
        gemstone=sign["lucky_gemstone"],
        element=sign["element"],
        affirmation=sign["positive_affirmation"],
        planet=sign["ruling_planet"],
    )

    # Generate script by type
    if reading_type == "daily":
        script_text = _generate_daily_script(sign, date, rng, moon, weekday_planet)
        title = f"{sign['name']} Daily Horoscope — {date.strftime('%B %d, %Y')}"
        intro = (
            f"Welcome, {sign['name']} {sign['symbol']}. Today is {weekday_name}, guided by {weekday_planet}. "
            f"The {moon['name']} {moon['emoji']} illuminates your path, inviting you to embrace {moon['meaning']}."
        )

    elif reading_type == "weekly":
        script_text = _generate_weekly_script(sign, date, rng, moon, weekday_planet)
        week_end = date + datetime.timedelta(days=6)
        title = f"{sign['name']} Weekly Horoscope — {date.strftime('%B %d')}–{week_end.strftime('%d, %Y')}"
        intro = (
            f"Greetings, {sign['name']} {sign['symbol']}. This week opens under the {moon['name']} {moon['emoji']}, "
            f"with {moon['meaning']} as your overarching theme. {sign['ruling_planet']} is guiding your journey."
        )

    elif reading_type == "monthly":
        script_text = _generate_monthly_script(sign, date, rng, moon, weekday_planet)
        title = f"{sign['name']} Monthly Horoscope — {date.strftime('%B %Y')}"
        month_theme = SEASONAL_THEMES.get(date.month, {}).get("theme", "transformation")
        intro = (
            f"Welcome to your {date.strftime('%B')} horoscope, {sign['name']} {sign['symbol']}. "
            f"This month's cosmic theme is {month_theme}. As a {sign['element']} sign, your natural gifts "
            f"are perfectly aligned with what the universe is asking of you."
        )

    elif reading_type == "yearly":
        script_text = _generate_yearly_script(sign, date.year, rng)
        title = f"{sign['name']} Yearly Horoscope {date.year} — Complete Reading"
        intro = (
            f"Welcome to your {date.year} yearly forecast, {sign['name']} {sign['symbol']}. "
            f"The year ahead is shaped by your {sign['element']} nature and {sign['ruling_planet']}'s influence. "
            f"As {sign['archetype']}, you are ready for what this year brings."
        )

    elif reading_type == "special_event":
        ev_name = event_name or "Celestial Event"
        ev_type = event_type or "eclipse"
        script_text = _generate_special_event_script(sign, date, rng, ev_name, ev_type)
        title = f"{sign['name']} — {ev_name} {date.year} Horoscope"
        intro = (
            f"{sign['name']} {sign['symbol']}, the {ev_name} is creating powerful ripples in your chart. "
            f"Your {sign['element']} nature is being called to respond with {sign['archetype'].lower()} wisdom."
        )

    else:
        raise ValueError(f"Unknown reading_type: {reading_type!r}. Use: daily, weekly, monthly, yearly, special_event")

    seasonal = SEASONAL_THEMES.get(date.month, {"theme": "transformation", "energy": "dynamic"})

    return {
        "sign": sign["name"],
        "symbol": sign["symbol"],
        "element": sign["element"],
        "modality": sign["modality"],
        "ruling_planet": sign["ruling_planet"],
        "house": sign["house"],
        "reading_type": reading_type,
        "date": date.isoformat(),
        "title": title,
        "intro": intro,
        "love": love_text,
        "career": career_text,
        "health": health_text,
        "spiritual": spirit_msg,
        "lucky_number": lucky_num,
        "lucky_color": lucky_color,
        "lucky_tip": lucky_tip,
        "overall_message": overall_msg,
        "affirmation": sign["positive_affirmation"],
        "moon_phase": moon,
        "weekday_planet": weekday_planet,
        "seasonal_theme": seasonal["theme"],
        "seasonal_energy": seasonal["energy"],
        "script_text": script_text,
    }


def generate_all_signs(
    reading_type: str,
    date: datetime.date | None = None,
    event_name: str = "",
    event_type: str = "",
) -> list[dict]:
    """
    Generate horoscope readings for all 12 zodiac signs.

    Returns:
        List of 12 reading dicts in canonical zodiac order (Aries first).
    """
    if date is None:
        date = datetime.date.today()

    return [
        generate_sign(sign_name, reading_type, date, event_name, event_type)
        for sign_name in SIGNS_ORDER
    ]


def get_video_title(
    reading_type: str,
    date: datetime.date | None = None,
    event_name: str = "",
) -> str:
    """Return a YouTube-optimized video title string."""
    if date is None:
        date = datetime.date.today()

    month = date.strftime("%B")
    day = date.strftime("%d").lstrip("0")
    year = date.year
    week_end = date + datetime.timedelta(days=6)
    end_day = week_end.strftime("%d").lstrip("0")

    titles = {
        "daily": f"Daily Horoscope {month} {day}, {year} — All 12 Signs ✨",
        "weekly": f"Weekly Horoscope {month} {day}–{end_day}, {year} — All Signs 🌙",
        "monthly": f"Monthly Horoscope {month} {year} — All 12 Signs 🌟",
        "yearly": f"Yearly Horoscope {year} — All 12 Zodiac Signs Complete Reading 🔮",
        "special_event": f"{event_name or 'Celestial Event'} Horoscope {year} — How It Affects Your Sign 🌕",
    }
    return titles.get(reading_type, f"Horoscope {date.isoformat()} ✨")


def get_video_description(
    reading_type: str,
    date: datetime.date | None = None,
    readings: list[dict] | None = None,
    event_name: str = "",
) -> str:
    """
    Build a full YouTube video description with timestamps/chapters for each sign.

    Chapters follow the pattern:
      0:00 Intro
      0:30 Aries ♈
      1:30 Taurus ♉
      ...
    """
    if date is None:
        date = datetime.date.today()

    # Duration per sign by type (seconds)
    durations = {
        "daily": 60,
        "weekly": 120,
        "monthly": 240,
        "yearly": 480,
        "special_event": 90,
    }
    dur = durations.get(reading_type, 60)
    intro_dur = 30

    # Build chapters
    chapters_lines = ["0:00 🌟 Introduction & Cosmic Overview"]
    current_seconds = intro_dur

    for sign_data in (readings or [{"sign": s, "symbol": SIGNS_BY_NAME[s]["symbol"]} for s in SIGNS_ORDER]):
        sign_name = sign_data.get("sign", "Unknown")
        symbol = sign_data.get("symbol", "")
        minutes = current_seconds // 60
        seconds = current_seconds % 60
        ts = f"{minutes}:{seconds:02d}"
        chapters_lines.append(f"{ts} {symbol} {sign_name}")
        current_seconds += dur

    outro_ts = f"{current_seconds // 60}:{current_seconds % 60:02d}"
    chapters_lines.append(f"{outro_ts} 🙏 Outro — Subscribe for Daily Cosmic Guidance")

    moon = get_moon_phase(date)

    type_descriptions = {
        "daily":         f"Your complete daily horoscope for {date.strftime('%B %d, %Y')} — all 12 zodiac signs.",
        "weekly":        f"Your weekly cosmic forecast for {date.strftime('%B %d, %Y')} — every sign covered.",
        "monthly":       f"Complete monthly horoscope for {date.strftime('%B %Y')} — all 12 signs with detailed guidance.",
        "yearly":        f"Full yearly horoscope for {date.year} — in-depth forecasts for every zodiac sign.",
        "special_event": f"How the {event_name or 'celestial event'} affects every zodiac sign — complete astrology guide.",
    }

    desc_intro = type_descriptions.get(reading_type, "Horoscope for all 12 zodiac signs.")

    hashtags_map = {
        "daily":         "#DailyHoroscope #Horoscope #Astrology #ZodiacSigns #GetMindFuelNow",
        "weekly":        "#WeeklyHoroscope #WeeklyAstrology #Horoscope #ZodiacForecast #GetMindFuelNow",
        "monthly":       "#MonthlyHoroscope #MonthlyAstrology #Zodiac #AstrologyForecast #GetMindFuelNow",
        "yearly":        "#YearlyHoroscope #AnnualHoroscope #2026Astrology #ZodiacPredictions #GetMindFuelNow",
        "special_event": "#Astrology #FullMoon #MercuryRetrograde #Horoscope #GetMindFuelNow",
    }

    description = f"""{desc_intro}

🌙 Moon Phase: {moon['name']} {moon['emoji']} — {moon['meaning'].capitalize()}

━━━━━━━━━━━━━━━━━━━━━
⏱️ VIDEO CHAPTERS
━━━━━━━━━━━━━━━━━━━━━
{chr(10).join(chapters_lines)}

━━━━━━━━━━━━━━━━━━━━━
✨ ABOUT GET MIND FUEL NOW
━━━━━━━━━━━━━━━━━━━━━
Welcome to GetMindFuelNow — your daily source of cosmic intelligence, astrology insights, and zodiac wisdom. Every day we decode the stars so you can live with greater purpose, clarity, and alignment.

🔔 SUBSCRIBE & hit the bell for daily horoscopes!
👍 LIKE this video if the cosmos spoke to you
💬 COMMENT your sign below — how accurate was your reading?

━━━━━━━━━━━━━━━━━━━━━
🔗 FOLLOW US
━━━━━━━━━━━━━━━━━━━━━
• YouTube: GetMindFuelNow
• New videos every day at 7 PM UTC

━━━━━━━━━━━━━━━━━━━━━
⚠️ DISCLAIMER
━━━━━━━━━━━━━━━━━━━━━
Horoscopes are for entertainment and spiritual guidance purposes only. Always trust your own intuition and judgment for major life decisions.

{hashtags_map.get(reading_type, "#Horoscope #Astrology #GetMindFuelNow")}
"""
    return description.strip()


def get_tags(reading_type: str) -> list[str]:
    """Return a comprehensive list of YouTube tags for a given reading type."""
    base_tags = [
        "horoscope", "astrology", "zodiac", "zodiac signs", "star signs",
        "astrology reading", "cosmic energy", "planetary alignment",
        "aries horoscope", "taurus horoscope", "gemini horoscope",
        "cancer horoscope", "leo horoscope", "virgo horoscope",
        "libra horoscope", "scorpio horoscope", "sagittarius horoscope",
        "capricorn horoscope", "aquarius horoscope", "pisces horoscope",
        "all 12 signs", "all zodiac signs", "GetMindFuelNow",
        "mercury retrograde", "moon phases", "full moon", "new moon",
        "saturn transit", "jupiter transit", "venus", "mars",
        "astrology 2026", "horoscope 2026",
    ]

    type_tags = {
        "daily": [
            "daily horoscope", "horoscope today", "today's horoscope",
            "daily astrology", "daily reading", "daily forecast",
            "horoscope for today", "today astrology", "astrology today",
        ],
        "weekly": [
            "weekly horoscope", "weekly astrology", "weekly forecast",
            "this week horoscope", "week horoscope", "weekly reading",
            "astrology this week", "weekly zodiac", "week ahead forecast",
        ],
        "monthly": [
            "monthly horoscope", "monthly astrology", "monthly forecast",
            "horoscope this month", "monthly reading", "monthly zodiac",
            "astrology this month", "monthly predictions",
        ],
        "yearly": [
            "yearly horoscope", "annual horoscope", "2026 horoscope",
            "year ahead astrology", "yearly forecast", "annual forecast",
            "2026 predictions", "year horoscope", "2026 astrology",
            "yearly reading", "horoscope 2026 all signs",
        ],
        "special_event": [
            "mercury retrograde", "full moon horoscope", "new moon horoscope",
            "lunar eclipse", "solar eclipse", "eclipse horoscope",
            "retrograde horoscope", "full moon reading", "new moon reading",
            "astronomical event astrology", "special astrology event",
        ],
    }

    return base_tags + type_tags.get(reading_type, [])


def build_full_voiceover_script(
    readings: list[dict],
    reading_type: str,
    date: datetime.date | None = None,
    event_name: str = "",
) -> str:
    """
    Combine all 12 sign scripts into one unified narration for the entire video.

    Format:
      [INTRO]
      ARIES — [reading]
      TAURUS — [reading]
      ...
      PISCES — [reading]
      [OUTRO]
    """
    if date is None:
        date = datetime.date.today()

    type_labels = {
        "daily": f"daily horoscope for {date.strftime('%B %d, %Y')}",
        "weekly": f"weekly horoscope for the week of {date.strftime('%B %d, %Y')}",
        "monthly": f"monthly horoscope for {date.strftime('%B %Y')}",
        "yearly": f"yearly horoscope for {date.year}",
        "special_event": f"{event_name or 'special celestial event'} horoscope",
    }
    label = type_labels.get(reading_type, f"horoscope for {date.isoformat()}")
    moon = get_moon_phase(date)

    intro = (
        f"Welcome to your {label} from GetMindFuelNow. "
        f"Tonight's {moon['name']} {moon['emoji']} is setting the cosmic stage: {moon['meaning']}. "
        f"Let's discover what the stars have in store for each sign. Stay with us for your complete reading.\n\n"
    )

    sign_sections = []
    for r in readings:
        section = f"{r['sign'].upper()} {r['symbol']}\n{r['script_text']}"
        sign_sections.append(section)

    outro = (
        "\n\nThank you for watching GetMindFuelNow. "
        "If the cosmos spoke to you today, please like this video and subscribe for your daily cosmic guidance. "
        "Drop your sign in the comments — we love hearing from you. "
        "Until next time, trust the stars, trust yourself, and keep your energy aligned. Namaste."
    )

    return intro + "\n\n".join(sign_sections) + outro
