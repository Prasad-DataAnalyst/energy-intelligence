"""
interfaces/audio_interface.py
Layer: Interface Contract
Safety: auto-heal-only
Description: Canonical dataclass for audio engine output.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class SceneAudioFile:
    scene_id: int
    voice_path: str
    sfx_paths: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    lufs_measured: float = 0.0
    voice_engine_used: str = "unknown"


@dataclass
class AudioPackage:
    # Per-scene files
    scene_files: List[SceneAudioFile] = field(default_factory=list)

    # Master mixed files
    voice_path: str = ""
    music_path: str = ""
    mixed_path: str = ""

    # Quality metrics
    final_lufs: float = 0.0
    true_peak_dbtp: float = 0.0
    dynamic_range_db: float = 0.0
    noise_floor_db: float = -60.0

    # Metadata
    voice_engine_used: str = "unknown"
    music_source: str = "procedural"
    sample_rate: int = 44100
    total_duration_seconds: float = 0.0
    processing_steps_applied: List[str] = field(default_factory=list)
    is_fallback: bool = False
