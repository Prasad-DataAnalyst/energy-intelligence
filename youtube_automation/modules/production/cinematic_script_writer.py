"""
modules/production/cinematic_script_writer.py
Layer: Production
Safety: auto-heal-only
Description: Cinematic screenplay engine for GetMindFuelNow YouTube automation.
             Generates full 8-minute (480s) documentary-style scripts following
             a mandatory structure: cold open -> amplify hook -> promise ->
             value delivery loops -> emotional climax -> CTA.
             Uses Claude Haiku when ANTHROPIC_API_KEY is set; falls back to
             procedural local generation otherwise.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from youtube_automation.interfaces.script_interface import (
    CinematicScript,
    Scene,
    SceneAudio,
    SceneText,
    SceneVisual,
)
from youtube_automation.interfaces.trend_interface import TrendOpportunity

log = logging.getLogger("cinematic.script")

# ---------------------------------------------------------------------------
# Constants derived from config (mirrors master_config.yaml / channel_persona.yaml)
# ---------------------------------------------------------------------------

FORBIDDEN_OPENERS: Tuple[str, ...] = (
    "hey", "welcome", "today", "in this video", "hi", "hello",
    "what's up", "guys", "so",
)

WORDS_PER_MINUTE: int = 160

# Section timing (seconds) from master_config.yaml
_COLD_OPEN_S        = 8
_AMPLIFY_HOOK_S     = 12
_PROMISE_S          = 25
_VALUE_LOOP_TOTAL_S = 255   # 0:45 -> 5:00
_CLIMAX_S           = 120   # 5:00 -> 7:00
_CTA_S              = 60    # 7:00 -> 8:00

# Value loop structure (~75 s x 3-4 loops)
_LOOP_MINI_HOOK_S   = 10
_LOOP_TENSION_S     = 20
_LOOP_DATA_S        = 25
_LOOP_RESOLUTION_S  = 20
_LOOP_TOTAL_S       = _LOOP_MINI_HOOK_S + _LOOP_TENSION_S + _LOOP_DATA_S + _LOOP_RESOLUTION_S  # 75

PATTERN_INTERRUPT_INTERVAL_S: int = 47
MIN_PATTERN_INTERRUPTS: int = 6
MIN_DATA_VIZ_SCENES: int = 3
MIN_PSYCHOLOGICAL_SCORE: int = 80

# Visual type rotation pool
_VISUAL_TYPES = [
    "title_card",
    "data_visualization",
    "ai_image_motion",
    "stock_footage",
    "ai_video",
]

# Camera movement rotation
_CAMERA_MOVES = ["push_in", "pull_out", "handheld", "crane_up", "orbit"]

# Cinematic documentary phrase library
_MINI_HOOK_PHRASES = [
    "Here's what the data actually shows.",
    "What nobody talks about is this.",
    "The part that changes everything comes next.",
    "Most people stop here -- and that's the mistake.",
    "The numbers behind this will reframe everything.",
    "This is the piece the headlines always skip.",
    "Here's where the story gets uncomfortable.",
    "And then something unexpected happened.",
]

_TENSION_PHRASES = [
    "The conventional explanation falls apart the moment you look at the numbers.",
    "For decades the same assumption has driven every decision in this space.",
    "Experts were confidently pointing in exactly the wrong direction.",
    "The gap between what people believe and what the evidence shows is staggering.",
    "Every model, every forecast, every projection -- all built on a single flaw.",
    "The pressure had been building for years before anyone noticed the crack.",
    "The standard playbook was written for a world that no longer exists.",
]

_DATA_REVEAL_PHRASES = [
    "The research makes it impossible to ignore:",
    "What the data reveals is this:",
    "Independent analysis points to one conclusion:",
    "The numbers are unambiguous:",
    "Three separate datasets converge on the same answer:",
    "The evidence, when laid out in full, tells a clear story:",
]

_RESOLUTION_PHRASES = [
    "And that changes how you have to think about everything that follows.",
    "Once you see that, the rest of the picture snaps into focus.",
    "That single shift explains more than a decade of confusion.",
    "The implication is significant -- and we're only halfway through.",
    "Hold that thought. Because the next layer goes deeper.",
]

# Power words for emphasis extraction
_POWER_WORDS = {
    "never", "secret", "truth", "real", "actual", "proven", "billion",
    "trillion", "million", "percent", "warning", "critical", "urgent",
    "shocking", "hidden", "exposed", "collapse", "surge", "crisis",
    "record", "unprecedented", "silent", "revealed", "finally",
}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _seconds_to_mmss(s: int) -> str:
    """Convert integer seconds to 'MM:SS' string."""
    return f"{s // 60:02d}:{s % 60:02d}"


def _words_for_duration(duration_s: int, wpm: int = WORDS_PER_MINUTE) -> int:
    """Return approximate word count for a given duration."""
    return max(1, int(duration_s * wpm / 60))


def _extract_emphasis(text: str) -> List[str]:
    """Return up to 4 emphasis words: numbers and power words."""
    result: List[str] = []
    for raw in text.split():
        word = raw.lower().strip(".,!?\"'")
        if word in _POWER_WORDS or re.sub(r"[,.]", "", word).isdigit():
            clean = raw.strip(".,!?\"'")
            if clean not in result:
                result.append(clean)
        if len(result) >= 4:
            break
    return result


def _pick(pool: List[str], index: int) -> str:
    """Deterministically pick from a list by cycling index."""
    return pool[index % len(pool)]


def _make_visual(
    index: int,
    description: str,
    style: str = "cinematic",
    color_grade: str = "tech_dark",
    vfx: str = "none",
    force_type: Optional[str] = None,
) -> SceneVisual:
    vtype = force_type if force_type else _pick(_VISUAL_TYPES, index)
    cam   = _pick(_CAMERA_MOVES, index)
    return SceneVisual(
        type=vtype,
        description=description,
        camera_movement=cam,
        style=style,
        color_grade=color_grade,
        vfx_overlay=vfx,
    )


def _make_audio(
    voiceover: str,
    tone: str = "authoritative",
    pacing: str = "medium",
    music_energy: str = "low",
    sfx: Optional[List[str]] = None,
) -> SceneAudio:
    return SceneAudio(
        voiceover=voiceover,
        tone=tone,
        pacing=pacing,
        emphasis_words=_extract_emphasis(voiceover),
        music_energy=music_energy,
        sfx=sfx or [],
    )


def _make_text(
    hook_text: str = "",
    caption_style: str = "word_highlight",
    source_credit: str = "",
    animation_type: str = "slam",
) -> SceneText:
    return SceneText(
        hook_text=hook_text,
        caption_style=caption_style,
        source_credit=source_credit,
        animation_type=animation_type,
    )


# ---------------------------------------------------------------------------
# CinematicScriptWriter
# ---------------------------------------------------------------------------

class CinematicScriptWriter:
    """
    Generates full 8-minute cinematic YouTube scripts for GetMindFuelNow.

    Public API
    ----------
    generate(opportunity)      -> CinematicScript
    score_script(script)       -> float
    """

    def __init__(self) -> None:
        self._api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
        self._client: Any = None
        if self._api_key:
            try:
                import anthropic  # type: ignore
                self._client = anthropic.Anthropic()
                log.debug("Anthropic client initialised for CinematicScriptWriter")
            except Exception as exc:
                log.warning("Could not initialise Anthropic client: %s", exc)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def generate(self, opportunity: TrendOpportunity) -> CinematicScript:
        """
        Generate a full cinematic script from a TrendOpportunity.

        Attempts Claude Haiku first; falls back to local procedural generation
        on any failure.
        """
        if self._client:
            try:
                script = self._generate_with_claude(opportunity)
                script.quality_score = self.score_script(script)
                return script
            except Exception as exc:
                log.warning(
                    "Claude script generation failed (%s) -- falling back to local", exc
                )

        script = self._generate_local(opportunity)
        script.quality_score = self.score_script(script)
        return script

    def score_script(self, script: CinematicScript) -> float:
        """
        Apply 9 quality gates and return a combined score 0-100.

        Gates
        -----
        1.  First word not on forbidden list
        2.  Hook sentence creates curiosity
        3.  Minimum 6 pattern interrupts
        4.  Every 60 s has a different visual type
        5.  Emotional arc complete (Shock->Curiosity->Clarity->Revelation->Action)
        6.  Every factual claim has a cited source
        7.  Minimum 3 data-visualization scenes
        8.  No bullet-point lecture structure (no "Number 1 / Number 2")
        9.  Psychological score > 80/100
        """
        total = 100.0
        deductions: List[Tuple[str, float]] = []

        scenes = script.scenes
        all_vo = " ".join(s.audio.voiceover for s in scenes)
        first_word = all_vo.split()[0].lower().rstrip(".,!?") if all_vo.split() else ""

        # Gate 1 -- forbidden opener
        if any(first_word == fo.split()[0].lower() for fo in FORBIDDEN_OPENERS):
            deductions.append(("forbidden_opener", 15.0))
        else:
            script.first_word_valid = True

        # Gate 2 -- hook curiosity
        hook = script.hook_sentence.lower()
        curiosity_signals = [
            "?", "never", "secret", "truth", "real", "hidden",
            "nobody", "what happens", "why", "the real", "actually",
        ]
        if not any(sig in hook for sig in curiosity_signals):
            deductions.append(("weak_hook", 10.0))

        # Gate 3 -- pattern interrupts
        pi_scenes = [s for s in scenes if s.retention_device == "pattern_interrupt"]
        script.pattern_interrupt_count = len(pi_scenes)
        if script.pattern_interrupt_count < MIN_PATTERN_INTERRUPTS:
            shortage = MIN_PATTERN_INTERRUPTS - script.pattern_interrupt_count
            deductions.append(("insufficient_pattern_interrupts", shortage * 2.0))

        # Gate 4 -- visual variety across 60-second windows
        # Parse timestamps as total seconds for window bucketing
        def ts_to_s(ts: str) -> int:
            parts = ts.split(":")
            return int(parts[0]) * 60 + int(parts[1])

        max_window = math.ceil(script.total_duration_seconds / 60)
        failed_windows = 0
        for w in range(max_window):
            w_start = w * 60
            w_end   = w_start + 60
            w_scenes = [
                s for s in scenes
                if w_start <= ts_to_s(s.timestamp_start) < w_end
            ]
            vtypes = {s.visual.type for s in w_scenes}
            if len(vtypes) < 2 and len(w_scenes) > 1:
                failed_windows += 1
        if failed_windows > 0:
            deductions.append(("low_visual_variety", failed_windows * 1.5))

        # Gate 5 -- emotional arc
        section_order = [s.section for s in scenes]
        arc_sections = ["cold_open", "amplify_hook", "promise", "value_loop", "climax", "cta"]
        arc_present = all(sec in section_order for sec in arc_sections)
        script.emotional_arc_complete = arc_present
        if not arc_present:
            deductions.append(("incomplete_emotional_arc", 10.0))

        # Gate 6 -- sources cited
        factual_scenes = [
            s for s in scenes
            if s.factual_source and s.factual_source not in ("", "none")
        ]
        all_sourced = len(factual_scenes) >= max(1, len(scenes) // 3)
        script.all_sources_cited = all_sourced
        if not all_sourced:
            deductions.append(("missing_sources", 8.0))

        # Gate 7 -- data viz scenes
        dv_scenes = [s for s in scenes if s.visual.type == "data_visualization"]
        script.data_viz_scene_count = len(dv_scenes)
        if script.data_viz_scene_count < MIN_DATA_VIZ_SCENES:
            shortage = MIN_DATA_VIZ_SCENES - script.data_viz_scene_count
            deductions.append(("insufficient_data_viz", shortage * 3.0))

        # Gate 8 -- no bullet-point lecture
        lecture_patterns = re.compile(
            r"\b(number\s+\d|point\s+\d|reason\s+\d|\#\d)\b", re.IGNORECASE
        )
        if lecture_patterns.search(all_vo):
            deductions.append(("lecture_structure", 8.0))

        # Gate 9 -- psychological score
        psych = self._calc_psychological_score(script)
        script.psychological_score = psych
        if psych < MIN_PSYCHOLOGICAL_SCORE:
            deficit = (MIN_PSYCHOLOGICAL_SCORE - psych) / 10.0
            deductions.append(("low_psych_score", deficit))

        penalty = sum(d for _, d in deductions)
        final_score = max(0.0, total - penalty)

        if deductions:
            log.debug(
                "score_script deductions: %s -> final %.1f",
                deductions,
                final_score,
            )

        return round(final_score, 1)

    # ------------------------------------------------------------------
    # Private -- Claude Haiku generation
    # ------------------------------------------------------------------

    def _generate_with_claude(self, opportunity: TrendOpportunity) -> CinematicScript:
        """
        Ask Claude Haiku to generate a full scene-by-scene screenplay and
        parse the JSON response into CinematicScript dataclasses.
        """
        facts_block  = "\n".join(f"- {f}" for f in opportunity.key_facts[:8])
        stats_block  = "\n".join(f"- {s}" for s in opportunity.supporting_stats[:6])
        sources_block = "\n".join(opportunity.data_sources[:6])

        prompt = (
            "You are the head writer for GetMindFuelNow, a Netflix-documentary-style YouTube channel.\n"
            "Generate a complete 8-minute (480 second) cinematic script for the following topic.\n\n"
            f"TOPIC: {opportunity.topic}\n"
            f"CATEGORY: {opportunity.category}\n"
            f"HOOK SENTENCE: {opportunity.hook_sentence}\n"
            f"SELECTED ANGLE: {opportunity.selected_angle.angle_type} -- {opportunity.selected_angle.title}\n"
            f"EMOTIONAL TRIGGER: {opportunity.emotional_trigger}\n\n"
            f"KEY FACTS:\n{facts_block}\n\n"
            f"SUPPORTING STATS:\n{stats_block}\n\n"
            f"SOURCES:\n{sources_block}\n\n"
            "FORBIDDEN FIRST WORDS (NEVER start with these): "
            "Hey, Welcome, Today, In this video, Hi, Hello, What's up, Guys, So\n\n"
            "MANDATORY STRUCTURE (total 480 seconds):\n"
            "Scene 1  [00:00-00:08]  COLD OPEN -- Most shocking statement first. NO forbidden openers. section=cold_open\n"
            "Scene 2  [00:08-00:20]  AMPLIFY HOOK -- ONE powerful fact building curiosity. section=amplify_hook\n"
            "Scene 3  [00:20-00:45]  THE PROMISE -- What viewer will understand by end. section=promise\n"
            "Scenes 4-15 [00:45-05:00] VALUE LOOPS (3-4 loops x75s, "
            "each: mini_hook 10s + tension 20s + data_reveal 25s + resolution 20s). section=value_loop\n"
            "Scene 16 [05:00-07:00]  EMOTIONAL CLIMAX -- Biggest revelation. section=climax\n"
            "Scene 17 [07:00-08:00]  CTA -- Engagement question + subscribe reason as value + open loop. section=cta\n\n"
            "RULES:\n"
            "- Voice: confident, calm, slightly urgent -- never shouty. Persona: brilliant friend who read everything.\n"
            "- NO numbered lecture format ('Number 1', 'Point 2'). Flowing cinematic documentary narrative.\n"
            "- Use phrases like: 'Here's what the data shows', "
            "'What nobody talks about is', 'The part that changes everything'\n"
            "- Every scene must have factual_source (real URL or citation)\n"
            "- psychological_trigger: one of curiosity|fear|desire|urgency|social_proof\n"
            "- retention_device: one of open_loop|pattern_interrupt|tease_next|shocking_reveal|none\n"
            "- Minimum 6 pattern_interrupt retention devices across entire script\n"
            "- visual type rotates: title_card, data_visualization, ai_image_motion, stock_footage, ai_video\n"
            "- camera_movement rotates: push_in, pull_out, handheld, crane_up, orbit\n"
            "- style: cinematic|documentary|thriller|epic\n"
            "- vfx_overlay: particles|light_rays|glitch|neural_network|data_stream|none\n\n"
            'Return ONLY a valid JSON object (no markdown, no commentary):\n'
            '{\n'
            '  "title": "video title (under 70 chars)",\n'
            '  "hook_sentence": "the cold open first sentence",\n'
            '  "scenes": [\n'
            '    {\n'
            '      "scene_id": 1,\n'
            '      "timestamp_start": "00:00",\n'
            '      "timestamp_end": "00:08",\n'
            '      "duration_seconds": 8,\n'
            '      "section": "cold_open",\n'
            '      "visual": {\n'
            '        "type": "title_card",\n'
            '        "description": "detailed visual prompt for generation",\n'
            '        "camera_movement": "push_in",\n'
            '        "style": "cinematic",\n'
            '        "color_grade": "tech_dark",\n'
            '        "vfx_overlay": "particles"\n'
            '      },\n'
            '      "audio": {\n'
            '        "voiceover": "narration text",\n'
            '        "tone": "shocked",\n'
            '        "pacing": "fast",\n'
            '        "emphasis_words": ["word1", "word2"],\n'
            '        "music_energy": "silent",\n'
            '        "sfx": ["impact_hit"]\n'
            '      },\n'
            '      "text_overlay": {\n'
            '        "hook_text": "SLAM TEXT",\n'
            '        "caption_style": "word_highlight",\n'
            '        "source_credit": "",\n'
            '        "animation_type": "slam"\n'
            '      },\n'
            '      "psychological_trigger": "curiosity",\n'
            '      "retention_device": "open_loop",\n'
            '      "factual_source": "https://example.com/source"\n'
            '    }\n'
            '  ]\n'
            '}'
        )

        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        data = json.loads(raw_text)
        return self._parse_claude_json(data, opportunity)

    def _parse_claude_json(
        self, data: Dict[str, Any], opportunity: TrendOpportunity
    ) -> CinematicScript:
        """Convert Claude's JSON response into CinematicScript dataclasses."""
        scenes: List[Scene] = []
        for raw in data.get("scenes", []):
            v = raw.get("visual", {})
            a = raw.get("audio", {})
            t = raw.get("text_overlay", {})

            visual = SceneVisual(
                type=v.get("type", "title_card"),
                description=v.get("description", ""),
                camera_movement=v.get("camera_movement", "push_in"),
                style=v.get("style", "cinematic"),
                color_grade=v.get("color_grade", "tech_dark"),
                vfx_overlay=v.get("vfx_overlay", "none"),
            )
            audio = SceneAudio(
                voiceover=a.get("voiceover", ""),
                tone=a.get("tone", "authoritative"),
                pacing=a.get("pacing", "medium"),
                emphasis_words=a.get("emphasis_words", []),
                music_energy=a.get("music_energy", "low"),
                sfx=a.get("sfx", []),
            )
            text_overlay = SceneText(
                hook_text=t.get("hook_text", ""),
                caption_style=t.get("caption_style", "word_highlight"),
                source_credit=t.get("source_credit", ""),
                animation_type=t.get("animation_type", "slam"),
            )
            scene = Scene(
                scene_id=raw.get("scene_id", len(scenes) + 1),
                timestamp_start=raw.get("timestamp_start", "00:00"),
                timestamp_end=raw.get("timestamp_end", "00:08"),
                duration_seconds=raw.get("duration_seconds", 8),
                visual=visual,
                audio=audio,
                text_overlay=text_overlay,
                psychological_trigger=raw.get("psychological_trigger", "curiosity"),
                retention_device=raw.get("retention_device", "none"),
                factual_source=raw.get("factual_source", ""),
                section=raw.get("section", "value_loop"),
            )
            scenes.append(scene)

        all_vo    = " ".join(s.audio.voiceover for s in scenes)
        word_count = len(all_vo.split())
        total_dur  = sum(s.duration_seconds for s in scenes)

        return CinematicScript(
            title=data.get("title", opportunity.selected_angle.title),
            hook_sentence=data.get("hook_sentence", opportunity.hook_sentence),
            total_duration_seconds=total_dur,
            word_count=word_count,
            scenes=scenes,
            recommended_grade=opportunity.color_grade,
            category=opportunity.category,
            is_fallback=False,
        )

    # ------------------------------------------------------------------
    # Private -- Local procedural generation
    # ------------------------------------------------------------------

    def _generate_local(self, opportunity: TrendOpportunity) -> CinematicScript:
        """
        Build a full 8-minute cinematic script procedurally from
        TrendOpportunity without any external API call.
        """
        topic    = opportunity.topic
        hook     = opportunity.hook_sentence
        facts    = list(opportunity.key_facts)
        stats    = list(opportunity.supporting_stats)
        sources  = list(opportunity.data_sources) + list(opportunity.sources_used)
        angle    = opportunity.selected_angle
        color    = opportunity.color_grade or "tech_dark"
        category = opportunity.category

        # Pad facts/stats/sources to avoid index errors
        while len(facts)   < 12: facts.append(f"Research on {topic} reveals significant shifts.")
        while len(stats)   < 8:  stats.append(f"Analysts tracking {topic} note accelerating trends.")
        while len(sources) < 12: sources.append("https://scholar.google.com")

        scenes: List[Scene] = []
        scene_counter = [0]  # mutable counter for nested-function use

        def next_id() -> int:
            scene_counter[0] += 1
            return scene_counter[0]

        def src(i: int) -> str:
            return sources[i % len(sources)]

        # ----------------------------------------------------------
        # Section 1 -- COLD OPEN [00:00-00:08]  8s
        # ----------------------------------------------------------
        cold_open_vo = self._ensure_no_forbidden_opener(hook, topic)
        cold_open_vo = self._trim_to_words(
            cold_open_vo, _words_for_duration(_COLD_OPEN_S)
        )

        scenes.append(Scene(
            scene_id=next_id(),
            timestamp_start="00:00",
            timestamp_end="00:08",
            duration_seconds=_COLD_OPEN_S,
            visual=_make_visual(
                0,
                (
                    f"Extreme close-up dramatic visual representing {topic}. "
                    "Dark background, single piercing light source. 4K cinematic."
                ),
                style="thriller",
                color_grade=color,
                vfx="glitch",
                force_type="title_card",
            ),
            audio=_make_audio(
                cold_open_vo,
                tone="shocked",
                pacing="fast",
                music_energy="silent",
                sfx=["impact_hit", "dead_silence"],
            ),
            text_overlay=_make_text(
                hook_text=self._extract_slam_text(cold_open_vo),
                caption_style="word_highlight",
                source_credit=src(0),
                animation_type="slam",
            ),
            psychological_trigger="curiosity",
            retention_device="open_loop",
            factual_source=src(0),
            section="cold_open",
        ))

        # ----------------------------------------------------------
        # Section 2 -- AMPLIFY HOOK [00:08-00:20]  12s
        # ----------------------------------------------------------
        amplify_vo = (
            f"{stats[0]}  "
            "That's not speculation -- that's what the numbers show."
        )
        amplify_vo = self._trim_to_words(
            amplify_vo, _words_for_duration(_AMPLIFY_HOOK_S)
        )

        scenes.append(Scene(
            scene_id=next_id(),
            timestamp_start="00:08",
            timestamp_end="00:20",
            duration_seconds=_AMPLIFY_HOOK_S,
            visual=_make_visual(
                1,
                (
                    f"Animated data chart or infographic revealing key statistic about {topic}. "
                    "Numbers counting up. Clean dark background."
                ),
                style="documentary",
                color_grade=color,
                vfx="data_stream",
                force_type="data_visualization",
            ),
            audio=_make_audio(
                amplify_vo,
                tone="authoritative",
                pacing="medium",
                music_energy="building",
                sfx=["data_blip"],
            ),
            text_overlay=_make_text(
                hook_text=self._extract_stat_text(stats[0]),
                caption_style="lower_third",
                source_credit=src(1),
                animation_type="typewriter",
            ),
            psychological_trigger="fear",
            retention_device="open_loop",
            factual_source=src(1),
            section="amplify_hook",
        ))

        # ----------------------------------------------------------
        # Section 3 -- THE PROMISE [00:20-00:45]  25s
        # ----------------------------------------------------------
        promise_vo = (
            f"By the time this ends, you'll understand exactly why {topic} is reshaping "
            f"{category.lower()} -- and what it means for the decisions ahead. "
            "The information coming is not available in a single place anywhere else. "
            "Keep watching."
        )
        promise_vo = self._trim_to_words(
            promise_vo, _words_for_duration(_PROMISE_S)
        )

        scenes.append(Scene(
            scene_id=next_id(),
            timestamp_start="00:20",
            timestamp_end="00:45",
            duration_seconds=_PROMISE_S,
            visual=_make_visual(
                2,
                (
                    f"Quick montage of cinematic visuals related to {topic}. "
                    "Each cut faster than the last. Aspirational imagery."
                ),
                style="epic",
                color_grade=color,
                vfx="light_rays",
                force_type="ai_image_motion",
            ),
            audio=_make_audio(
                promise_vo,
                tone="intimate",
                pacing="medium",
                music_energy="building",
                sfx=[],
            ),
            text_overlay=_make_text(
                hook_text="WHAT YOU'RE ABOUT TO LEARN",
                caption_style="word_highlight",
                source_credit="",
                animation_type="fade",
            ),
            psychological_trigger="desire",
            retention_device="tease_next",
            factual_source="",
            section="promise",
        ))

        # ----------------------------------------------------------
        # Section 4 -- VALUE DELIVERY LOOPS [00:45-05:00]  255s
        # Build 3 full loops (3 x 75s = 225s) + 1 partial (30s) = 255s
        # ----------------------------------------------------------
        loop_start_s = _COLD_OPEN_S + _AMPLIFY_HOOK_S + _PROMISE_S  # 45

        # (fact_idx, stat_idx, source_idx, trigger, mini_hook_phrase_idx)
        loop_defs = [
            (0,  0,  2,  "curiosity", 0),
            (3,  2,  4,  "fear",      2),
            (6,  4,  6,  "desire",    4),
            (9,  6,  8,  "urgency",   6),
        ]

        scene_vis_counter = 3  # continue visual-type rotation from scene 4 onward

        for loop_num, (fi, si, sri, loop_trigger, mhi) in enumerate(loop_defs):
            loop_offset_s = loop_start_s + loop_num * _LOOP_TOTAL_S
            if loop_offset_s >= loop_start_s + _VALUE_LOOP_TOTAL_S:
                break

            # Clamp each sub-scene's end to not exceed the section boundary
            section_budget = (loop_start_s + _VALUE_LOOP_TOTAL_S) - loop_offset_s

            # ---- mini_hook (10s) ----
            mh_s  = min(_LOOP_MINI_HOOK_S, section_budget)
            mh_start = loop_offset_s
            mh_end   = mh_start + mh_s
            mh_vo    = f"{_pick(_MINI_HOOK_PHRASES, mhi)}  {facts[fi % len(facts)]}"
            mh_vo    = self._trim_to_words(mh_vo, _words_for_duration(mh_s))

            scenes.append(Scene(
                scene_id=next_id(),
                timestamp_start=_seconds_to_mmss(mh_start),
                timestamp_end=_seconds_to_mmss(mh_end),
                duration_seconds=mh_s,
                visual=_make_visual(
                    scene_vis_counter,
                    f"Cinematic wide shot establishing context for {topic} -- loop {loop_num + 1}.",
                    style="cinematic",
                    color_grade=color,
                    vfx="none",
                ),
                audio=_make_audio(
                    mh_vo,
                    tone="conspiratorial",
                    pacing="fast",
                    music_energy="drop",
                    sfx=["music_stab"],
                ),
                text_overlay=_make_text(
                    hook_text=self._extract_slam_text(facts[fi % len(facts)]),
                    caption_style="word_highlight",
                    source_credit=src(sri),
                    animation_type="slam",
                ),
                psychological_trigger=loop_trigger,
                retention_device="pattern_interrupt",
                factual_source=src(sri),
                section="value_loop",
            ))
            scene_vis_counter += 1
            section_budget -= mh_s
            if section_budget <= 0:
                break

            # ---- tension_build (20s) ----
            tb_s  = min(_LOOP_TENSION_S, section_budget)
            tb_start = mh_end
            tb_end   = tb_start + tb_s
            tb_phrase = _pick(_TENSION_PHRASES, loop_num)
            tb_vo    = f"{tb_phrase}  {facts[(fi + 1) % len(facts)]}"
            tb_vo    = self._trim_to_words(tb_vo, _words_for_duration(tb_s))

            scenes.append(Scene(
                scene_id=next_id(),
                timestamp_start=_seconds_to_mmss(tb_start),
                timestamp_end=_seconds_to_mmss(tb_end),
                duration_seconds=tb_s,
                visual=_make_visual(
                    scene_vis_counter,
                    (
                        f"Close-up documentary footage or graphic showing tension "
                        f"and complexity around {topic}."
                    ),
                    style="documentary",
                    color_grade=color,
                    vfx="neural_network",
                ),
                audio=_make_audio(
                    tb_vo,
                    tone="urgent",
                    pacing="medium",
                    music_energy="building",
                    sfx=[],
                ),
                text_overlay=_make_text(
                    hook_text="",
                    caption_style="lower_third",
                    source_credit=src(sri),
                    animation_type="typewriter",
                ),
                psychological_trigger="fear",
                retention_device="open_loop",
                factual_source=src(sri),
                section="value_loop",
            ))
            scene_vis_counter += 1
            section_budget -= tb_s
            if section_budget <= 0:
                break

            # ---- data_reveal (25s) ----
            dr_s  = min(_LOOP_DATA_S, section_budget)
            dr_start = tb_end
            dr_end   = dr_start + dr_s
            dr_phrase = _pick(_DATA_REVEAL_PHRASES, loop_num)
            dr_vo    = (
                f"{dr_phrase}  "
                f"{stats[si % len(stats)]}  "
                f"{stats[(si + 1) % len(stats)]}"
            )
            dr_vo    = self._trim_to_words(dr_vo, _words_for_duration(dr_s))

            scenes.append(Scene(
                scene_id=next_id(),
                timestamp_start=_seconds_to_mmss(dr_start),
                timestamp_end=_seconds_to_mmss(dr_end),
                duration_seconds=dr_s,
                visual=_make_visual(
                    scene_vis_counter,
                    (
                        f"Animated data visualization: charts, graphs, or infographic about {topic}. "
                        "Numbers animate in. Clean, high-contrast."
                    ),
                    style="documentary",
                    color_grade=color,
                    vfx="data_stream",
                    force_type="data_visualization",
                ),
                audio=_make_audio(
                    dr_vo,
                    tone="authoritative",
                    pacing="slow",
                    music_energy="building",
                    sfx=["data_blip"],
                ),
                text_overlay=_make_text(
                    hook_text=self._extract_stat_text(stats[si % len(stats)]),
                    caption_style="lower_third",
                    source_credit=src(sri + 1),
                    animation_type="typewriter",
                ),
                psychological_trigger="social_proof",
                retention_device="shocking_reveal",
                factual_source=src(sri + 1),
                section="value_loop",
            ))
            scene_vis_counter += 1
            section_budget -= dr_s
            if section_budget <= 0:
                break

            # ---- mini_resolution (20s) ----
            mr_s  = min(_LOOP_RESOLUTION_S, section_budget)
            mr_start = dr_end
            mr_end   = mr_start + mr_s
            mr_phrase = _pick(_RESOLUTION_PHRASES, loop_num)
            mr_vo    = (
                f"{mr_phrase}  "
                f"{facts[(fi + 2) % len(facts)]}"
            )
            mr_vo    = self._trim_to_words(mr_vo, _words_for_duration(mr_s))

            scenes.append(Scene(
                scene_id=next_id(),
                timestamp_start=_seconds_to_mmss(mr_start),
                timestamp_end=_seconds_to_mmss(mr_end),
                duration_seconds=mr_s,
                visual=_make_visual(
                    scene_vis_counter,
                    (
                        f"Slow motion footage or AI generated imagery related to {topic}, "
                        "signaling resolution and clarity."
                    ),
                    style="cinematic",
                    color_grade=color,
                    vfx="light_rays",
                ),
                audio=_make_audio(
                    mr_vo,
                    tone="calm",
                    pacing="medium",
                    music_energy="low",
                    sfx=[],
                ),
                text_overlay=_make_text(
                    hook_text="",
                    caption_style="word_highlight",
                    source_credit=src(sri + 1),
                    animation_type="fade",
                ),
                psychological_trigger="desire",
                retention_device="tease_next",
                factual_source=src(sri + 1),
                section="value_loop",
            ))
            scene_vis_counter += 1
            section_budget -= mr_s
            if section_budget <= 0:
                break

        # Ensure minimum pattern interrupts are distributed across the value loops
        self._enforce_pattern_interrupts(scenes)

        # ----------------------------------------------------------
        # Section 5 -- EMOTIONAL CLIMAX [05:00-07:00]  120s
        # ----------------------------------------------------------
        climax_start_s = _COLD_OPEN_S + _AMPLIFY_HOOK_S + _PROMISE_S + _VALUE_LOOP_TOTAL_S  # 300
        climax_mid_s   = climax_start_s + 60
        climax_end_s   = climax_start_s + _CLIMAX_S  # 420

        # First climax scene -- revelation (60s)
        revelation_vo = (
            "And here is the part that reshapes everything. "
            f"{facts[8 % len(facts)]}  "
            f"{stats[6 % len(stats)]}  "
            "The implication is not theoretical. It is already happening."
        )
        revelation_vo = self._trim_to_words(revelation_vo, _words_for_duration(60))

        scenes.append(Scene(
            scene_id=next_id(),
            timestamp_start=_seconds_to_mmss(climax_start_s),
            timestamp_end=_seconds_to_mmss(climax_mid_s),
            duration_seconds=60,
            visual=_make_visual(
                scene_vis_counter,
                (
                    "Most cinematic visuals of entire video -- sweeping AI-generated or stock footage "
                    f"representing the full scale of {topic}. Epic wide angle, god-ray lighting."
                ),
                style="epic",
                color_grade=color,
                vfx="light_rays",
                force_type="ai_video",
            ),
            audio=_make_audio(
                revelation_vo,
                tone="shocked",
                pacing="slow",
                music_energy="peak",
                sfx=["orchestral_swell"],
            ),
            text_overlay=_make_text(
                hook_text="THE REAL STORY",
                caption_style="word_highlight",
                source_credit=src(9),
                animation_type="slam",
            ),
            psychological_trigger="fear",
            retention_device="shocking_reveal",
            factual_source=src(9),
            section="climax",
        ))
        scene_vis_counter += 1

        # Second climax scene -- path forward (60s)
        path_vo = (
            "What the data consistently points to is a narrow window of clarity. "
            f"{facts[9 % len(facts)]}  "
            "The people who understand this early have an asymmetric advantage. "
            "That is not hyperbole -- it is the pattern behind every major shift of the last decade."
        )
        path_vo = self._trim_to_words(path_vo, _words_for_duration(60))

        scenes.append(Scene(
            scene_id=next_id(),
            timestamp_start=_seconds_to_mmss(climax_mid_s),
            timestamp_end=_seconds_to_mmss(climax_end_s),
            duration_seconds=60,
            visual=_make_visual(
                scene_vis_counter,
                (
                    "Forward-moving camera through dynamic environment representing progress and clarity. "
                    f"Subject: {topic}. Style: National Geographic meets Bloomberg."
                ),
                style="cinematic",
                color_grade=color,
                vfx="particles",
                force_type="stock_footage",
            ),
            audio=_make_audio(
                path_vo,
                tone="authoritative",
                pacing="slow",
                music_energy="peak",
                sfx=[],
            ),
            text_overlay=_make_text(
                hook_text="",
                caption_style="lower_third",
                source_credit=src(10),
                animation_type="fade",
            ),
            psychological_trigger="desire",
            retention_device="open_loop",
            factual_source=src(10),
            section="climax",
        ))
        scene_vis_counter += 1

        # ----------------------------------------------------------
        # Section 6 -- CTA [07:00-08:00]  60s
        # ----------------------------------------------------------
        cta_start_s = climax_end_s   # 420
        cta_end_s   = cta_start_s + _CTA_S  # 480

        cta_vo = (
            "The question worth sitting with: if this is already in motion, "
            "what does it mean for the decisions you make in the next six months? "
            "Leave your answer in the comments -- I read every single one. "
            "Every week on this channel, we find the signal inside the noise -- "
            "the research, the data, and the pattern that everyone else missed. "
            "If that's useful to you, the subscribe button is right there. "
            f"And in the next video, we go one level deeper into {topic} -- "
            "specifically the part that almost nobody has connected yet."
        )
        cta_vo = self._trim_to_words(cta_vo, _words_for_duration(_CTA_S))

        scenes.append(Scene(
            scene_id=next_id(),
            timestamp_start=_seconds_to_mmss(cta_start_s),
            timestamp_end=_seconds_to_mmss(cta_end_s),
            duration_seconds=_CTA_S,
            visual=_make_visual(
                scene_vis_counter,
                (
                    "Channel branding card with end-screen layout. Suggested video thumbnails visible. "
                    "Animated subscribe prompt. Warm tone shift from rest of video."
                ),
                style="cinematic",
                color_grade="warm_gold",
                vfx="none",
                force_type="title_card",
            ),
            audio=_make_audio(
                cta_vo,
                tone="intimate",
                pacing="medium",
                music_energy="drop",
                sfx=["gentle_chime"],
            ),
            text_overlay=_make_text(
                hook_text="STAY FOR THE NEXT ONE",
                caption_style="lower_third",
                source_credit="",
                animation_type="fade",
            ),
            psychological_trigger="curiosity",
            retention_device="open_loop",
            factual_source="",
            section="cta",
        ))

        # ----------------------------------------------------------
        # Assemble CinematicScript
        # ----------------------------------------------------------
        all_vo   = " ".join(s.audio.voiceover for s in scenes)
        wc       = len(all_vo.split())
        total_s  = sum(s.duration_seconds for s in scenes)

        return CinematicScript(
            title=angle.title,
            hook_sentence=hook,
            total_duration_seconds=total_s,
            word_count=wc,
            scenes=scenes,
            recommended_grade=color,
            category=category,
            is_fallback=True,
        )

    # ------------------------------------------------------------------
    # Private -- helpers
    # ------------------------------------------------------------------

    def _calc_psychological_score(self, script: CinematicScript) -> float:
        """
        Compute psychological engagement score using the defined formula, capped at 100.

        psych_score = (
            curiosity_hooks_count * 12 +
            fear_triggers_count * 10 +
            desire_triggers_count * 10 +
            open_loops_never_closed_simultaneously * 8 +
            social_proof_count * 6 +
            pattern_interrupts_count * 8 +
            data_reveals_count * 6
        )  # capped at 100
        """
        scenes           = script.scenes
        curiosity_hooks  = sum(1 for s in scenes if s.psychological_trigger == "curiosity")
        fear_triggers    = sum(1 for s in scenes if s.psychological_trigger == "fear")
        desire_triggers  = sum(1 for s in scenes if s.psychological_trigger == "desire")
        social_proof     = sum(1 for s in scenes if s.psychological_trigger == "social_proof")
        pattern_ints     = sum(1 for s in scenes if s.retention_device == "pattern_interrupt")
        data_reveals     = sum(1 for s in scenes if s.retention_device == "shocking_reveal")

        # Approximate simultaneous open loops: count open_loop scenes before any
        # tease_next or shocking_reveal, bounded at 5 to match realistic viewer experience
        open_loop_scenes = [s for s in scenes if s.retention_device == "open_loop"]
        open_loops_simultaneous = min(len(open_loop_scenes), 5)

        raw = (
            curiosity_hooks         * 12 +
            fear_triggers           * 10 +
            desire_triggers         * 10 +
            open_loops_simultaneous *  8 +
            social_proof            *  6 +
            pattern_ints            *  8 +
            data_reveals            *  6
        )
        return min(float(raw), 100.0)

    def _enforce_pattern_interrupts(self, scenes: List[Scene]) -> None:
        """
        Ensure at least MIN_PATTERN_INTERRUPTS scenes carry 'pattern_interrupt'
        by marking eligible value_loop scenes at ~47-second intervals.
        """
        pi_count = sum(1 for s in scenes if s.retention_device == "pattern_interrupt")
        if pi_count >= MIN_PATTERN_INTERRUPTS:
            return

        needed   = MIN_PATTERN_INTERRUPTS - pi_count
        tagged   = 0
        last_pi_s = -PATTERN_INTERRUPT_INTERVAL_S  # seed to allow immediate first tag

        for scene in scenes:
            if tagged >= needed:
                break
            parts   = scene.timestamp_start.split(":")
            scene_s = int(parts[0]) * 60 + int(parts[1])
            if (
                scene_s - last_pi_s >= PATTERN_INTERRUPT_INTERVAL_S
                and scene.retention_device not in ("open_loop", "shocking_reveal")
                and scene.section not in ("cold_open", "cta")
            ):
                scene.retention_device = "pattern_interrupt"
                last_pi_s = scene_s
                tagged += 1

    def _ensure_no_forbidden_opener(self, text: str, topic: str) -> str:
        """
        Return text as-is if the first word is not forbidden.
        Otherwise craft a cinematic replacement cold-open sentence.
        """
        first = text.split()[0].lower().rstrip(".,!?\"'") if text.split() else ""
        forbidden_first_words = {fo.split()[0].lower() for fo in FORBIDDEN_OPENERS}
        if first not in forbidden_first_words:
            return text

        replacements = [
            f"The numbers behind {topic} are not what anyone expected.",
            f"Something is fundamentally broken in the way we understand {topic}.",
            f"Every analyst missed it. Every model got it wrong. {topic} changed anyway.",
            f"Three months ago, a quiet shift began inside {topic} -- and almost nobody noticed.",
            f"There is a gap between what experts say about {topic} and what the data actually shows.",
            f"Somewhere inside {topic}, a pattern is forming that the mainstream is not covering.",
        ]
        return replacements[hash(topic) % len(replacements)]

    @staticmethod
    def _trim_to_words(text: str, max_words: int) -> str:
        """Trim text to approximately max_words, trying to end on a sentence boundary."""
        words = text.split()
        if len(words) <= max_words:
            return text
        trimmed = words[:max_words]
        joined  = " ".join(trimmed)
        # Prefer ending on a sentence boundary in the second half of the trimmed text
        for end_char in (".", "!", "?"):
            last_sent = joined.rfind(end_char)
            if last_sent > len(joined) // 2:
                return joined[: last_sent + 1]
        return joined + "."

    @staticmethod
    def _extract_slam_text(sentence: str) -> str:
        """Extract a 2-4 word SLAM TEXT fragment from a sentence."""
        words  = sentence.split()
        if len(words) <= 4:
            return sentence.upper()
        fillers = {
            "the", "a", "an", "is", "are", "was", "were",
            "it", "in", "on", "at", "of", "and", "but", "or",
        }
        chosen: List[str] = []
        for w in words:
            cleaned = w.lower().rstrip(".,!?\"'")
            if cleaned not in fillers:
                chosen.append(w.rstrip(".,!?\"'"))
            if len(chosen) == 4:
                break
        return " ".join(chosen).upper() if chosen else " ".join(words[:3]).upper()

    @staticmethod
    def _extract_stat_text(stat: str) -> str:
        """Extract numeric stat or short claim for on-screen text overlay."""
        match = re.search(
            r"[\d,]+\.?\d*\s*(?:%|percent|billion|million|trillion|x|X)?", stat
        )
        if match:
            return match.group(0).strip().upper()
        return " ".join(stat.split()[:5]).upper()

    def build_voiceover_text(self, script: CinematicScript) -> str:
        """
        Concatenate all scene voiceovers into one continuous narration string.
        Utility method for downstream TTS modules.
        """
        return "  ".join(
            s.audio.voiceover.strip()
            for s in script.scenes
            if s.audio.voiceover.strip()
        )
