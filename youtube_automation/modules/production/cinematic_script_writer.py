"""
modules/production/cinematic_script_writer.py
Cinematic screenplay generator for YouTube videos.
Uses Claude API if ANTHROPIC_API_KEY is set, otherwise uses enhanced topic library.
"""
from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

log = logging.getLogger("cinematic.script")


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class SceneVisual:
    type: str           # "title_card|data_viz|b_roll|number_card|cta"
    description: str
    camera_movement: str = "push_in"
    color_grade: str = "tech_dark"
    vfx_overlay: str = "none"


@dataclass
class SceneAudio:
    voiceover: str
    tone: str = "authoritative"   # urgent|calm|shocked|authoritative|intimate
    pacing: str = "medium"
    emphasis_words: List[str] = field(default_factory=list)
    music_energy: str = "medium"  # low|building|peak|drop|silence
    sfx: List[str] = field(default_factory=list)


@dataclass
class SceneText:
    hook_text: str = ""
    lower_third: str = ""
    source_credit: str = ""
    animation: str = "slam"   # fade|slam|glitch|typewriter|zoom_in


@dataclass
class Scene:
    scene_id: int
    timestamp_start: str
    timestamp_end: str
    duration_seconds: int
    visual: SceneVisual
    audio: SceneAudio
    text: SceneText
    psychological_trigger: str = "curiosity"
    retention_device: str = "none"


@dataclass
class CinematicScript:
    topic: str
    hook: str
    scenes: List[Scene]
    quality_score: float = 0.0
    total_duration_s: int = 0
    word_count: int = 0
    estimated_ctr: float = 4.5


# ── Script writer ──────────────────────────────────────────────────────────────

