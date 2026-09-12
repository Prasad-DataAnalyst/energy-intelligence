"""Shared data models for the viral pipeline."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
import json


@dataclass
class TrendResult:
    topic: str
    title: str
    score: float
    velocity: float
    engagement_potential: float
    sources: List[str]
    talking_points: List[str] = field(default_factory=list)
    target_audience: str = "general"
    emotional_hooks: List[str] = field(default_factory=list)
    visual_style: str = "cinematic"
    keywords: List[str] = field(default_factory=list)
    category: str = "general"
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass
class Scene:
    id: int
    duration: float
    voiceover: str
    visual: str               # image generation prompt or description
    text_overlay: str = ""
    transition: str = "cut"   # cut | fade | zoom_punch | glitch
    shot_type: str = "medium" # close | medium | wide | aerial
    broll_description: str = ""
    pacing: str = "normal"    # slow | normal | fast
    keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Script:
    topic: str
    platform: str             # youtube | tiktok | reels
    total_duration: float
    scenes: List[Scene]
    style: str = "cinematic"
    target_audience: str = "general"
    hook: str = ""
    cta: str = ""
    title: str = ""
    description: str = ""

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Script":
        scenes = [Scene(**s) for s in d.pop("scenes", [])]
        return cls(scenes=scenes, **d)


@dataclass
class AudioAsset:
    scene_id: int
    voice_file: str
    duration: float
    provider: str = "espeak"
    sample_rate: int = 44100


@dataclass
class VisualAsset:
    scene_id: int
    image_file: str
    video_file: Optional[str] = None
    motion_type: str = "ken_burns"
    processed_file: Optional[str] = None
    width: int = 1920
    height: int = 1080


@dataclass
class PipelineContext:
    topic_slug: str
    output_dir: str
    platform: str = "youtube"
    trend: Optional[TrendResult] = None
    script: Optional[Script] = None
    audio_assets: List[AudioAsset] = field(default_factory=list)
    music_file: Optional[str] = None
    mixed_audio: Optional[str] = None
    visual_assets: List[VisualAsset] = field(default_factory=list)
    final_video: Optional[str] = None
    thumbnails: List[str] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    def save_state(self, path: str) -> None:
        state = {
            "topic_slug": self.topic_slug,
            "output_dir": self.output_dir,
            "platform": self.platform,
            "trend": asdict(self.trend) if self.trend else None,
            "script": json.loads(self.script.to_json()) if self.script else None,
            "audio_assets": [asdict(a) for a in self.audio_assets],
            "music_file": self.music_file,
            "mixed_audio": self.mixed_audio,
            "visual_assets": [asdict(v) for v in self.visual_assets],
            "final_video": self.final_video,
            "thumbnails": self.thumbnails,
            "metadata": self.metadata,
        }
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
