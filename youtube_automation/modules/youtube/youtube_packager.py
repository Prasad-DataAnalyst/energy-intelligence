"""
modules/youtube/youtube_packager.py
Layer: YouTube SEO & Packaging
Safety: auto-heal-only
Description: Generates a complete YouTube upload package (title variants, description,
             tags, chapters, thumbnail prompts, Shorts segments, community post, pinned
             comment) from a CinematicScript and TrendOpportunity.  All limit values are
             read from config/master_config.yaml; channel identity is read from
             config/channel_persona.yaml.  Every sub-method is wrapped in try/except and
             returns a safe default on failure so the pipeline never blocks.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

# ── Interface imports ─────────────────────────────────────────────────────────
# Import from the canonical interface contracts so we stay consistent with the
# rest of the system.
from youtube_automation.interfaces.script_interface import CinematicScript
from youtube_automation.interfaces.trend_interface import TrendOpportunity

log = logging.getLogger("youtube.packager")

# ── Config paths ──────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_CONFIG_DIR = _HERE.parents[1] / "config"
_MASTER_CONFIG_PATH = _CONFIG_DIR / "master_config.yaml"
_PERSONA_PATH = _CONFIG_DIR / "channel_persona.yaml"


# ══════════════════════════════════════════════════════════════════════════════
# Config loader (module-level cache)
# ══════════════════════════════════════════════════════════════════════════════

def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file and return its contents as a dict.  Returns {} on error."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        log.warning("Could not load %s: %s", path, exc)
        return {}


_master: Dict[str, Any] = {}
_persona: Dict[str, Any] = {}


def _cfg() -> Dict[str, Any]:
    """Return the SEO section of master_config.yaml (lazy-loaded)."""
    global _master
    if not _master:
        _master = _load_yaml(_MASTER_CONFIG_PATH)
    return _master.get("seo", {})


def _chan() -> Dict[str, Any]:
    """Return channel persona dict (lazy-loaded)."""
    global _persona
    if not _persona:
        _persona = _load_yaml(_PERSONA_PATH)
    return _persona


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ts_to_seconds(ts: str) -> int:
    """Convert 'M:SS' or 'MM:SS' or 'H:MM:SS' timestamp string to integer seconds."""
    try:
        parts = ts.strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        pass
    return 0


def _seconds_to_ts(seconds: int) -> str:
    """Convert integer seconds to 'M:SS' string."""
    m, s = divmod(max(0, int(seconds)), 60)
    return f"{m}:{s:02d}"


def _slugify(text: str) -> str:
    """Lower-case alphanumeric with single spaces, no punctuation."""
    text = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _keyword_density(text: str, keyword: str) -> float:
    """Fraction of words in *text* that match any word in *keyword*."""
    words = _slugify(text).split()
    if not words:
        return 0.0
    kw_words = set(_slugify(keyword).split())
    matches = sum(1 for w in words if w in kw_words)
    return matches / len(words)


_EMOTIONAL_WORDS: Dict[str, float] = {
    "truth": 0.9, "secret": 0.85, "shocking": 0.9, "nobody": 0.8,
    "suddenly": 0.7, "why": 0.65, "facts": 0.6, "real": 0.6,
    "hidden": 0.85, "revealed": 0.8, "exposed": 0.9, "crisis": 0.9,
    "danger": 0.85, "warning": 0.8, "urgent": 0.75, "fear": 0.8,
    "money": 0.7, "free": 0.7, "change": 0.65, "discover": 0.6,
}


def _emotional_power(text: str) -> float:
    """Score 0–1 based on presence of high-emotion trigger words."""
    words = _slugify(text).split()
    if not words:
        return 0.0
    total = sum(_EMOTIONAL_WORDS.get(w, 0.0) for w in words)
    return min(1.0, total / max(len(words), 1) * 5)


def _score_title(title: str, keyword: str) -> float:
    """Composite CTR proxy: keyword density + emotional power + length + first-3 bonus."""
    max_len = _cfg().get("max_title_length", 70)
    length_score = 1.0 - max(0.0, len(title) - max_len) / max_len
    kd = _keyword_density(title, keyword)
    ep = _emotional_power(title)
    first_three = " ".join(title.lower().split()[:3])
    kw_first_bonus = 0.2 if (_slugify(keyword).split() or [""])[0] in first_three else 0.0
    return (kd * 0.35) + (ep * 0.40) + (length_score * 0.15) + kw_first_bonus


def _truncate(text: str, max_len: int) -> str:
    """Truncate *text* to *max_len* characters on a word boundary."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated.rstrip(".,;:")


