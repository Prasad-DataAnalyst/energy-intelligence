"""
interfaces/script_interface.py
Layer: Interface Contract
Safety: auto-heal-only
Description: Canonical dataclasses for cinematic screenplay output.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SceneVisual:
    type: str             # ai_video|stock_footage|ai_image_motion|data_visualization|title_card
    description: str      # detailed prompt for generation
    camera_movement: str  # push_in|pull_out|pan_left|orbit|crane_up|handheld
    style: str            # cinematic|documentary|thriller|epic
    color_grade: str
    vfx_overlay: str      # particles|light_rays|glitch|neural_network|data_stream|none


@dataclass
class SceneAudio:
    voiceover: str
    tone: str             # urgent|calm|shocked|intimate|authoritative|conspiratorial
    pacing: str           # fast|medium|slow|dramatic_pause
    emphasis_words: List[str] = field(default_factory=list)
    music_energy: str = "low"   # silent|low|building|peak|drop
    sfx: List[str] = field(default_factory=list)


@dataclass
class SceneText:
    hook_text: str = ""
    caption_style: str = "word_highlight"  # word_highlight|lower_third|none
    source_credit: str = ""
    animation_type: str = "slam"            # slam|typewriter|fade|glitch


@dataclass
class Scene:
    scene_id: int
    timestamp_start: str   # "MM:SS"
    timestamp_end: str
    duration_seconds: int
    visual: SceneVisual
    audio: SceneAudio
    text_overlay: SceneText
    psychological_trigger: str   # curiosity|fear|desire|urgency|social_proof
    retention_device: str        # open_loop|pattern_interrupt|tease_next|shocking_reveal|none
    factual_source: str = ""
    section: str = "value_loop"  # cold_open|amplify_hook|promise|value_loop|climax|cta


@dataclass
class CinematicScript:
    title: str
    hook_sentence: str
    total_duration_seconds: int
    word_count: int
    scenes: List[Scene] = field(default_factory=list)

    # Quality metrics
    quality_score: float = 0.0
    psychological_score: float = 0.0
    pattern_interrupt_count: int = 0
    data_viz_scene_count: int = 0
    emotional_arc_complete: bool = False
    all_sources_cited: bool = False
    first_word_valid: bool = True

    # Production metadata
    recommended_grade: str = "tech_dark"
    category: str = ""
    is_fallback: bool = False
