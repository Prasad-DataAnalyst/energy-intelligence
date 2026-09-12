"""
interfaces/trend_interface.py
Layer: Interface Contract
Safety: auto-heal-only
Description: Canonical dataclass for trend intelligence output.
All modules that consume trend data must accept TrendOpportunity.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class VideoAngle:
    angle_type: str          # shocking_truth | data_explainer | future_prediction | simple_explanation | contrarian
    title: str
    hook: str
    search_volume_score: float    # 0-1
    competition_gap_score: float  # 0-1  (higher = less competition)
    emotional_trigger_score: float
    monetization_tier: str        # tier_1 | tier_2 | tier_3
    visual_potential_score: float
    composite_score: float        # weighted final score


@dataclass
class TrendOpportunity:
    # Core topic
    topic: str
    category: str
    selected_angle: VideoAngle
    hook_sentence: str

    # Research payload
    key_facts: List[str] = field(default_factory=list)
    data_sources: List[str] = field(default_factory=list)
    supporting_stats: List[str] = field(default_factory=list)

    # Psychological profile
    emotional_trigger: str = "curiosity"   # curiosity|fear|desire|urgency|social_proof
    target_audience: str = "18-45 US adults"

    # Performance predictions
    predicted_ctr_range: str = "4-8%"
    rpm_category: str = "tier_2"
    rpm_estimate: float = 10.0
    competition_level: str = "medium"      # low|medium|high|saturated
    opportunity_score: float = 0.0
    opportunity_window_hours: int = 24

    # Production guidance
    recommended_visual_style: str = "tech_dark"
    recommended_video_length: int = 480    # seconds
    shorts_potential: bool = True
    color_grade: str = "tech_dark"

    # All five angles generated
    all_five_angles: List[VideoAngle] = field(default_factory=list)

    # Metadata
    sources_used: List[str] = field(default_factory=list)
    timestamp_fetched: str = ""
    is_fallback: bool = False