def _topic_words(topic: str) -> List[str]:
    """Return meaningful (non-stop) words from *topic*, lower-cased, de-duped."""
    stop = {"a", "an", "the", "of", "in", "on", "at", "is", "are", "was",
            "and", "or", "for", "to", "with", "by", "as", "it", "its"}
    words = _slugify(topic).split()
    seen: List[str] = []
    for w in words:
        if w not in stop and w not in seen and len(w) > 2:
            seen.append(w)
    return seen


# ══════════════════════════════════════════════════════════════════════════════
# YouTubePackager
# ══════════════════════════════════════════════════════════════════════════════

class YouTubePackager:
    """Generates a complete YouTube upload package from a CinematicScript and
    TrendOpportunity.

    All limit values come from ``config/master_config.yaml`` (seo section).
    Channel branding comes from ``config/channel_persona.yaml``.
    Every public method is individually exception-safe and returns a sensible
    default when it cannot complete normally so the pipeline never blocks.
    """

    # ------------------------------------------------------------------
    # 1. SEO TITLE GENERATOR
    # ------------------------------------------------------------------

    def generate_titles(
        self,
        opportunity: TrendOpportunity,
        script: CinematicScript,
    ) -> List[str]:
        """Return 3 title variants sorted by composite CTR score (best first).

        Variant A — number list   : "5 [Topic] Facts That Will [Benefit]"
        Variant B — revelation    : "The Truth About [Topic] Nobody Is Saying"
        Variant C — question      : "Why Is [Topic] Suddenly [Consequence]?"

        Every title embeds the primary keyword from *opportunity.topic* and is
        capped at the ``seo.max_title_length`` value from master_config.yaml.
        """
        try:
            max_len = _cfg().get("max_title_length", 70)
            topic = opportunity.topic.strip()

            # Derive a short benefit phrase from the script hook sentence
            hook = script.hook_sentence or opportunity.hook_sentence or ""
            benefit_raw = re.split(r"[,—–-]", hook)[-1].strip()
            benefit = benefit_raw[:30].strip() if benefit_raw else "Change Everything"

            # Consequence phrase mapped from emotional trigger
            _trigger_consequence: Dict[str, str] = {
                "fear": "Going Wrong",
                "curiosity": "Changing Fast",
                "urgency": "Happening Now",
                "desire": "Going Viral",
                "social_proof": "Taking Over",
            }
            consequence = _trigger_consequence.get(
                opportunity.emotional_trigger, "Changing Everything"
            )

            variant_a = _truncate(f"5 {topic} Facts That Will {benefit}", max_len)
            variant_b = _truncate(f"The Truth About {topic} Nobody Is Saying", max_len)
            variant_c = _truncate(f"Why Is {topic} Suddenly {consequence}?", max_len)

            scored = sorted(
                [
                    (variant_a, _score_title(variant_a, topic)),
                    (variant_b, _score_title(variant_b, topic)),
                    (variant_c, _score_title(variant_c, topic)),
                ],
                key=lambda x: x[1],
                reverse=True,
            )
            return [t for t, _ in scored]

        except Exception as exc:
            log.error("generate_titles failed: %s", exc)
            topic = getattr(opportunity, "topic", "This Topic")
            return [
                f"The Truth About {topic} Nobody Is Saying",
                f"5 {topic} Facts That Will Change Everything",
                f"Why Is {topic} Suddenly Changing?",
            ]

    # ------------------------------------------------------------------
    # 2. SEO DESCRIPTION BUILDER
    # ------------------------------------------------------------------

    def generate_description(
        self,
        script: CinematicScript,
        opportunity: TrendOpportunity,
        chapters: List[Dict[str, str]],
    ) -> str:
        """Build a keyword-rich YouTube description following the canonical template.

        Structure
        ---------
        Lines 1-2  : Hook — primary keyword front-loaded, visible before fold.
        Lines 3-6  : Summary — 2-3 keyword-rich sentences.
        Divider    + CHAPTERS block auto-generated from *chapters*.
        Divider    + SOURCES block from opportunity.data_sources.
        Divider    + Channel footer (branding + CTA lines).
        Copyright line.
        Divider    + Up to seo.max_hashtags hashtags.
        """
        try:
            seo = _cfg()
            max_hashtags = seo.get("max_hashtags", 15)
            max_desc_len = seo.get("max_description_length", 5000)

            chan = _chan()
            channel_name = chan.get("channel", {}).get("name", "GetMindFuelNow")
            email = chan.get("channel", {}).get("email", "prasad2t@gmail.com")

            topic = opportunity.topic.strip()
            hook_sentence = script.hook_sentence or opportunity.hook_sentence or ""

            # Lines 1-2: hook — keyword must appear in first sentence
            hook_line = (
                f"{topic}: {hook_sentence}"
                if topic.lower() not in hook_sentence.lower()
                else hook_sentence
            )

            # Lines 3-6: summary
            key_facts = (opportunity.key_facts or [])[:2]
            supporting = (opportunity.supporting_stats or [])[:1]
            fact_text = " ".join(key_facts)
            stat_text = supporting[0] if supporting else ""
            summary = (
                f"In this video we break down everything you need to know about "
                f"{topic}. {fact_text} {stat_text}".strip()
            )

            divider = "\n━━━━━━━━━━━━━━━━━━━━━━"

            # CHAPTERS block
            chapter_lines = "\n".join(
                f"{ch['time']} {ch['title']}" for ch in chapters
            )

            # SOURCES block
            sources = opportunity.data_sources or opportunity.sources_used or []
            source_lines = (
                "\n".join(f"• {s}" for s in sources)
                if sources
                else "• See video for full citations."
            )

            # Footer
            footer = (
                f"📺 {channel_name} — Learn Something New Every Day\n"
                f"🔔 New video every day at 3 PM — subscribe & hit the bell\n"
                f"💬 Drop your biggest takeaway in the comments below\n"
                f"📌 Share with someone who needs to hear this"
            )

            copyright_line = f"© {channel_name} | {email}"

            # Hashtags
            hashtags = self._build_hashtags(opportunity, script, max_hashtags)
            hashtag_line = " ".join(f"#{h}" for h in hashtags)

            description = (
                f"{hook_line}\n\n"
                f"{summary}"
                f"{divider}\n"
                f"⏱ CHAPTERS\n"
                f"{chapter_lines}"
                f"{divider}\n"
                f"🔗 SOURCES & FURTHER READING\n"
                f"{source_lines}"
                f"{divider}\n"
                f"{footer}"
                f"{divider}\n"
                f"{copyright_line}\n"
                f"{divider}\n\n"
                f"{hashtag_line}"
            )

            if len(description) > max_desc_len:
                description = description[: max_desc_len - 3] + "..."

            return description

        except Exception as exc:
            log.error("generate_description failed: %s", exc)
            topic = getattr(opportunity, "topic", "this topic")
            return (
                f"{topic}: Everything you need to know.\n\n"
                f"© GetMindFuelNow | prasad2t@gmail.com"
            )

    def _build_hashtags(
        self,
        opportunity: TrendOpportunity,
        script: CinematicScript,
        max_hashtags: int,
    ) -> List[str]:
        """Build an ordered, deduplicated hashtag list (values without '#' prefix)."""
        seen: List[str] = []

        def _add(tag: str) -> None:
            slug = re.sub(r"[^\w]", "", tag.lower().replace(" ", ""))
            if slug and slug not in seen:
                seen.append(slug)

        for w in _topic_words(opportunity.topic):
            _add(w)
        _add(opportunity.topic.replace(" ", ""))
        if opportunity.category:
            _add(opportunity.category.replace(" ", ""))
        if opportunity.selected_angle and opportunity.selected_angle.angle_type:
            _add(opportunity.selected_angle.angle_type.replace("_", ""))
        for b in ["getmindfuelnow", "mindfuelnow", "mindful", "learneveryday"]:
            _add(b)
        if script.category:
            _add(script.category.replace(" ", ""))

        return seen[:max_hashtags]

    # ------------------------------------------------------------------
    # 3. TAGS GENERATOR
    # ------------------------------------------------------------------

    def generate_tags(
        self,
        opportunity: TrendOpportunity,
        script: CinematicScript,
    ) -> List[str]:
        """Generate up to 30 deduplicated tags within 500 total characters.

        Ordering: exact match keyword → phrase variations → related terms →
                  broad category → brand tags.
        """
        try:
            seo = _cfg()
            max_chars = seo.get("max_tags_characters", 500)
            max_tags = 30
            brand_tags = ["getmindfuelnow", "mindfuelnow", "mindful", "learneveryday"]

            topic = opportunity.topic.strip()
            topic_words = _topic_words(topic)

            candidates: List[str] = []

            def _add_tag(tag: str) -> None:
                clean = tag.strip().lower()
                if clean and clean not in candidates:
                    candidates.append(clean)

            # 1. Exact match
            _add_tag(topic)

            # 2. Phrase variations
            for w in topic_words:
                _add_tag(w)
            _add_tag(f"{topic} explained")
            _add_tag(f"{topic} 2024")
            _add_tag(f"{topic} facts")
            _add_tag(f"what is {topic}")
            _add_tag(f"{topic} news")
            _add_tag(f"{topic} today")

            # 3. Related terms from key facts
            for fact in (opportunity.key_facts or [])[:4]:
                words = _slugify(fact).split()
                for i, w in enumerate(words):
                    if len(w) > 3:
                        _add_tag(w)
                    if i + 1 < len(words) and len(w) > 3:
                        _add_tag(f"{w} {words[i + 1]}")

            if opportunity.emotional_trigger:
                _add_tag(opportunity.emotional_trigger)
            if opportunity.selected_angle and opportunity.selected_angle.angle_type:
                _add_tag(opportunity.selected_angle.angle_type.replace("_", " "))

            # 4. Broad category
            if opportunity.category:
                _add_tag(opportunity.category)
            if script.category:
                _add_tag(script.category)
            for broad in ["education", "learn", "documentary", "explainer", "science", "technology"]:
                _add_tag(broad)

            # 5. Brand tags
            for b in brand_tags:
                _add_tag(b)

            # Enforce max_tags and max_chars
            final: List[str] = []
            total_chars = 0
            for tag in candidates:
                needed = len(tag) + (2 if final else 0)  # account for ", " separator
                if len(final) >= max_tags or total_chars + needed > max_chars:
                    break
                final.append(tag)
                total_chars += needed

            return final

        except Exception as exc:
            log.error("generate_tags failed: %s", exc)
            topic = getattr(opportunity, "topic", "education")
            return [topic, "education", "getmindfuelnow", "learneveryday"]

    # ------------------------------------------------------------------
    # 4. CHAPTERS GENERATOR
    # ------------------------------------------------------------------

    def generate_chapters(self, script: CinematicScript) -> List[Dict[str, str]]:
        """Auto-generate keyword-rich chapter markers from scene timestamps.

        Always starts at 0:00.  Guarantees at least ``seo.min_chapters`` chapters
        (default 5).  Mandatory closing chapters: Key Takeaway + What To Do Now.
        """
        try:
            min_chapters = _cfg().get("min_chapters", 5)

            _section_title_map: Dict[str, str] = {
                "cold_open": "Introduction",
                "amplify_hook": "Why This Matters",
                "promise": "What You Will Learn",
                "value_loop": "Deep Dive",
                "climax": "The Shocking Truth",
                "cta": "What To Do Now",
            }

            chapters: List[Dict[str, str]] = []
            seen_sections: set = set()

            for scene in script.scenes:
                section = getattr(scene, "section", "value_loop") or "value_loop"
                ts = scene.timestamp_start or "0:00"
                start_sec = _ts_to_seconds(ts)

                # De-duplicate repeated value_loop sections
                if section in seen_sections and section == "value_loop":
                    continue
                seen_sections.add(section)

                base_title = _section_title_map.get(section, "Key Insights")

                # Enrich value_loop titles with hook_text when available
                hook_text = getattr(scene.text_overlay, "hook_text", "") or ""
                if section == "value_loop" and hook_text:
                    chapter_title = hook_text[:40].strip() or base_title
                else:
                    chapter_title = base_title

                chapters.append({"time": _seconds_to_ts(start_sec), "title": chapter_title})

            # Ensure we start at 0:00
            if not chapters or chapters[0]["time"] != "0:00":
                chapters.insert(0, {"time": "0:00", "title": "Introduction"})

            total_dur = script.total_duration_seconds or 480
            titles_lower = [c["title"].lower() for c in chapters]

            # Mandatory Key Takeaway chapter
            if not any("takeaway" in t or "truth" in t for t in titles_lower):
                chapters.append({
                    "time": _seconds_to_ts(int(total_dur * 0.80)),
                    "title": "Key Takeaway",
                })

            # Mandatory What To Do Now chapter
            if not any("do now" in t or "action" in t for t in titles_lower):
                chapters.append({
                    "time": _seconds_to_ts(int(total_dur * 0.90)),
                    "title": "What To Do Now",
                })

            # Sort and deduplicate by timestamp
            chapters.sort(key=lambda c: _ts_to_seconds(c["time"]))
            seen_times: set = set()
            deduped: List[Dict[str, str]] = []
            for ch in chapters:
                if ch["time"] not in seen_times:
                    deduped.append(ch)
                    seen_times.add(ch["time"])
            chapters = deduped

            # Pad to min_chapters if still short
            if len(chapters) < min_chapters:
                filler = [
                    "Background & Context",
                    "The Core Evidence",
                    "Expert Analysis",
                    "Hidden Implications",
                    "Breaking It Down",
                ]
                step = total_dur // (min_chapters + 1)
                idx = 0
                while len(chapters) < min_chapters and idx < len(filler):
                    ts_str = _seconds_to_ts(step * (idx + 1))
                    if ts_str not in seen_times:
                        chapters.append({"time": ts_str, "title": filler[idx]})
                        seen_times.add(ts_str)
                    idx += 1
                chapters.sort(key=lambda c: _ts_to_seconds(c["time"]))

            return chapters

        except Exception as exc:
            log.error("generate_chapters failed: %s", exc)
            return [
                {"time": "0:00", "title": "Introduction"},
                {"time": "1:30", "title": "Background & Context"},
                {"time": "3:00", "title": "Deep Dive"},
                {"time": "6:00", "title": "Key Takeaway"},
                {"time": "7:30", "title": "What To Do Now"},
            ]

    # ------------------------------------------------------------------
    # 5. THUMBNAIL PROMPT GENERATOR
    # ------------------------------------------------------------------

    def generate_thumbnail_prompts(
        self,
        opportunity: TrendOpportunity,
        script: CinematicScript,
    ) -> List[Dict[str, Any]]:
        """Return 3 A/B/C thumbnail variant dicts for SDXL generation at 1280×720.

        Variant A: Face + text — highest CTR formula.
        Variant B: Data/statistic visual + text.
        Variant C: Before/after split-screen concept.

        Color psychology applied:
          Red/Yellow → urgency/fear   Blue/White → trust/curiosity
          Orange/Black → energy       Green → growth
        """
        try:
            seo = _cfg()
            w = seo.get("thumbnail_width", 1280)
            h = seo.get("thumbnail_height", 720)

            topic = opportunity.topic.strip()
            tw = _topic_words(topic)
            short_topic = " ".join(tw[:2]).upper() if tw else topic.upper()[:20]

            _trigger_word_map: Dict[str, str] = {
                "fear": "SHOCKING",
                "curiosity": "REVEALED",
                "urgency": "URGENT",
                "desire": "DISCOVER",
                "social_proof": "PROVEN",
            }
            trigger_word = _trigger_word_map.get(opportunity.emotional_trigger, "SHOCKING")

            _color_map: Dict[str, str] = {
                "fear": "red_yellow",
                "urgency": "red_yellow",
                "curiosity": "blue_white",
                "social_proof": "blue_white",
                "desire": "orange_black",
            }
            color_scheme = _color_map.get(opportunity.emotional_trigger, "red_yellow")
            visual_style = opportunity.recommended_visual_style or "tech_dark"

            first_fact = (
                opportunity.key_facts[0]
                if opportunity.key_facts
                else f"{topic} is changing"
            )
            first_stat = (
                opportunity.supporting_stats[0]
                if opportunity.supporting_stats
                else "New data just released"
            )

            # ── Variant A: Face + text ─────────────────────────────────────
            overlay_a = _truncate(f"{trigger_word}: {short_topic}", 30)
            variant_a: Dict[str, Any] = {
                "variant": "A",
                "image_prompt": (
                    f"Cinematic {w}x{h} YouTube thumbnail, {visual_style} style. "
                    f"Center-frame extreme close-up of a shocked human face: wide eyes, open mouth, "
                    f"expressing alarm and urgency. Background: dark gradient with dramatic cinematic "
                    f"lighting and subtle {topic} iconography. High contrast, ultra-sharp, photorealistic, "
                    f"8K quality, cinematic bokeh, deep dramatic shadows. No text rendered in image."
                ),
                "text_overlay": overlay_a,
                "color_scheme": color_scheme,
                "emotion": "shock" if opportunity.emotional_trigger in ("fear", "urgency") else "curiosity",
                "background": f"Dark cinematic gradient with {topic} thematic elements",
                "subject": "Shocked human face, center-frame extreme close-up",
            }

            # ── Variant B: Data / statistic visual + text ─────────────────
            overlay_b = _truncate(f"THE TRUTH: {short_topic}", 30)
            variant_b: Dict[str, Any] = {
                "variant": "B",
                "image_prompt": (
                    f"Cinematic {w}x{h} YouTube thumbnail, data-visualization style. "
                    f"Dramatic infographic representing '{first_stat}': bold chart or graph with glowing "
                    f"data lines on near-black background. Topic: {topic}. High-contrast neon data streams, "
                    f"holographic overlays, cinematic depth of field. Color palette: electric blue and white. "
                    f"Ultra-sharp, 8K quality. No text rendered in image."
                ),
                "text_overlay": overlay_b,
                "color_scheme": "blue_white",
                "emotion": "curiosity",
                "background": "Dark tech background with glowing neon data visualization",
                "subject": f"Bold data chart or infographic: {first_fact[:60]}",
            }

            # ── Variant C: Before/after split-screen ──────────────────────
            overlay_c = _truncate(f"BEFORE VS AFTER: {short_topic}", 30)
            variant_c: Dict[str, Any] = {
                "variant": "C",
                "image_prompt": (
                    f"Cinematic {w}x{h} YouTube thumbnail, split-screen composition. "
                    f"LEFT half (desaturated, slightly blurred): the problematic state before knowing "
                    f"about {topic}. RIGHT half (vibrant, sharp, high-saturation): the transformed state "
                    f"after understanding {topic}. Bold vertical dividing line at center. "
                    f"Dramatic cinematic lighting, orange and black color palette. Ultra-sharp, 8K quality. "
                    f"No text rendered in image."
                ),
                "text_overlay": overlay_c,
                "color_scheme": "orange_black",
                "emotion": "urgency",
                "background": "Split-screen before/after contrast composition",
                "subject": f"Before vs after transformation related to {topic}",
            }

            return [variant_a, variant_b, variant_c]

        except Exception as exc:
            log.error("generate_thumbnail_prompts failed: %s", exc)
            topic = getattr(opportunity, "topic", "this topic")
            return [
                {
                    "variant": "A",
                    "image_prompt": f"Shocked face cinematic thumbnail about {topic}",
                    "text_overlay": "SHOCKING TRUTH",
                    "color_scheme": "red_yellow",
                    "emotion": "shock",
                    "background": "Dark cinematic gradient",
                    "subject": "Shocked human face",
                },
                {
                    "variant": "B",
                    "image_prompt": f"Data visualization cinematic thumbnail about {topic}",
                    "text_overlay": "THE REAL DATA",
                    "color_scheme": "blue_white",
                    "emotion": "curiosity",
                    "background": "Dark tech background",
                    "subject": "Data chart visualization",
                },
                {
                    "variant": "C",
                    "image_prompt": f"Before/after split-screen thumbnail about {topic}",
                    "text_overlay": "BEFORE VS AFTER",
                    "color_scheme": "orange_black",
                    "emotion": "urgency",
                    "background": "Split-screen composition",
                    "subject": "Before/after transformation",
                },
            ]

    # ------------------------------------------------------------------
    # 6. SHORTS SEGMENT IDENTIFIER
    # ------------------------------------------------------------------

    def identify_shorts_segments(
        self, script: CinematicScript
    ) -> List[Dict[str, Any]]:
        """Identify 3-5 non-overlapping Shorts segments of 28–55 seconds each.

        Preference order (by scene psychological trigger + retention device score):
          most shocking → best data reveal → strongest emotional beat →
          most shareable quote → best visual moment.
        """
        try:
            chan = _chan()
            seo = _cfg()
            min_dur = chan.get("shorts", {}).get("min_duration_seconds", 28)
            max_dur = chan.get("shorts", {}).get("max_duration_seconds", 55)
            target_count = seo.get("shorts_count", 3)

            _trigger_scores: Dict[str, float] = {
                "fear": 1.0, "urgency": 0.95, "curiosity": 0.85,
                "desire": 0.7, "social_proof": 0.6,
            }
            _retention_scores: Dict[str, float] = {
                "shocking_reveal": 1.0, "pattern_interrupt": 0.9,
                "open_loop": 0.85, "tease_next": 0.75, "none": 0.4,
            }
            _trigger_rationale: Dict[str, str] = {
                "fear": "High fear trigger creates immediate engagement and share impulse",
                "curiosity": "Opens a curiosity gap that viewers must resolve by watching",
                "urgency": "Time-sensitive framing drives rapid shares and saves",
                "desire": "Aspirational content performs well in feeds",
                "social_proof": "Social validation cues drive comments and reshares",
            }
            _section_rationale: Dict[str, str] = {
                "cold_open": "Punchy hook scene with strong first-3-word stop-scroll potential",
                "amplify_hook": "Amplification beat that deepens urgency before the main reveal",
                "climax": "Climactic reveal — the most shareable moment in the video",
                "value_loop": "Core data reveal that standalone teaches and surprises",
                "cta": "Action-driven close with clear next step — drives conversions",
            }

            scored: List[tuple] = []
            for scene in script.scenes:
                trig = getattr(scene, "psychological_trigger", "curiosity") or "curiosity"
                ret = getattr(scene, "retention_device", "none") or "none"
                dur = scene.duration_seconds or 0
                score = (
                    _trigger_scores.get(trig, 0.5) * 0.5
                    + _retention_scores.get(ret, 0.4) * 0.3
                    + (0.2 if min_dur <= dur <= max_dur else 0.0)
                )
                scored.append((score, scene))

            scored.sort(key=lambda x: x[0], reverse=True)

            used_ranges: List[tuple] = []
            segments: List[Dict[str, Any]] = []
            segment_id = 1

            for score, scene in scored:
                if len(segments) >= target_count + 2:
                    break

                start_sec = _ts_to_seconds(scene.timestamp_start)
                end_sec = _ts_to_seconds(scene.timestamp_end)
                dur = end_sec - start_sec

                # Clamp duration to valid Shorts range
                if dur < min_dur:
                    end_sec = start_sec + min_dur
                elif dur > max_dur:
                    end_sec = start_sec + max_dur

                # Skip if overlaps an already selected segment
                if any(not (end_sec <= r[0] or start_sec >= r[1]) for r in used_ranges):
                    continue

                used_ranges.append((start_sec, end_sec))

                voiceover = (scene.audio.voiceover if scene.audio else "") or ""
                hook_words = voiceover.split()[:3]
                hook_text = (
                    " ".join(hook_words)
                    if hook_words
                    else (
                        (scene.text_overlay.hook_text[:20] if scene.text_overlay else "")
                        or "Watch this moment"
                    )
                )

                caption_text = (
                    (scene.text_overlay.hook_text if scene.text_overlay and scene.text_overlay.hook_text else "")
                    or voiceover[:80].strip()
                    or f"You need to know this about {script.title}"
                )

                trig = getattr(scene, "psychological_trigger", "curiosity") or "curiosity"
                section = getattr(scene, "section", "value_loop") or "value_loop"
                rationale = _trigger_rationale.get(trig, "Strong engagement potential")
                section_note = _section_rationale.get(section, "")
                if section_note:
                    rationale = f"{section_note}. {rationale}"

                segments.append({
                    "segment_id": segment_id,
                    "start_seconds": start_sec,
                    "end_seconds": end_sec,
                    "hook_text": hook_text,
                    "caption_text": caption_text[:150],
                    "rationale": rationale,
                })
                segment_id += 1

            # Synthesize additional segments if not enough found
            if len(segments) < 3:
                total_dur = script.total_duration_seconds or 480
                synthetic = [
                    (int(total_dur * 0.05), "The key insight everyone misses"),
                    (int(total_dur * 0.35), "The data that changes everything"),
                    (int(total_dur * 0.65), "What experts won't tell you"),
                    (int(total_dur * 0.80), "The most important takeaway"),
                ]
                for start, caption in synthetic:
                    if len(segments) >= target_count + 2:
                        break
                    end = start + 42  # 42 s sweet-spot
                    if any(not (end <= r[0] or start >= r[1]) for r in used_ranges):
                        continue
                    used_ranges.append((start, end))
                    segments.append({
                        "segment_id": segment_id,
                        "start_seconds": start,
                        "end_seconds": end,
                        "hook_text": caption[:20],
                        "caption_text": caption,
                        "rationale": "Synthesized segment from high-value video section",
                    })
                    segment_id += 1

            return segments[: target_count + 2]  # up to 5 max

        except Exception as exc:
            log.error("identify_shorts_segments failed: %s", exc)
            return [
                {
                    "segment_id": 1,
                    "start_seconds": 30,
                    "end_seconds": 72,
                    "hook_text": "Nobody talks about",
                    "caption_text": "The truth nobody is talking about",
                    "rationale": "Default fallback: strong open-loop hook",
                },
                {
                    "segment_id": 2,
                    "start_seconds": 180,
                    "end_seconds": 222,
                    "hook_text": "The data shows",
                    "caption_text": "What the data actually reveals",
                    "rationale": "Default fallback: data-driven credibility",
                },
                {
                    "segment_id": 3,
                    "start_seconds": 390,
                    "end_seconds": 432,
                    "hook_text": "Here is what",
                    "caption_text": "Here is what you should do next",
                    "rationale": "Default fallback: action-driving close",
                },
            ]

    # ------------------------------------------------------------------
    # 7. COMMUNITY POST GENERATOR
    # ------------------------------------------------------------------

    def generate_community_post(
        self,
        opportunity: TrendOpportunity,
        script: CinematicScript,
    ) -> str:
        """Generate a mobile-readable community post (150-300 characters ideal).

        Posted within ``seo.community_post_delay_minutes`` (default 60) of upload.
        Ends with [VIDEO_LINK] placeholder.  2-3 emoji max.
        """
        try:
            topic = opportunity.topic.strip()

            # Pull the engagement question from the CTA scene voiceover
            cta_scene = next(
                (s for s in script.scenes if getattr(s, "section", "") == "cta"),
                None,
            )
            engagement_question = ""
            if cta_scene and cta_scene.audio:
                sentences = re.split(r"(?<=[.!?])\s+", cta_scene.audio.voiceover or "")
                for sent in sentences:
                    if sent.strip().endswith("?"):
                        engagement_question = sent.strip()
                        break

            if not engagement_question:
                _trigger_questions: Dict[str, str] = {
                    "fear": f"What worries you most about {topic}?",
                    "curiosity": f"What surprised you most about {topic}?",
                    "urgency": f"Are you prepared for what is happening with {topic}?",
                    "desire": f"What would you do differently knowing this about {topic}?",
                    "social_proof": f"Have you seen what is happening with {topic}?",
                }
                engagement_question = _trigger_questions.get(
                    opportunity.emotional_trigger,
                    f"What do you think about {topic}?",
                )

            _emoji_map: Dict[str, str] = {
                "fear": "⚠️",
                "curiosity": "🔍",
                "urgency": "🚨",
                "desire": "💡",
                "social_proof": "📊",
            }
            emoji = _emoji_map.get(opportunity.emotional_trigger, "💡")

            post = (
                f"{emoji} New video just dropped!\n\n"
                f"{topic} — and what we found will surprise you.\n\n"
                f"{engagement_question}\n\n"
                f"Watch now 👇\n"
                f"[VIDEO_LINK]"
            )
            return post

        except Exception as exc:
            log.error("generate_community_post failed: %s", exc)
            topic = getattr(opportunity, "topic", "this topic")
            return (
                f"💡 New video just dropped!\n\n"
                f"Everything you need to know about {topic}.\n\n"
                f"What do you think? Watch now 👇\n"
                f"[VIDEO_LINK]"
            )

    # ------------------------------------------------------------------
    # 8. PINNED COMMENT GENERATOR
    # ------------------------------------------------------------------

    def generate_pinned_comment(
        self,
        opportunity: TrendOpportunity,
        script: CinematicScript,
    ) -> str:
        """Generate a debate-starting pinned comment (100-200 characters, no emojis).

        Posted within ``seo.pinned_comment_delay_minutes`` (default 5) of going public.
        No emojis — looks more authentic and encourages real replies.
        """
        try:
            topic = opportunity.topic.strip()

            debate_templates: List[str] = [
                f"Honest question: did you already know about {topic}, or was this new to you? Drop your answer below.",
                f"What is the one thing about {topic} that most people still do not understand?",
                f"After watching — do you think {topic} is as serious as the data suggests? Yes or no?",
                f"Before this video, what did you believe about {topic}? Has anything changed for you?",
                f"Quick poll: does {topic} affect your daily life directly? Comment and let others know.",
            ]

            idx = int(opportunity.opportunity_score * len(debate_templates)) % len(debate_templates)
            comment = debate_templates[idx]

            if len(comment) > 200:
                comment = comment[:197] + "..."

            return comment

        except Exception as exc:
            log.error("generate_pinned_comment failed: %s", exc)
            topic = getattr(opportunity, "topic", "this topic")
            return (
                f"Did you already know about {topic}, or was this new to you? "
                f"Drop your answer below."
            )

    # ------------------------------------------------------------------
    # MAIN ENTRY POINT
    # ------------------------------------------------------------------

    def generate_package(
        self,
        script: CinematicScript,
        opportunity: TrendOpportunity,
        video_path: str,
    ) -> Dict[str, Any]:
        """Generate and return the complete YouTube upload package.

        Parameters
        ----------
        script:
            Fully rendered CinematicScript from the script writer module.
        opportunity:
            TrendOpportunity selected by the intelligence module.
        video_path:
            Absolute path to the final rendered video file.

        Returns
        -------
        dict with keys:
            titles, selected_title, description, tags, chapters,
            thumbnail_prompts, shorts_segments, community_post,
            pinned_comment, keyword_primary, rpm_category, category_id.
        """
        log.info("Generating YouTube package for topic: %s", opportunity.topic)

        titles = self.generate_titles(opportunity, script)
        selected_title = titles[0] if titles else (script.title or opportunity.topic)

        chapters = self.generate_chapters(script)
        description = self.generate_description(script, opportunity, chapters)
        tags = self.generate_tags(opportunity, script)
        thumbnail_prompts = self.generate_thumbnail_prompts(opportunity, script)
        shorts_segments = self.identify_shorts_segments(script)
        community_post = self.generate_community_post(opportunity, script)
        pinned_comment = self.generate_pinned_comment(opportunity, script)

        # YouTube category ID lookup
        # Reference: https://developers.google.com/youtube/v3/docs/videoCategories/list
        _category_id_map: Dict[str, str] = {
            "education": "27",
            "science": "27",
            "history": "27",
            "psychology": "27",
            "finance": "27",
            "health": "26",
            "howto": "26",
            "technology": "28",
            "science & technology": "28",
            "ai": "28",
            "entertainment": "24",
            "news": "25",
            "true crime": "25",
            "geopolitics": "25",
            "gaming": "20",
        }
        cat_key = (opportunity.category or script.category or "education").lower().strip()
        category_id = _category_id_map.get(cat_key, "27")  # default: Education

        package: Dict[str, Any] = {
            "titles": titles,
            "selected_title": selected_title,
            "description": description,
            "tags": tags,
            "chapters": chapters,
            "thumbnail_prompts": thumbnail_prompts,
            "shorts_segments": shorts_segments,
            "community_post": community_post,
            "pinned_comment": pinned_comment,
            "keyword_primary": opportunity.topic,
            "rpm_category": opportunity.rpm_category,
            "category_id": category_id,
        }

        log.info(
            "Package ready — title: %r | tags: %d | chapters: %d | shorts: %d",
            selected_title,
            len(tags),
            len(chapters),
            len(shorts_segments),
        )
        return package
