"""
interfaces/video_interface.py
Layer: Interface Contract
Safety: auto-heal-only
Description: Canonical dataclass for video production output.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class SceneVideoFile:
    scene_id: int
    video_path: str
    duration_seconds: float
    visual_level_used: int    # 1=AI video, 2=AI image, 3=stock, 4=data_viz
    source: str = ""          # runway|kling|replicate|pexels|pixabay|generated


@dataclass
class VideoPackage:
    # Scene-level clips
    scene_clips: List[SceneVideoFile] = field(default_factory=list)

    # Master outputs
    silent_video_path: str = ""
    final_video_path: str = ""
    graded_video_path: str = ""

    # Quality metrics
    resolution: str = "1920x1080"
    fps: int = 24
    duration_seconds: float = 0.0
    sharpness_score: float = 0.0
    motion_smoothness: float = 0.0
    color_richness: float = 0.0
    scene_variety_count: int = 0

    # Color grade applied
    grade_applied: str = ""
    color_grade_path: str = ""

    # Thumbnails
    thumbnail_paths: List[str] = field(default_factory=list)

    # Shorts clips
    shorts_paths: List[str] = field(default_factory=list)
    shorts_timestamps: List[Dict] = field(default_factory=list)

    # Metadata
    visual_levels_used: List[int] = field(default_factory=list)
    is_fallback: bool = False
    file_size_mb: float = 0.0