class CinematicScriptWriter:
    """Generates Hollywood-style YouTube scripts with cinematic scene breakdowns."""

    QUALITY_THRESHOLD = 70.0

    # Hook formulas that have proven CTR performance
    HOOK_FORMULAS = [
        "The truth about {topic} nobody is saying",
        "What {topic} actually means for your {life_area}",
        "The numbers behind {topic} will shock you",
        "Why {topic} is happening (the real explanation)",
        "Everyone is getting {topic} completely wrong",
        "{topic} just changed everything — here's what to do",
        "The {topic} secret the media won't cover",
        "I studied {topic} for 30 days. This is what I found.",
    ]

    LIFE_AREAS = ["career", "money", "future", "health", "retirement", "business"]

    def __init__(self):
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._client = None
        if self._api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError:
                log.debug("anthropic package not available")

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate(self, content: dict) -> CinematicScript:
        """
        Generate a cinematic script from content dict.
        content = {topic, hook, bullets[], takeaway, category, tags[]}
        """
        if self._client:
            try:
                return self._generate_with_claude(content)
            except Exception as e:
                log.warning("Claude script generation failed (%s), using local", e)

        return self._generate_local(content)

    def build_voiceover_text(self, script: CinematicScript) -> str:
        """Concatenate all scene voiceovers into one continuous narration string."""
        parts = []
        for scene in script.scenes:
            vo = scene.audio.voiceover.strip()
            if vo:
                parts.append(vo)
        return "  ".join(parts)

    def score_script(self, script: CinematicScript) -> float:
        """Score script quality 0-100."""
        score = 50.0

        full_text = " ".join(s.audio.voiceover for s in script.scenes).lower()
        first_words = full_text.split()[:5]

        # Hook quality checks
        bad_openers = {"hey", "welcome", "today", "in this", "hi,", "hello"}
        if not any(w in bad_openers for w in first_words):
            score += 15

        # Pattern interrupts (scene variety)
        visual_types = [s.visual.type for s in script.scenes]
        unique_types = len(set(visual_types))
        score += min(unique_types * 3, 15)

        # Psychological triggers present
        triggers = {s.psychological_trigger for s in script.scenes}
        score += min(len(triggers) * 3, 12)

        # Duration (8-12 min is ideal but we do shorter format)
        if 40 <= script.total_duration_s <= 60:
            score += 8  # short format done right

        # Word count (too short = not enough value)
        if script.word_count >= 80:
            score += 10

        return min(score, 100.0)

    # ── Claude-powered generation ──────────────────────────────────────────────

    def _generate_with_claude(self, content: dict) -> CinematicScript:
        topic = content.get("topic", "")
        hook = content.get("hook", "")
        bullets = content.get("bullets", [])
        takeaway = content.get("takeaway", "")

        prompt = f"""You are a YouTube video scriptwriter. Write a compelling 45-50 second video script.

TOPIC: {topic}
HOOK: {hook}
KEY POINTS:
{chr(10).join(f'- {b}' for b in bullets)}
TAKEAWAY: {takeaway}

Rules:
- Do NOT start with "Hey", "Welcome", "Today", or "Hi"
- Start mid-action or mid-revelation
- Natural, conversational speech
- No "Number 1, Number 2" structure — flowing narrative
- End with ONE clear call to action

Write ONLY the narration text, no stage directions. Keep it to ~130 words."""

        resp = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        narration = resp.content[0].text.strip()
        return self._build_script_from_narration(content, narration)

    # ── Local generation (no API) ──────────────────────────────────────────────

    def _generate_local(self, content: dict) -> CinematicScript:
        topic   = content.get("topic", "Today's Topic")
        hook    = content.get("hook", f"Here's what you need to know about {topic}.")
        bullets = content.get("bullets", [])
        takeaway = content.get("takeaway", "Take action today.")
        category = content.get("category", "Tech")

        # Build natural narration (no numbered list style)
        life_area = self.LIFE_AREAS[hash(topic) % len(self.LIFE_AREAS)]

        # Rewrite hook to be punchy
        cinematic_hook = self._make_cinematic_hook(hook, topic, life_area)

        # Build flowing narrative for bullets
        transitions = [
            "Here's what the data shows.",
            "And it gets more interesting.",
            "The critical part most people miss:",
            "This is where it really matters.",
            "The final piece of the puzzle:",
        ]

        narration_parts = [cinematic_hook]
        for i, bullet in enumerate(bullets[:5]):
            transition = transitions[i] if i < len(transitions) else "Also important:"
            narration_parts.append(f"{transition} {bullet}.")

        narration_parts.append(
            f"The key takeaway: {takeaway} "
            f"Subscribe so you never miss insights like this."
        )

        narration = "  ".join(narration_parts)
        return self._build_script_from_narration(content, narration)

    def _make_cinematic_hook(self, original_hook: str, topic: str, life_area: str) -> str:
        """Rewrite hook to be more cinematic — no safe openers."""
        # If hook already starts well, keep it
        first_word = original_hook.split()[0].lower().rstrip(".,") if original_hook else ""
        bad_openers = {"hey", "welcome", "today", "in", "hi", "hello", "have", "did"}
        if first_word not in bad_openers:
            return original_hook

        # Rewrite with cinematic formula
        formula_idx = hash(topic) % len(self.HOOK_FORMULAS)
        template = self.HOOK_FORMULAS[formula_idx]
        return template.format(topic=topic, life_area=life_area).capitalize() + "."

    def _build_script_from_narration(self, content: dict, narration: str) -> CinematicScript:
        """Break narration into scenes with visual/audio metadata."""
        topic    = content.get("topic", "")
        hook     = content.get("hook", "")
        bullets  = content.get("bullets", [])
        takeaway = content.get("takeaway", "")

        # Split narration into sentence groups
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', narration) if s.strip()]

        scenes: List[Scene] = []
        t = 0

        # Scene 1: HOOK (0-6s)
        hook_text = sentences[0] if sentences else hook
        hook_vo   = hook_text
        scenes.append(Scene(
            scene_id=1,
            timestamp_start="0:00",
            timestamp_end="0:06",
            duration_seconds=6,
            visual=SceneVisual(
                type="title_card",
                description=f"Dark cinematic background with animated particles. Topic: {topic}",
                camera_movement="push_in",
                color_grade="tech_dark",
                vfx_overlay="particle_system",
            ),
            audio=SceneAudio(
                voiceover=hook_vo,
                tone="shocked",
                pacing="fast",
                emphasis_words=self._extract_emphasis(hook_vo),
                music_energy="building",
                sfx=["impact"],
            ),
            text=SceneText(
                hook_text=topic.upper(),
                animation="slam",
            ),
            psychological_trigger="curiosity",
            retention_device="open_loop",
        ))
        t = 6

        # Scenes 2-6: BULLET POINTS (4s each)
        bullet_triggers = ["curiosity", "fear", "desire", "urgency", "social_proof"]
        for i, bullet in enumerate(bullets[:5]):
            start_s = t
            end_s   = t + 4
            start_ts = f"{start_s // 60}:{start_s % 60:02d}"
            end_ts   = f"{end_s // 60}:{end_s % 60:02d}"

            # Use corresponding sentence if available
            sent_idx = i + 1
            vo = sentences[sent_idx] if sent_idx < len(sentences) else bullet

            scenes.append(Scene(
                scene_id=i + 2,
                timestamp_start=start_ts,
                timestamp_end=end_ts,
                duration_seconds=4,
                visual=SceneVisual(
                    type="number_card",
                    description=f"Number {i+1} with fact: {bullet[:60]}",
                    camera_movement="zoom_punch" if i % 2 == 0 else "pan_right",
                    color_grade="tech_dark",
                    vfx_overlay="none",
                ),
                audio=SceneAudio(
                    voiceover=vo,
                    tone="authoritative",
                    pacing="medium",
                    emphasis_words=self._extract_emphasis(vo),
                    music_energy="medium",
                    sfx=["whoosh"] if i == 0 else [],
                ),
                text=SceneText(
                    lower_third=bullet[:80],
                    animation="typewriter" if i % 2 == 0 else "fade",
                ),
                psychological_trigger=bullet_triggers[i % len(bullet_triggers)],
                retention_device="tease_next" if i < 4 else "shocking_reveal",
            ))
            t = end_s

        # Scene 7: TAKEAWAY (6s)
        start_ts = f"{t // 60}:{t % 60:02d}"
        t += 6
        end_ts   = f"{t // 60}:{t % 60:02d}"
        takeaway_vo = sentences[-2] if len(sentences) >= 2 else takeaway

        scenes.append(Scene(
            scene_id=7,
            timestamp_start=start_ts,
            timestamp_end=end_ts,
            duration_seconds=6,
            visual=SceneVisual(
                type="title_card",
                description="Dramatic full-screen takeaway with radial glow",
                camera_movement="pull_out",
                color_grade="tech_dark",
                vfx_overlay="light_rays",
            ),
            audio=SceneAudio(
                voiceover=takeaway_vo,
                tone="calm",
                pacing="slow",
                emphasis_words=self._extract_emphasis(takeaway_vo),
                music_energy="peak",
                sfx=[],
            ),
            text=SceneText(
                hook_text="KEY TAKEAWAY",
                animation="slam",
            ),
            psychological_trigger="desire",
            retention_device="open_loop",
        ))

        # Scene 8: CTA (5s)
        start_ts = f"{t // 60}:{t % 60:02d}"
        t += 5
        end_ts   = f"{t // 60}:{t % 60:02d}"
        cta_vo   = sentences[-1] if sentences else "Subscribe for daily insights."

        scenes.append(Scene(
            scene_id=8,
            timestamp_start=start_ts,
            timestamp_end=end_ts,
            duration_seconds=5,
            visual=SceneVisual(
                type="cta",
                description="Subscribe CTA with pulsing ring and channel branding",
                camera_movement="push_in",
                color_grade="tech_dark",
                vfx_overlay="none",
            ),
            audio=SceneAudio(
                voiceover=cta_vo,
                tone="warm",
                pacing="medium",
                emphasis_words=["subscribe"],
                music_energy="drop",
                sfx=["chime"],
            ),
            text=SceneText(
                hook_text="SUBSCRIBE",
                lower_third="Mind Fuel Daily",
                animation="zoom_in",
            ),
            psychological_trigger="social_proof",
            retention_device="none",
        ))

        total_duration = t
        full_vo = " ".join(s.audio.voiceover for s in scenes)
        word_count = len(full_vo.split())

        script = CinematicScript(
            topic=topic,
            hook=hook,
            scenes=scenes,
            total_duration_s=total_duration,
            word_count=word_count,
        )
        script.quality_score = self.score_script(script)
        return script

    def _extract_emphasis(self, text: str) -> List[str]:
        """Extract words that should be emphasized — numbers, power words."""
        power_words = {
            "shocking", "never", "secret", "truth", "real", "actual",
            "proven", "million", "billion", "percent", "%", "free",
            "instant", "warning", "critical", "urgent", "discover",
        }
        words = []
        for w in text.split():
            clean = w.lower().strip(".,!?")
            if clean in power_words or clean.replace(",", "").isdigit():
                words.append(w.strip(".,!?"))
        return words[:4]
