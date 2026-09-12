"""
modules/horoscope/zodiac_data.py
Complete zodiac data for all 12 signs.
No external API required — pure static data.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Core zodiac data
# ---------------------------------------------------------------------------

ZODIAC_SIGNS: list[dict[str, Any]] = [
    {
        "name": "Aries",
        "symbol": "♈",
        "unicode_symbol": "♈",
        "dates": "March 21 – April 19",
        "date_range": (3, 21, 4, 19),   # (start_month, start_day, end_month, end_day)
        "element": "Fire",
        "modality": "Cardinal",
        "ruling_planet": "Mars",
        "house": 1,
        "traits": [
            "bold", "ambitious", "pioneering", "courageous",
            "energetic", "enthusiastic", "impulsive", "competitive",
        ],
        "life_areas": ["self-expression", "new beginnings", "identity", "leadership"],
        "compatible_signs": ["Leo", "Sagittarius", "Gemini", "Aquarius"],
        "incompatible_signs": ["Cancer", "Capricorn"],
        "lucky_numbers": [1, 9, 17, 26, 41],
        "lucky_colors": ["Red", "Scarlet", "Orange-Red"],
        "lucky_days": ["Tuesday", "Saturday"],
        "lucky_gemstone": "Diamond",
        "keywords": {
            "love": ["passionate", "direct", "adventurous", "spontaneous", "magnetic"],
            "career": ["leadership", "entrepreneurship", "initiative", "competition", "innovation"],
            "health": ["vitality", "physical activity", "headaches", "adrenal health", "energy"],
            "spirituality": ["warrior spirit", "divine courage", "solar activation", "rebirth"],
        },
        "body_part": "Head, Face, Brain",
        "tarot_card": "The Emperor",
        "metal": "Iron",
        "day_phrase": "I am",
        "archetype": "The Warrior",
        "shadow_side": "recklessness, selfishness, aggression",
        "positive_affirmation": "I boldly pioneer my path with courage and clarity.",
    },
    {
        "name": "Taurus",
        "symbol": "♉",
        "unicode_symbol": "♉",
        "dates": "April 20 – May 20",
        "date_range": (4, 20, 5, 20),
        "element": "Earth",
        "modality": "Fixed",
        "ruling_planet": "Venus",
        "house": 2,
        "traits": [
            "reliable", "patient", "practical", "devoted",
            "stubborn", "sensual", "determined", "grounded",
        ],
        "life_areas": ["material security", "pleasure", "values", "resources"],
        "compatible_signs": ["Virgo", "Capricorn", "Cancer", "Pisces"],
        "incompatible_signs": ["Leo", "Aquarius"],
        "lucky_numbers": [2, 6, 9, 12, 24],
        "lucky_colors": ["Green", "Pink", "Earthy Brown"],
        "lucky_days": ["Friday", "Monday"],
        "lucky_gemstone": "Emerald",
        "keywords": {
            "love": ["loyal", "sensual", "steady", "nurturing", "romantic"],
            "career": ["financial acumen", "persistence", "craftsmanship", "reliability", "luxury"],
            "health": ["throat", "neck", "thyroid", "indulgence", "stability"],
            "spirituality": ["earth wisdom", "sacred beauty", "grounding rituals", "abundance"],
        },
        "body_part": "Throat, Neck, Thyroid",
        "tarot_card": "The Hierophant",
        "metal": "Copper",
        "day_phrase": "I have",
        "archetype": "The Builder",
        "shadow_side": "possessiveness, stubbornness, materialism",
        "positive_affirmation": "I am grounded in abundance and open to life's pleasures.",
    },
    {
        "name": "Gemini",
        "symbol": "♊",
        "unicode_symbol": "♊",
        "dates": "May 21 – June 20",
        "date_range": (5, 21, 6, 20),
        "element": "Air",
        "modality": "Mutable",
        "ruling_planet": "Mercury",
        "house": 3,
        "traits": [
            "versatile", "expressive", "curious", "witty",
            "adaptable", "intellectual", "sociable", "restless",
        ],
        "life_areas": ["communication", "learning", "siblings", "short journeys"],
        "compatible_signs": ["Libra", "Aquarius", "Aries", "Leo"],
        "incompatible_signs": ["Virgo", "Pisces"],
        "lucky_numbers": [3, 5, 7, 11, 14],
        "lucky_colors": ["Yellow", "Silver", "Light Blue"],
        "lucky_days": ["Wednesday", "Sunday"],
        "lucky_gemstone": "Agate",
        "keywords": {
            "love": ["playful", "communicative", "curious", "flirtatious", "mentally stimulating"],
            "career": ["writing", "media", "teaching", "networking", "multi-tasking"],
            "health": ["lungs", "nervous system", "hands", "shoulders", "mental agility"],
            "spirituality": ["divine mind", "sacred words", "information integration", "dual nature"],
        },
        "body_part": "Lungs, Shoulders, Hands, Nervous System",
        "tarot_card": "The Lovers",
        "metal": "Mercury (Quicksilver)",
        "day_phrase": "I think",
        "archetype": "The Communicator",
        "shadow_side": "inconsistency, superficiality, indecisiveness",
        "positive_affirmation": "My mind is a bridge between worlds, connecting all with curiosity.",
    },
    {
        "name": "Cancer",
        "symbol": "♋",
        "unicode_symbol": "♋",
        "dates": "June 21 – July 22",
        "date_range": (6, 21, 7, 22),
        "element": "Water",
        "modality": "Cardinal",
        "ruling_planet": "Moon",
        "house": 4,
        "traits": [
            "intuitive", "nurturing", "empathetic", "protective",
            "imaginative", "loyal", "moody", "sentimental",
        ],
        "life_areas": ["home", "family", "emotions", "roots", "security"],
        "compatible_signs": ["Scorpio", "Pisces", "Taurus", "Virgo"],
        "incompatible_signs": ["Aries", "Libra"],
        "lucky_numbers": [2, 7, 11, 16, 20],
        "lucky_colors": ["Silver", "Sea Green", "Milky White"],
        "lucky_days": ["Monday", "Thursday"],
        "lucky_gemstone": "Pearl",
        "keywords": {
            "love": ["devoted", "protective", "deeply emotional", "romantic", "family-oriented"],
            "career": ["caregiving", "real estate", "food", "hospitality", "psychology"],
            "health": ["stomach", "chest", "digestion", "emotional eating", "self-care"],
            "spirituality": ["lunar wisdom", "ancestral connection", "emotional alchemy", "inner child"],
        },
        "body_part": "Breasts, Stomach, Chest",
        "tarot_card": "The Chariot",
        "metal": "Silver",
        "day_phrase": "I feel",
        "archetype": "The Nurturer",
        "shadow_side": "clinginess, moodiness, over-sensitivity",
        "positive_affirmation": "My emotions are my compass, guiding me home to love.",
    },
    {
        "name": "Leo",
        "symbol": "♌",
        "unicode_symbol": "♌",
        "dates": "July 23 – August 22",
        "date_range": (7, 23, 8, 22),
        "element": "Fire",
        "modality": "Fixed",
        "ruling_planet": "Sun",
        "house": 5,
        "traits": [
            "dramatic", "creative", "self-confident", "generous",
            "cheerful", "arrogant", "dominant", "theatrical",
        ],
        "life_areas": ["creativity", "self-expression", "romance", "children", "joy"],
        "compatible_signs": ["Aries", "Sagittarius", "Gemini", "Libra"],
        "incompatible_signs": ["Taurus", "Scorpio"],
        "lucky_numbers": [1, 5, 9, 19, 41],
        "lucky_colors": ["Gold", "Orange", "Royal Purple"],
        "lucky_days": ["Sunday", "Tuesday"],
        "lucky_gemstone": "Ruby",
        "keywords": {
            "love": ["passionate", "royal", "generous", "theatrical", "devoted"],
            "career": ["entertainment", "leadership", "creative arts", "management", "performance"],
            "health": ["heart", "spine", "vitality", "energy levels", "solar plexus"],
            "spirituality": ["solar consciousness", "divine creativity", "sacred play", "light"],
        },
        "body_part": "Heart, Spine, Upper Back",
        "tarot_card": "Strength",
        "metal": "Gold",
        "day_phrase": "I will",
        "archetype": "The Royalty",
        "shadow_side": "arrogance, drama, need for validation",
        "positive_affirmation": "I radiate love, creativity, and the light of my authentic self.",
    },
    {
        "name": "Virgo",
        "symbol": "♍",
        "unicode_symbol": "♍",
        "dates": "August 23 – September 22",
        "date_range": (8, 23, 9, 22),
        "element": "Earth",
        "modality": "Mutable",
        "ruling_planet": "Mercury",
        "house": 6,
        "traits": [
            "analytical", "practical", "diligent", "meticulous",
            "helpful", "critical", "precise", "modest",
        ],
        "life_areas": ["work", "health", "daily routines", "service", "purification"],
        "compatible_signs": ["Taurus", "Capricorn", "Cancer", "Scorpio"],
        "incompatible_signs": ["Gemini", "Sagittarius"],
        "lucky_numbers": [5, 14, 15, 23, 32],
        "lucky_colors": ["Navy Blue", "Grey", "Tan", "Beige"],
        "lucky_days": ["Wednesday", "Friday"],
        "lucky_gemstone": "Sapphire",
        "keywords": {
            "love": ["attentive", "devoted", "thoughtful", "analytical", "service-oriented"],
            "career": ["analysis", "healthcare", "editing", "research", "problem-solving"],
            "health": ["digestive system", "intestines", "nervous system", "precision", "diet"],
            "spirituality": ["sacred service", "divine order", "purification rituals", "discernment"],
        },
        "body_part": "Digestive System, Intestines, Nervous System",
        "tarot_card": "The Hermit",
        "metal": "Mercury (Quicksilver)",
        "day_phrase": "I analyze",
        "archetype": "The Healer",
        "shadow_side": "perfectionism, criticism, over-analysis",
        "positive_affirmation": "I trust the divine perfection unfolding in every detail of my life.",
    },
    {
        "name": "Libra",
        "symbol": "♎",
        "unicode_symbol": "♎",
        "dates": "September 23 – October 22",
        "date_range": (9, 23, 10, 22),
        "element": "Air",
        "modality": "Cardinal",
        "ruling_planet": "Venus",
        "house": 7,
        "traits": [
            "diplomatic", "charming", "fair-minded", "cooperative",
            "indecisive", "idealistic", "gracious", "social",
        ],
        "life_areas": ["partnerships", "balance", "justice", "beauty", "harmony"],
        "compatible_signs": ["Gemini", "Aquarius", "Leo", "Sagittarius"],
        "incompatible_signs": ["Cancer", "Capricorn"],
        "lucky_numbers": [4, 6, 13, 15, 24],
        "lucky_colors": ["Blue", "Pink", "Lavender", "Pale Green"],
        "lucky_days": ["Friday", "Saturday"],
        "lucky_gemstone": "Opal",
        "keywords": {
            "love": ["harmonious", "romantic", "balanced", "charming", "committed"],
            "career": ["law", "diplomacy", "design", "counseling", "public relations"],
            "health": ["kidneys", "lower back", "adrenal glands", "skin", "balance"],
            "spirituality": ["sacred balance", "divine justice", "beauty as path", "karma"],
        },
        "body_part": "Kidneys, Lower Back, Adrenal Glands",
        "tarot_card": "Justice",
        "metal": "Copper",
        "day_phrase": "I balance",
        "archetype": "The Diplomat",
        "shadow_side": "indecisiveness, people-pleasing, avoidance of conflict",
        "positive_affirmation": "I create harmony within myself and in all my relationships.",
    },
    {
        "name": "Scorpio",
        "symbol": "♏",
        "unicode_symbol": "♏",
        "dates": "October 23 – November 21",
        "date_range": (10, 23, 11, 21),
        "element": "Water",
        "modality": "Fixed",
        "ruling_planet": "Pluto",
        "house": 8,
        "traits": [
            "passionate", "resourceful", "brave", "stubborn",
            "mysterious", "intense", "magnetic", "transformative",
        ],
        "life_areas": ["transformation", "shared resources", "mysteries", "power", "rebirth"],
        "compatible_signs": ["Cancer", "Pisces", "Virgo", "Capricorn"],
        "incompatible_signs": ["Leo", "Aquarius"],
        "lucky_numbers": [8, 11, 18, 22, 29],
        "lucky_colors": ["Deep Red", "Black", "Maroon"],
        "lucky_days": ["Tuesday", "Thursday"],
        "lucky_gemstone": "Topaz",
        "keywords": {
            "love": ["intense", "transformative", "loyal", "magnetic", "deep"],
            "career": ["investigation", "psychology", "research", "finance", "occult"],
            "health": ["reproductive system", "elimination", "detoxification", "willpower"],
            "spirituality": ["shamanic wisdom", "death and rebirth", "hidden truth", "phoenix rising"],
        },
        "body_part": "Reproductive Organs, Bladder, Bowels",
        "tarot_card": "Death",
        "metal": "Steel / Iron",
        "day_phrase": "I desire",
        "archetype": "The Alchemist",
        "shadow_side": "jealousy, manipulation, obsession",
        "positive_affirmation": "I transform darkness into light and emerge renewed with every cycle.",
    },
    {
        "name": "Sagittarius",
        "symbol": "♐",
        "unicode_symbol": "♐",
        "dates": "November 22 – December 21",
        "date_range": (11, 22, 12, 21),
        "element": "Fire",
        "modality": "Mutable",
        "ruling_planet": "Jupiter",
        "house": 9,
        "traits": [
            "optimistic", "freedom-loving", "philosophical", "adventurous",
            "enthusiastic", "blunt", "idealistic", "restless",
        ],
        "life_areas": ["philosophy", "travel", "higher education", "expansion", "truth"],
        "compatible_signs": ["Aries", "Leo", "Libra", "Aquarius"],
        "incompatible_signs": ["Virgo", "Pisces"],
        "lucky_numbers": [3, 9, 12, 21, 36],
        "lucky_colors": ["Purple", "Turquoise", "Navy Blue"],
        "lucky_days": ["Thursday", "Sunday"],
        "lucky_gemstone": "Turquoise",
        "keywords": {
            "love": ["adventurous", "honest", "free-spirited", "passionate", "philosophical"],
            "career": ["travel", "academia", "publishing", "law", "spirituality"],
            "health": ["hips", "thighs", "liver", "sciatic nerve", "outdoor activity"],
            "spirituality": ["cosmic truth", "sacred adventure", "divine wisdom", "expansion"],
        },
        "body_part": "Hips, Thighs, Liver, Sciatic Nerve",
        "tarot_card": "Temperance",
        "metal": "Tin",
        "day_phrase": "I seek",
        "archetype": "The Seeker",
        "shadow_side": "tactlessness, overcommitment, escapism",
        "positive_affirmation": "My wisdom expands with every experience and every horizon I explore.",
    },
    {
        "name": "Capricorn",
        "symbol": "♑",
        "unicode_symbol": "♑",
        "dates": "December 22 – January 19",
        "date_range": (12, 22, 1, 19),
        "element": "Earth",
        "modality": "Cardinal",
        "ruling_planet": "Saturn",
        "house": 10,
        "traits": [
            "responsible", "disciplined", "self-controlled", "ambitious",
            "practical", "serious", "cautious", "determined",
        ],
        "life_areas": ["career", "public reputation", "ambition", "structure", "legacy"],
        "compatible_signs": ["Taurus", "Virgo", "Scorpio", "Pisces"],
        "incompatible_signs": ["Aries", "Libra"],
        "lucky_numbers": [4, 8, 13, 22, 31],
        "lucky_colors": ["Brown", "Dark Green", "Grey", "Black"],
        "lucky_days": ["Saturday", "Tuesday"],
        "lucky_gemstone": "Garnet",
        "keywords": {
            "love": ["committed", "traditional", "loyal", "protective", "structured"],
            "career": ["management", "finance", "engineering", "government", "real estate"],
            "health": ["knees", "bones", "teeth", "skin", "discipline"],
            "spirituality": ["sacred structure", "divine timing", "karmic mastery", "mountain wisdom"],
        },
        "body_part": "Knees, Bones, Teeth, Skin",
        "tarot_card": "The Devil",
        "metal": "Lead",
        "day_phrase": "I use",
        "archetype": "The Elder",
        "shadow_side": "pessimism, rigidity, over-ambition",
        "positive_affirmation": "I build my legacy with integrity, patience, and unwavering purpose.",
    },
    {
        "name": "Aquarius",
        "symbol": "♒",
        "unicode_symbol": "♒",
        "dates": "January 20 – February 18",
        "date_range": (1, 20, 2, 18),
        "element": "Air",
        "modality": "Fixed",
        "ruling_planet": "Uranus",
        "house": 11,
        "traits": [
            "progressive", "original", "independent", "humanitarian",
            "intellectual", "aloof", "unconventional", "visionary",
        ],
        "life_areas": ["community", "innovation", "social reform", "friendship", "future"],
        "compatible_signs": ["Gemini", "Libra", "Aries", "Sagittarius"],
        "incompatible_signs": ["Taurus", "Scorpio"],
        "lucky_numbers": [4, 7, 11, 22, 29],
        "lucky_colors": ["Electric Blue", "Silver", "Turquoise"],
        "lucky_days": ["Saturday", "Sunday"],
        "lucky_gemstone": "Amethyst",
        "keywords": {
            "love": ["independent", "intellectual", "unconventional", "friendship-based", "visionary"],
            "career": ["technology", "science", "social work", "innovation", "activism"],
            "health": ["ankles", "circulatory system", "nervous system", "freedom", "community"],
            "spirituality": ["cosmic consciousness", "collective awakening", "divine innovation", "unity"],
        },
        "body_part": "Ankles, Calves, Circulatory System",
        "tarot_card": "The Star",
        "metal": "Uranium / Aluminum",
        "day_phrase": "I know",
        "archetype": "The Visionary",
        "shadow_side": "aloofness, rebellion, emotional detachment",
        "positive_affirmation": "I serve humanity's evolution with my unique vision and awakened mind.",
    },
    {
        "name": "Pisces",
        "symbol": "♓",
        "unicode_symbol": "♓",
        "dates": "February 19 – March 20",
        "date_range": (2, 19, 3, 20),
        "element": "Water",
        "modality": "Mutable",
        "ruling_planet": "Neptune",
        "house": 12,
        "traits": [
            "compassionate", "artistic", "intuitive", "gentle",
            "wise", "musical", "overly-trusting", "escapist",
        ],
        "life_areas": ["spirituality", "subconscious", "hidden enemies", "compassion", "dreams"],
        "compatible_signs": ["Cancer", "Scorpio", "Taurus", "Capricorn"],
        "incompatible_signs": ["Gemini", "Sagittarius"],
        "lucky_numbers": [3, 9, 12, 15, 18],
        "lucky_colors": ["Sea Green", "Lavender", "Violet"],
        "lucky_days": ["Thursday", "Monday"],
        "lucky_gemstone": "Aquamarine",
        "keywords": {
            "love": ["unconditional", "empathic", "romantic", "spiritual", "selfless"],
            "career": ["arts", "healing", "spirituality", "music", "service"],
            "health": ["feet", "lymphatic system", "immune system", "sleep", "intuition"],
            "spirituality": ["cosmic union", "divine love", "mystical visions", "transcendence"],
        },
        "body_part": "Feet, Lymphatic System, Immune System",
        "tarot_card": "The Moon",
        "metal": "Tin",
        "day_phrase": "I believe",
        "archetype": "The Mystic",
        "shadow_side": "escapism, victimhood, boundary dissolution",
        "positive_affirmation": "I flow with divine love, healing myself and the world through compassion.",
    },
]

# ---------------------------------------------------------------------------
# Quick-lookup helpers
# ---------------------------------------------------------------------------

SIGNS_BY_NAME: dict[str, dict] = {s["name"]: s for s in ZODIAC_SIGNS}
SIGNS_ORDER: list[str] = [s["name"] for s in ZODIAC_SIGNS]

# Element groupings
FIRE_SIGNS = ["Aries", "Leo", "Sagittarius"]
EARTH_SIGNS = ["Taurus", "Virgo", "Capricorn"]
AIR_SIGNS = ["Gemini", "Libra", "Aquarius"]
WATER_SIGNS = ["Cancer", "Scorpio", "Pisces"]

ELEMENT_GROUPS = {
    "Fire": FIRE_SIGNS,
    "Earth": EARTH_SIGNS,
    "Air": AIR_SIGNS,
    "Water": WATER_SIGNS,
}

# Planetary day rulers (Monday=0 in Python weekday)
PLANETARY_RULERS_BY_WEEKDAY: dict[int, str] = {
    0: "Moon",
    1: "Mars",
    2: "Mercury",
    3: "Jupiter",
    4: "Venus",
    5: "Saturn",
    6: "Sun",
}

WEEKDAY_NAMES: dict[int, str] = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}


def get_sign(name: str) -> dict:
    """Return zodiac data dict for the given sign name (case-insensitive)."""
    name_cap = name.strip().title()
    if name_cap not in SIGNS_BY_NAME:
        raise ValueError(f"Unknown sign: {name!r}. Valid: {SIGNS_ORDER}")
    return SIGNS_BY_NAME[name_cap]


def sign_for_date(month: int, day: int) -> str:
    """
    Return zodiac sign name for a given birth month/day.
    Handles Capricorn's year-wrap (Dec 22 – Jan 19).
    """
    boundaries = [
        (3, 21, "Aries"),
        (4, 20, "Taurus"),
        (5, 21, "Gemini"),
        (6, 21, "Cancer"),
        (7, 23, "Leo"),
        (8, 23, "Virgo"),
        (9, 23, "Libra"),
        (10, 23, "Scorpio"),
        (11, 22, "Sagittarius"),
        (12, 22, "Capricorn"),
    ]
    for bm, bd, name in boundaries:
        if month < bm or (month == bm and day < bd):
            prev_idx = SIGNS_ORDER.index(name) - 1
            return SIGNS_ORDER[prev_idx]
    # December 22 onward is Capricorn; Jan 1–19 also Capricorn
    if month == 1 and day <= 19:
        return "Capricorn"
    return "Capricorn"


def get_element_mates(sign_name: str) -> list[str]:
    """Return other signs in the same element."""
    sign = get_sign(sign_name)
    return [s for s in ELEMENT_GROUPS[sign["element"]] if s != sign_name]


def get_planetary_ruler_today(weekday: int) -> str:
    """Return the planetary ruler for a given weekday (0=Monday)."""
    return PLANETARY_RULERS_BY_WEEKDAY[weekday % 7]
