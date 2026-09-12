"""
intelligence/global_expansion.py — 10x Views via Language & Market Expansion
=============================================================================
Translates, culturally adapts, and localises every video into 6 languages
covering ~4 billion people.

Languages: Spanish (es), Hindi (hi), Portuguese (pt),
           French (fr), Arabic (ar), Indonesian (id)

Pipeline per language:
  1. TranslationPipeline  — DeepL API (+ Claude fallback for Hindi)
  2. CulturalAdapter      — region examples, tone, content filters
  3. Voice re-synthesis   — ElevenLabs multilingual voices
  4. ThumbnailLocalizer   — culture-specific PIL colour/style overlay
  5. MultilingualSEO      — local titles, tags, search-volume estimation
  6. RegionalTrendHunter  — InnerTube per-country trend sampling
  7. ChannelManager       — per-language channel registry + cross-links
  8. GlobalExpansion      — orchestrator
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("brain.global_expansion")

HERE = Path(__file__).parent.parent

# ── Language profiles ─────────────────────────────────────────────────────────

LANGUAGE_PROFILES: Dict[str, Dict] = {
    "es": {
        "name": "Spanish",        "native": "Español",
        "deepl_code": "ES",
        "innertube_gl": "MX",     "innertube_hl": "es",
        "country_codes": ["MX", "ES", "CO", "AR"],
        "primary_market": "Latin America",
        "population_reach": 559_000_000,
        "elevenlabs_voice": "pNInz6obpgDQGcFmaJgB",
        "thumbnail_style": "high_contrast_emotional",
        "color_palette": ["#FF4136", "#FFDC00", "#2ECC40"],
        "rtl": False,
    },
    "hi": {
        "name": "Hindi",          "native": "हिन्दी",
        "deepl_code": None,       # DeepL doesn't support Hindi; Claude handles it
        "innertube_gl": "IN",     "innertube_hl": "hi",
        "country_codes": ["IN"],
        "primary_market": "India",
        "population_reach": 600_000_000,
        "elevenlabs_voice": "pNInz6obpgDQGcFmaJgB",
        "thumbnail_style": "bright_bold_indian",
        "color_palette": ["#FF6B35", "#FFD700", "#FF1493"],
        "rtl": False,
    },
    "pt": {
        "name": "Portuguese",     "native": "Português",
        "deepl_code": "PT-BR",
        "innertube_gl": "BR",     "innertube_hl": "pt",
        "country_codes": ["BR", "PT"],
        "primary_market": "Brazil",
        "population_reach": 260_000_000,
        "elevenlabs_voice": "pNInz6obpgDQGcFmaJgB",
        "thumbnail_style": "vibrant_energetic",
        "color_palette": ["#009C3B", "#FFDF00", "#FF4500"],
        "rtl": False,
    },
    "fr": {
        "name": "French",         "native": "Français",
        "deepl_code": "FR",
        "innertube_gl": "FR",     "innertube_hl": "fr",
        "country_codes": ["FR", "BE", "CH", "CA"],
        "primary_market": "France",
        "population_reach": 300_000_000,
        "elevenlabs_voice": "pNInz6obpgDQGcFmaJgB",
        "thumbnail_style": "minimalist_european",
        "color_palette": ["#002395", "#FFFFFF", "#ED2939"],
        "rtl": False,
    },
    "ar": {
        "name": "Arabic",         "native": "العربية",
        "deepl_code": "AR",
        "innertube_gl": "SA",     "innertube_hl": "ar",
        "country_codes": ["SA", "EG", "AE", "MA"],
        "primary_market": "MENA",
        "population_reach": 422_000_000,
        "elevenlabs_voice": "pNInz6obpgDQGcFmaJgB",
        "thumbnail_style": "bold_arabic",
        "color_palette": ["#006C35", "#FFD700", "#C8102E"],
        "rtl": True,
    },
    "id": {
        "name": "Indonesian",     "native": "Bahasa Indonesia",
        "deepl_code": "ID",
        "innertube_gl": "ID",     "innertube_hl": "id",
        "country_codes": ["ID"],
        "primary_market": "Indonesia",
        "population_reach": 270_000_000,
        "elevenlabs_voice": "pNInz6obpgDQGcFmaJgB",
        "thumbnail_style": "bright_community",
        "color_palette": ["#CE1126", "#FFFFFF", "#009B3A"],
        "rtl": False,
    },
}

TOTAL_REACH = sum(p["population_reach"] for p in LANGUAGE_PROFILES.values())

# ── Cultural adaptation tables ────────────────────────────────────────────────

CULTURAL_ADAPTATIONS: Dict[str, Dict] = {
    "es": {
        "brand_subs": {"Amazon": "MercadoLibre", "Walmart": "Chedraui", "Netflix": "Netflix"},
        "example_subs": {
            "Silicon Valley": "Ciudad de México tech hub",
            "Wall Street": "Bolsa Mexicana de Valores",
            "US inflation": "inflación en Latinoamérica",
        },
        "urgency_phrases": ["¡No te lo pierdas!", "¡Ahora mismo!", "¡Increíble!"],
        "currency_symbol": "$",
        "avoid": [],
        "tone": "warm_conversational",
    },
    "hi": {
        "brand_subs": {"Amazon": "Flipkart", "Walmart": "Reliance Retail", "Uber": "Ola"},
        "example_subs": {
            "Silicon Valley": "बेंगलुरु टेक हब",
            "Wall Street": "बॉम्बे स्टॉक एक्सचेंज",
            "US inflation": "भारत में महंगाई",
        },
        "urgency_phrases": ["अभी देखें!", "जल्दी करें!", "अविश्वसनीय!"],
        "currency_symbol": "₹",
        "avoid": [],
        "tone": "respectful_enthusiastic",
        "local_icons": ["Mukesh Ambani", "Ratan Tata"],
    },
    "pt": {
        "brand_subs": {"Amazon": "Amazon Brasil", "Uber": "99", "Netflix": "Netflix"},
        "example_subs": {
            "Silicon Valley": "polo tech de São Paulo",
            "Wall Street": "B3 Bolsa de Valores",
            "US inflation": "inflação no Brasil",
        },
        "urgency_phrases": ["Não perca!", "Agora mesmo!", "Incrível!"],
        "currency_symbol": "R$",
        "avoid": [],
        "tone": "energetic_friendly",
    },
    "fr": {
        "brand_subs": {"Amazon": "Amazon.fr", "Walmart": "Carrefour", "Uber": "Uber"},
        "example_subs": {
            "Silicon Valley": "Station F Paris",
            "Wall Street": "CAC 40",
            "US inflation": "inflation en Europe",
        },
        "urgency_phrases": ["Ne manquez pas ça!", "Immédiatement!", "Extraordinaire!"],
        "currency_symbol": "€",
        "avoid": [],
        "tone": "measured_intellectual",
    },
    "ar": {
        "brand_subs": {"Amazon": "Amazon.ae", "Walmart": "Lulu Hypermarket", "Uber": "Careem"},
        "example_subs": {
            "Silicon Valley": "مدينة دبي للإنترنت",
            "Wall Street": "سوق الأسهم السعودي تداول",
            "US inflation": "التضخم في دول الخليج",
        },
        "urgency_phrases": ["لا تفوت هذا!", "الآن!", "رائع!"],
        "currency_symbol": "﷼",
        "avoid": ["alcohol", "gambling", "pork", "dating"],
        "tone": "formal_authoritative",
    },
    "id": {
        "brand_subs": {"Amazon": "Tokopedia", "Uber": "Gojek", "Airbnb": "Traveloka"},
        "example_subs": {
            "Silicon Valley": "kawasan startup Jakarta",
            "Wall Street": "Bursa Efek Indonesia",
            "US inflation": "inflasi di Indonesia",
        },
        "urgency_phrases": ["Jangan sampai ketinggalan!", "Sekarang juga!", "Luar biasa!"],
        "currency_symbol": "Rp",
        "avoid": [],
        "tone": "friendly_inclusive",
    },
}

# ── Thumbnail style parameters ────────────────────────────────────────────────

THUMBNAIL_STYLES: Dict[str, Dict] = {
    "bright_bold_indian": {
        "brightness": 1.30, "saturation": 1.60, "contrast": 1.25,
        "text_scale": 1.15, "text_color": "#FFFFFF",
        "stroke_color": "#FF6B35", "stroke_width": 4,
        "border_color": "#FFD700", "border_px": 8,
        "glow": True, "rtl": False,
    },
    "minimalist_european": {
        "brightness": 1.00, "saturation": 0.75, "contrast": 1.05,
        "text_scale": 0.85, "text_color": "#1A1A2E",
        "stroke_color": None, "stroke_width": 0,
        "border_color": None, "border_px": 0,
        "glow": False, "rtl": False,
    },
    "high_contrast_emotional": {
        "brightness": 1.15, "saturation": 1.30, "contrast": 1.35,
        "text_scale": 1.00, "text_color": "#FFFFFF",
        "stroke_color": "#FF4136", "stroke_width": 3,
        "border_color": "#FF4136", "border_px": 5,
        "glow": False, "rtl": False,
    },
    "vibrant_energetic": {
        "brightness": 1.20, "saturation": 1.45, "contrast": 1.20,
        "text_scale": 1.10, "text_color": "#FFDF00",
        "stroke_color": "#002776", "stroke_width": 4,
        "border_color": "#009C3B", "border_px": 6,
        "glow": True, "rtl": False,
    },
    "bold_arabic": {
        "brightness": 1.10, "saturation": 1.25, "contrast": 1.20,
        "text_scale": 1.05, "text_color": "#FFD700",
        "stroke_color": "#1A1A2E", "stroke_width": 3,
        "border_color": "#006C35", "border_px": 6,
        "glow": False, "rtl": True,
    },
    "bright_community": {
        "brightness": 1.15, "saturation": 1.35, "contrast": 1.15,
        "text_scale": 1.00, "text_color": "#FFFFFF",
        "stroke_color": "#CE1126", "stroke_width": 3,
        "border_color": "#CE1126", "border_px": 5,
        "glow": False, "rtl": False,
    },
}

# ── SEO keyword boosters per language ─────────────────────────────────────────

SEO_BOOSTERS: Dict[str, List[str]] = {
    "es": ["cómo", "tutorial", "gratis", "mejor", "2026", "secretos", "explicado"],
    "hi": ["कैसे", "टिप्स", "फ्री", "बेस्ट", "2026", "सीक्रेट", "हिंदी में"],
    "pt": ["como", "tutorial", "grátis", "melhor", "2026", "segredos", "dicas"],
    "fr": ["comment", "tutoriel", "gratuit", "meilleur", "2026", "secrets", "expliqué"],
    "ar": ["كيف", "شرح", "مجاناً", "أفضل", "2026", "أسرار", "بالعربي"],
    "id": ["cara", "tutorial", "gratis", "terbaik", "2026", "rahasia", "indonesia"],
}


# ── 1. TranslationPipeline ────────────────────────────────────────────────────

class TranslationPipeline:
    """
    Translates scripts and metadata via DeepL REST API.
    Hindi falls back to Claude Haiku (DeepL doesn't support it).
    When no API key is present, returns clearly-marked placeholder text
    so the pipeline stays functional in dev/test environments.
    """

    DEEPL_URL = "https://api-free.deepl.com/v2/translate"

    def __init__(self):
        self._deepl_key  = os.getenv("DEEPL_API_KEY", "")
        self._claude_key = os.getenv("ANTHROPIC_API_KEY", "")

    # ── Public API ────────────────────────────────────────────────────────

    def translate(self, text: str, target_lang: str) -> str:
        """Translate text to the given language code (es/hi/pt/fr/ar/id)."""
        if not text:
            return text
        profile = LANGUAGE_PROFILES.get(target_lang, {})
        deepl_code = profile.get("deepl_code")

        if deepl_code and self._deepl_key:
            result = self._deepl_translate(text, deepl_code)
            if result:
                return result

        # Hindi (no DeepL) or DeepL unavailable → try Claude
        if self._claude_key:
            result = self._claude_translate(text, target_lang, profile.get("name", target_lang))
            if result:
                return result

        # Graceful fallback: mark as untranslated for human review
        return f"[{target_lang.upper()}_TRANSLATION_NEEDED] {text}"

    def translate_metadata(self, title: str, description: str,
                           tags: List[str], target_lang: str) -> Dict:
        """Translate title, description and tags; inject local SEO boosters."""
        t_title = self.translate(title, target_lang)
        t_desc  = self.translate(description[:500], target_lang) if description else ""
        t_tags  = [self.translate(tag, target_lang) for tag in (tags or [])[:10]]

        # Append language-specific search boosters to tag list
        boosters = SEO_BOOSTERS.get(target_lang, [])
        for b in boosters[:5]:
            if b not in t_tags:
                t_tags.append(b)

        return {
            "title":       t_title[:100],
            "description": t_desc,
            "tags":        t_tags[:30],
            "language":    target_lang,
        }

    def translate_script(self, script: Dict, target_lang: str) -> Dict:
        """Deep-copy a script dict and translate all text fields."""
        out = deepcopy(script)
        if "hook" in out:
            out["hook"] = self.translate(out["hook"], target_lang)
        if "title" in out:
            out["title"] = self.translate(out["title"], target_lang)
        for scene in out.get("scenes", []):
            if isinstance(scene, dict):
                if "narration" in scene:
                    scene["narration"] = self.translate(scene["narration"], target_lang)
                if "title" in scene:
                    scene["title"] = self.translate(scene["title"], target_lang)
                if "text_overlay" in scene:
                    scene["text_overlay"] = self.translate(scene["text_overlay"], target_lang)
        out["language"] = target_lang
        return out

    # ── Private helpers ───────────────────────────────────────────────────

    def _deepl_translate(self, text: str, deepl_code: str) -> Optional[str]:
        payload = json.dumps({
            "text": [text[:5000]],
            "target_lang": deepl_code,
        }).encode()
        req = urllib.request.Request(
            self.DEEPL_URL,
            data=payload,
            headers={
                "Authorization": f"DeepL-Auth-Key {self._deepl_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data["translations"][0]["text"]
        except Exception as e:
            log.debug(f"DeepL error for {deepl_code}: {e}")
            return None

    def _claude_translate(self, text: str, lang_code: str, lang_name: str) -> Optional[str]:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._claude_key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Translate the following text to {lang_name}. "
                        "Return ONLY the translated text, no explanation:\n\n"
                        f"{text[:3000]}"
                    ),
                }],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            log.debug(f"Claude translate error for {lang_code}: {e}")
            return None


# ── 2. CulturalAdapter ────────────────────────────────────────────────────────

class CulturalAdapter:
    """
    Applies region-specific cultural substitutions to a translated script:
    brand names, local examples, urgency phrasing, and content filters.
    """

    def adapt_script(self, script: Dict, language_code: str) -> Dict:
        """Return a culturally adapted copy of the script."""
        adapted = deepcopy(script)
        culture = CULTURAL_ADAPTATIONS.get(language_code, {})

        if "hook" in adapted:
            adapted["hook"] = self._apply_subs(adapted["hook"], culture)
        for scene in adapted.get("scenes", []):
            if isinstance(scene, dict):
                for field in ("narration", "title", "text_overlay"):
                    if field in scene:
                        scene[field] = self._apply_subs(scene[field], culture)

        # Tag on a local urgency phrase to the call-to-action if present
        if "cta" in adapted:
            phrases = culture.get("urgency_phrases", [])
            if phrases:
                adapted["cta"] = f"{phrases[0]} {adapted['cta']}"

        adapted["cultural_region"] = LANGUAGE_PROFILES.get(language_code, {}).get("primary_market", "")
        return adapted

    def filter_inappropriate(self, text: str, language_code: str) -> str:
        """Remove or replace culturally inappropriate references."""
        avoid = CULTURAL_ADAPTATIONS.get(language_code, {}).get("avoid", [])
        for term in avoid:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            text = pattern.sub("[content adapted for local audience]", text)
        return text

    def get_urgency_phrase(self, language_code: str) -> str:
        phrases = CULTURAL_ADAPTATIONS.get(language_code, {}).get("urgency_phrases", [])
        return phrases[0] if phrases else ""

    def _apply_subs(self, text: str, culture: Dict) -> str:
        if not text:
            return text
        for en, local in culture.get("brand_subs", {}).items():
            text = re.sub(r'\b' + re.escape(en) + r'\b', local, text)
        for en, local in culture.get("example_subs", {}).items():
            text = text.replace(en, local)
        text = self.filter_inappropriate(text, next(
            (k for k, v in CULTURAL_ADAPTATIONS.items() if v is culture), "en"
        ))
        return text


# ── 3. ThumbnailLocalizer ─────────────────────────────────────────────────────

class ThumbnailLocalizer:
    """
    Applies culture-specific visual style to an existing thumbnail using PIL.
    Adjusts brightness, saturation, contrast, adds border, and overlays
    translated title text in the correct reading direction.
    """

    def localize(self, thumbnail_path: str, language_code: str,
                 translated_title: str = "", output_dir: str = "") -> Optional[str]:
        """Return path to the localised thumbnail, or None on failure."""
        try:
            from PIL import Image, ImageEnhance, ImageDraw, ImageFont
        except ImportError:
            log.debug("Pillow not installed — skipping thumbnail localization")
            return None

        profile    = LANGUAGE_PROFILES.get(language_code, {})
        style_name = profile.get("thumbnail_style", "high_contrast_emotional")
        style      = THUMBNAIL_STYLES.get(style_name, THUMBNAIL_STYLES["high_contrast_emotional"])

        try:
            img = Image.open(thumbnail_path).convert("RGB")
        except Exception as e:
            log.debug(f"Cannot open thumbnail {thumbnail_path}: {e}")
            return None

        img = self._apply_enhancements(img, style)
        if style.get("border_px", 0) and style.get("border_color"):
            img = self._add_border(img, style["border_px"], style["border_color"])
        if translated_title:
            img = self._overlay_title(img, translated_title, style)

        out_dir = Path(output_dir) if output_dir else Path(thumbnail_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        stem    = Path(thumbnail_path).stem
        out_path = str(out_dir / f"{stem}_{language_code}.jpg")
        img.save(out_path, "JPEG", quality=92)
        return out_path

    # ── PIL helpers ───────────────────────────────────────────────────────

    def _apply_enhancements(self, img, style):
        from PIL import ImageEnhance
        img = ImageEnhance.Brightness(img).enhance(style.get("brightness", 1.0))
        img = ImageEnhance.Color(img).enhance(style.get("saturation", 1.0))
        img = ImageEnhance.Contrast(img).enhance(style.get("contrast", 1.0))
        return img

    def _add_border(self, img, border_px: int, hex_color: str):
        from PIL import ImageOps
        rgb = self._hex_to_rgb(hex_color)
        return ImageOps.expand(img, border=border_px, fill=rgb)

    def _overlay_title(self, img, title: str, style: Dict):
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        w, h = img.size
        font_size = max(20, int(h * 0.07 * style.get("text_scale", 1.0)))

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                       font_size)
        except Exception:
            font = ImageFont.load_default()

        # Wrap text to ~30 chars
        words = title.split()
        lines, line = [], []
        for w_tok in words:
            if len(" ".join(line + [w_tok])) > 28:
                if line:
                    lines.append(" ".join(line))
                line = [w_tok]
            else:
                line.append(w_tok)
        if line:
            lines.append(" ".join(line))
        lines = lines[:3]

        text_color = self._hex_to_rgb(style.get("text_color", "#FFFFFF"))
        stroke_color = (self._hex_to_rgb(style["stroke_color"])
                        if style.get("stroke_color") else None)
        stroke_w = style.get("stroke_width", 0)

        y = h - (len(lines) * (font_size + 6)) - 10
        for line_text in lines:
            bbox   = draw.textbbox((0, 0), line_text, font=font)
            text_w = bbox[2] - bbox[0]
            x = (w - text_w) // 2
            if stroke_color:
                draw.text((x - stroke_w, y), line_text, font=font, fill=stroke_color)
                draw.text((x + stroke_w, y), line_text, font=font, fill=stroke_color)
                draw.text((x, y - stroke_w), line_text, font=font, fill=stroke_color)
                draw.text((x, y + stroke_w), line_text, font=font, fill=stroke_color)
            draw.text((x, y), line_text, font=font, fill=text_color)
            y += font_size + 6
        return img

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> Tuple:
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


# ── 4. RegionalTrendHunter ────────────────────────────────────────────────────

class RegionalTrendHunter:
    """
    Samples YouTube homepage trends per country via InnerTube's browse API.
    Detects early-mover opportunities when a trend peaks in one market
    ~2+ weeks before appearing in another.
    """

    BROWSE_URL = "https://www.youtube.com/youtubei/v1/browse"
    API_KEY    = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

    TREND_MARKETS = [
        ("IN", "hi"), ("US", "en"), ("MX", "es"), ("BR", "pt"),
        ("FR", "fr"), ("SA", "ar"), ("ID", "id"), ("GB", "en"),
        ("DE", "de"), ("JP", "ja"),
    ]

    def fetch_country_trends(self, country_code: str, hl: str,
                              n: int = 20) -> List[Dict]:
        """Return top trending videos for a specific country."""
        payload = json.dumps({
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101",
                    "gl": country_code,
                    "hl": hl,
                }
            },
            "browseId": "FEtrending",
        }).encode()
        req = urllib.request.Request(
            f"{self.BROWSE_URL}?key={self.API_KEY}",
            data=payload,
            headers={"Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data  = json.loads(resp.read())
                items = self._extract_items(data)
                return [{"title": it["title"], "views": it.get("views", 0),
                         "country": country_code, "language": hl,
                         "source": "innertube_trending"}
                        for it in items[:n]]
        except Exception as e:
            log.debug(f"Trend fetch failed for {country_code}: {e}")
            return []

    def fetch_all_markets(self) -> Dict[str, List[Dict]]:
        """Fetch trends for all tracked markets in parallel."""
        results: Dict[str, List[Dict]] = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(self.fetch_country_trends, gl, hl): gl
                for gl, hl in self.TREND_MARKETS
            }
            for fut in as_completed(futures):
                cc = futures[fut]
                try:
                    results[cc] = fut.result()
                except Exception as e:
                    log.debug(f"Market {cc} fetch error: {e}")
                    results[cc] = []
        return results

    def detect_early_trends(self, market_data: Dict[str, List[Dict]] = None,
                             memory=None) -> List[Dict]:
        """
        Identify topics trending in 1-2 markets but not yet in the main
        English-speaking market — these are early-mover opportunities.
        Returns ranked list of early signals.
        """
        if market_data is None:
            market_data = self.fetch_all_markets()

        # Build normalised title sets per market
        def norm(t): return re.sub(r"[^a-z0-9 ]", "", t.lower())

        english_titles = {norm(v["title"]) for v in market_data.get("US", [])}
        english_titles |= {norm(v["title"]) for v in market_data.get("GB", [])}

        early: List[Dict] = []
        checked: set = set()

        for cc, videos in market_data.items():
            if cc in ("US", "GB"):
                continue
            for v in videos:
                key = norm(v["title"])[:40]
                if key in checked or len(key) < 5:
                    continue
                checked.add(key)
                # Present in this non-English market but absent from English
                if not any(key[:15] in et for et in english_titles):
                    early.append({
                        "title":        v["title"],
                        "source_market": cc,
                        "opportunity":  "early_mover",
                        "score":        0.85,
                    })

        if memory:
            try:
                memory.save_regional_trends(early, source="early_detection")
            except Exception:
                pass

        return early[:20]

    def _extract_items(self, data: Dict) -> List[Dict]:
        items = []
        try:
            tabs = (data.get("contents", {})
                        .get("twoColumnBrowseResultsRenderer", {})
                        .get("tabs", []))
            for tab in tabs:
                sections = (tab.get("tabRenderer", {})
                               .get("content", {})
                               .get("sectionListRenderer", {})
                               .get("contents", []))
                for sec in sections:
                    shelf = sec.get("itemSectionRenderer", {})
                    for item in shelf.get("contents", []):
                        expanded = item.get("shelfRenderer", {})
                        for v in expanded.get("content", {}).get("expandedShelfContentsRenderer", {}).get("items", []):
                            vr = v.get("videoRenderer", {})
                            title = vr.get("title", {}).get("runs", [{}])[0].get("text", "")
                            if title:
                                items.append({"title": title})
        except Exception:
            pass
        return items


# ── 5. MultilingualSEO ────────────────────────────────────────────────────────

class MultilingualSEO:
    """
    Generates and optimises SEO metadata (title, description, tags) for each
    language market.  Includes local keyword boosters and search-volume
    estimation heuristics.
    """

    def __init__(self, translator: TranslationPipeline = None):
        self._translator = translator or TranslationPipeline()

    def generate_local_seo(self, title: str, description: str,
                           tags: List[str], language_code: str) -> Dict:
        """Full SEO package for a single target language."""
        translated = self._translator.translate_metadata(
            title, description, tags, language_code
        )
        local_title = self._optimise_title(translated["title"], language_code)
        local_tags  = self._expand_tags(translated["tags"], language_code, title)

        return {
            "title":           local_title,
            "description":     translated["description"],
            "tags":            local_tags,
            "language":        language_code,
            "market":          LANGUAGE_PROFILES.get(language_code, {}).get("primary_market", ""),
            "estimated_reach": LANGUAGE_PROFILES.get(language_code, {}).get("population_reach", 0),
            "search_volume_tier": self._estimate_volume_tier(title, language_code),
        }

    def generate_all_markets(self, title: str, description: str,
                              tags: List[str]) -> Dict[str, Dict]:
        """Return SEO packages for all 6 target languages."""
        results = {}
        for lang in LANGUAGE_PROFILES:
            try:
                results[lang] = self.generate_local_seo(title, description, tags, lang)
            except Exception as e:
                log.warning(f"SEO generation failed for {lang}: {e}")
                results[lang] = {"title": title, "tags": tags, "language": lang}
        return results

    # ── Helpers ───────────────────────────────────────────────────────────

    def _optimise_title(self, title: str, language_code: str) -> str:
        """Ensure title length is within YouTube's 100-char limit and is SEO-strong."""
        boosters = SEO_BOOSTERS.get(language_code, [])
        # Append the first booster keyword if not already present
        if boosters and boosters[0].lower() not in title.lower():
            candidate = f"{title} | {boosters[0]}"
            if len(candidate) <= 100:
                title = candidate
        return title[:100]

    def _expand_tags(self, base_tags: List[str], language_code: str,
                     source_title: str) -> List[str]:
        """Merge base tags with local boosters; return up to 30 unique tags."""
        boosters = SEO_BOOSTERS.get(language_code, [])
        combined = list(dict.fromkeys(base_tags + boosters))
        # Add 2-word keyphrases from source title
        words = re.findall(r'\b\w{4,}\b', source_title.lower())
        for i in range(len(words) - 1):
            combined.append(f"{words[i]} {words[i+1]}")
        return list(dict.fromkeys(combined))[:30]

    def _estimate_volume_tier(self, title: str, language_code: str) -> str:
        """
        Heuristic volume tier: high / medium / low based on market size and
        topic keyword density (real volume requires Google Ads API).
        """
        profile = LANGUAGE_PROFILES.get(language_code, {})
        reach   = profile.get("population_reach", 0)
        boosters = SEO_BOOSTERS.get(language_code, [])
        score = 0
        title_lower = title.lower()
        for kw in boosters:
            if kw in title_lower:
                score += 1
        if reach > 400_000_000 and score >= 2:
            return "high"
        if reach > 200_000_000 or score >= 1:
            return "medium"
        return "low"


# ── 6. ChannelManager ────────────────────────────────────────────────────────

class ChannelManager:
    """
    Manages per-language channel configurations and generates cross-link
    end-screen data so every video points viewers to sister channels.
    """

    def __init__(self, memory=None):
        self._memory = memory

    def register_channel(self, language_code: str, channel_id: str,
                          channel_name: str) -> None:
        if self._memory:
            try:
                self._memory.upsert_language_channel(
                    language_code, channel_id, channel_name
                )
                log.info(f"  Registered channel [{language_code}] {channel_name}")
            except Exception as e:
                log.warning(f"  Channel register failed for {language_code}: {e}")

    def get_channel_config(self, language_code: str) -> Dict:
        if self._memory:
            try:
                return self._memory.get_language_channel(language_code) or {}
            except Exception:
                pass
        return {}

    def get_all_channels(self) -> List[Dict]:
        if self._memory:
            try:
                return self._memory.get_all_language_channels()
            except Exception:
                pass
        return []

    def build_end_screen_links(self, source_language: str,
                                source_yt_id: str = "") -> List[Dict]:
        """
        Generate end-screen data: link to sister channels in 3 other languages.
        Returns a list of dicts ready to pass to the YouTube API end-screens endpoint.
        """
        all_channels = self.get_all_channels()
        links = []
        for ch in all_channels:
            if ch.get("language_code") == source_language:
                continue
            profile = LANGUAGE_PROFILES.get(ch["language_code"], {})
            links.append({
                "type":          "channel",
                "channel_id":    ch.get("channel_id", ""),
                "language_code": ch["language_code"],
                "label":         profile.get("native", ch["language_code"]),
                "market":        profile.get("primary_market", ""),
            })
            if len(links) >= 3:
                break
        return links

    def update_channel_stats(self, language_code: str, subscribers: int,
                              total_views: int, video_count: int = 0) -> None:
        if self._memory:
            try:
                self._memory.update_language_channel_stats(
                    language_code, subscribers, total_views, video_count
                )
            except Exception:
                pass


# ── 7. GlobalExpansion (orchestrator) ────────────────────────────────────────

class GlobalExpansion:
    """
    Orchestrates the full multilingual expansion of a single video:
      translate → culturally adapt → re-synthesise voice → localise thumbnail
      → generate local SEO → queue for per-language channel publish.

    Design principles:
    - One language failure never blocks the others (each wrapped in try/except)
    - All voice and thumbnail work is done in parallel via ThreadPoolExecutor
    - Queues localization_jobs in memory for async retry if APIs are slow
    """

    def __init__(self, memory=None):
        self._memory     = memory
        self._translator = TranslationPipeline()
        self._culture    = CulturalAdapter()
        self._thumb      = ThumbnailLocalizer()
        self._trend      = RegionalTrendHunter()
        self._seo        = MultilingualSEO(self._translator)
        self._channels   = ChannelManager(memory)

    # ── Main entry points ──────────────────────────────────────────────────

    def expand_video(self, source_script: Dict, source_metadata: Dict,
                     thumbnail_path: str = "", source_yt_id: str = "",
                     source_video_db_id: int = None,
                     languages: List[str] = None) -> Dict:
        """
        Expand one produced video into all target languages.
        Returns a result dict keyed by language code.
        """
        target_langs = languages or list(LANGUAGE_PROFILES.keys())
        results: Dict[str, Dict] = {}

        log.info(f"  Global expansion: {len(target_langs)} languages | "
                 f"total reach ~{TOTAL_REACH // 1_000_000:,}M people")

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(self._expand_one_language,
                            lang, source_script, source_metadata,
                            thumbnail_path, source_yt_id,
                            source_video_db_id): lang
                for lang in target_langs
            }
            for fut in as_completed(futures):
                lang = futures[fut]
                try:
                    results[lang] = fut.result()
                    status = results[lang].get("status", "done")
                    log.info(f"  [{lang}] {status} — "
                             f"{results[lang].get('seo',{}).get('title','')[:50]}")
                except Exception as e:
                    log.error(f"  [{lang}] expansion failed: {e}", exc_info=True)
                    results[lang] = {"status": "failed", "error": str(e)}

        return {
            "languages":    results,
            "total_reach":  TOTAL_REACH,
            "success_count": sum(1 for r in results.values() if r.get("status") == "done"),
            "source_yt_id": source_yt_id,
        }

    def run_daily(self, video_data: Dict = None) -> Dict:
        """
        Full daily global-expansion pass:
          1. Fetch regional trends across all markets
          2. Detect early-mover opportunities
          3. If video_data supplied, expand it to all 6 languages
        """
        result: Dict = {}

        # Regional trend sweep
        log.info("  Regional trend sweep across 10 markets…")
        try:
            market_data  = self._trend.fetch_all_markets()
            early_trends = self._trend.detect_early_trends(market_data, self._memory)
            result["market_trends"]   = {cc: len(v) for cc, v in market_data.items()}
            result["early_trends"]    = early_trends
            result["early_trend_count"] = len(early_trends)
            if self._memory:
                self._memory.save_regional_trends(early_trends, source="daily_sweep")
        except Exception as e:
            log.warning(f"  Regional trend sweep failed (non-fatal): {e}")
            result["early_trend_count"] = 0

        # Video expansion (if a video was produced today)
        if video_data:
            expansion = self.expand_video(
                source_script   = video_data.get("script", {}),
                source_metadata = video_data.get("metadata", {}),
                thumbnail_path  = (video_data.get("thumbnails") or [""])[0],
                source_yt_id    = video_data.get("youtube_id", ""),
                source_video_db_id = video_data.get("db_id"),
            )
            result["expansion"] = expansion

        return result

    def get_expansion_status(self, source_video_db_id: int) -> Dict:
        """Return per-language localization status for a given video."""
        if not self._memory:
            return {}
        try:
            return self._memory.get_localization_jobs(source_video_db_id)
        except Exception:
            return {}

    def print_report(self, result: Dict) -> None:
        log.info("\n" + "=" * 60)
        log.info("GLOBAL EXPANSION REPORT")
        log.info("=" * 60)
        log.info(f"  Early-mover trends:  {result.get('early_trend_count', 0)}")
        markets = result.get("market_trends", {})
        if markets:
            log.info(f"  Markets sampled:     {len(markets)}")
            for cc, n in sorted(markets.items()):
                log.info(f"    {cc}: {n} trends")
        expansion = result.get("expansion", {})
        if expansion:
            log.info(f"  Videos expanded:     {expansion.get('success_count', 0)}/"
                     f"{len(LANGUAGE_PROFILES)} languages")
            log.info(f"  Total reach:         ~{expansion.get('total_reach', 0) // 1_000_000:,}M people")
            for lang, info in expansion.get("languages", {}).items():
                profile = LANGUAGE_PROFILES.get(lang, {})
                title   = info.get("seo", {}).get("title", "—")[:50]
                status  = info.get("status", "?")
                log.info(f"    [{lang}] {profile.get('name',''):12s}  {status:6s}  {title}")

    # ── Per-language expansion ────────────────────────────────────────────

    def _expand_one_language(self, lang: str, source_script: Dict,
                              source_metadata: Dict, thumbnail_path: str,
                              source_yt_id: str,
                              source_video_db_id: Optional[int]) -> Dict:
        result: Dict = {"language": lang, "status": "done"}

        if self._memory and source_video_db_id:
            self._memory.save_localization_job(source_video_db_id, lang, "full", "running")

        # Step 1: Translate
        t_script   = self._translator.translate_script(source_script, lang)
        title      = source_metadata.get("title", source_script.get("title", ""))
        description = source_metadata.get("description", "")
        tags        = source_metadata.get("tags", [])

        # Step 2: Cultural adaptation
        t_script = self._culture.adapt_script(t_script, lang)

        # Step 3: SEO metadata
        seo = self._seo.generate_local_seo(title, description, tags, lang)
        result["seo"] = seo

        # Step 4: Voice re-synthesis (optional — skips gracefully if no API key)
        result["voice"] = self._synthesise_voice(t_script, lang)

        # Step 5: Thumbnail localisation
        if thumbnail_path and Path(thumbnail_path).exists():
            out_dir  = str(Path(thumbnail_path).parent)
            t_thumb  = self._thumb.localize(thumbnail_path, lang,
                                             seo.get("title", ""), out_dir)
            result["thumbnail"] = t_thumb
        else:
            result["thumbnail"] = None

        # Step 6: Cross-link end-screen data
        result["end_screen_links"] = self._channels.build_end_screen_links(
            lang, source_yt_id
        )

        # Step 7: Persist to memory
        if self._memory and source_video_db_id:
            try:
                self._memory.save_translation(source_video_db_id, lang, seo)
                self._memory.save_localization_job(
                    source_video_db_id, lang, "full", "done",
                    result={"seo_title": seo.get("title", ""),
                            "voice":     result.get("voice"),
                            "thumbnail": result.get("thumbnail")}
                )
            except Exception as e:
                log.debug(f"  Memory save failed for [{lang}]: {e}")

        return result

    def _synthesise_voice(self, script: Dict, lang: str) -> Optional[str]:
        """
        Calls ElevenLabs multilingual voice synthesis if ELEVENLABS_API_KEY is set.
        Returns the audio file path or None.
        """
        api_key  = os.getenv("ELEVENLABS_API_KEY", "")
        voice_id = LANGUAGE_PROFILES.get(lang, {}).get("elevenlabs_voice", "")
        if not api_key or not voice_id:
            return None

        # Build narration text from scene narrations
        narration = " ".join(
            sc.get("narration", "") for sc in script.get("scenes", [])
            if isinstance(sc, dict) and sc.get("narration")
        )[:2000]
        if not narration:
            return None

        url     = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        payload = json.dumps({
            "text":           narration,
            "model_id":       "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                audio_data = resp.read()
            out_dir  = HERE / "output" / "global" / lang
            out_dir.mkdir(parents=True, exist_ok=True)
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(out_dir / f"voice_{lang}_{ts}.mp3")
            Path(path).write_bytes(audio_data)
            return path
        except Exception as e:
            log.debug(f"  ElevenLabs voice synthesis failed for [{lang}]: {e}")
            return None
