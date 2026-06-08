"""
Phase 2 — Script & Storyboard Generator
=========================================
Uses Claude to produce a viral-optimised video script with structured JSON
scenes, then builds a scene-by-scene storyboard from it.
"""
import json
import logging
import os
import re
from typing import Dict, Any, List

import anthropic

from utils.models import TrendResult, Script, Scene

log = logging.getLogger("viral_pipeline.script")


# ── Style-specific visual direction ──────────────────────────────────────────
STYLE_DIRECTION = {
    "tech-dark": (
        "Dark, moody, cinematic. Deep navy/charcoal backgrounds. "
        "Neon cyan/blue accent colours. Futuristic UI elements."
    ),
    "vlog-warm": (
        "Warm, authentic, approachable. Golden-hour lighting. "
        "Natural textures. Muted warm tones with orange/yellow accents."
    ),
    "news-clean": (
        "Professional, clean, authoritative. Dark studio background. "
        "Bold red/white graphics. Lower-thirds style text."
    ),
    "motivation-epic": (
        "Epic, dramatic, inspiring. Deep purples/blacks. "
        "Purple/magenta neon. Particle effects. High-energy pacing."
    ),
}

# ── Platform timing constraints ───────────────────────────────────────────────
PLATFORM_DURATION = {
    "youtube":  {"min": 480, "max": 720, "default": 600},  # 8-12 min
    "tiktok":   {"min": 45,  "max": 60,  "default": 55},
    "reels":    {"min": 30,  "max": 90,  "default": 60},
}


def _duration_for_platform(platform: str, mode: str) -> int:
    if mode == "short":
        return 55
    if mode == "standard":
        return 600
    return PLATFORM_DURATION.get(platform, PLATFORM_DURATION["youtube"])["default"]


def build_script_prompt(trend: TrendResult, cfg: dict) -> str:
    platform   = cfg.get("video", {}).get("platform", "youtube")
    style      = cfg.get("video", {}).get("style_preset", "tech-dark")
    duration   = _duration_for_platform(
        platform, cfg.get("video", {}).get("duration_mode", "auto"))
    visual_dir = STYLE_DIRECTION.get(style, STYLE_DIRECTION["tech-dark"])
    spm        = cfg.get("script", {}).get("scenes_per_minute", 4)
    n_scenes   = max(6, int(duration / 60 * spm))

    return f"""You are a world-class viral YouTube scriptwriter.

TOPIC: {trend.title}
AUDIENCE: {trend.target_audience}
PLATFORM: {platform} (~{duration}s total)
STYLE: {style} — {visual_dir}
TALKING POINTS:
{chr(10).join(f"  - {p}" for p in trend.talking_points)}
EMOTIONAL HOOKS:
{chr(10).join(f"  - {h}" for h in trend.emotional_hooks)}

Write a complete video script optimised for maximum retention using:
1. HOOK (0-3s): Jaw-dropping statement or question that forces viewers to keep watching
2. PATTERN INTERRUPT (3-7s): Subvert expectations immediately
3. VALUE LOOP: Promise + partial reveal × {n_scenes - 3} times to keep viewers hooked
4. CTA (last 15s): Subscribe + engage call-to-action

REQUIREMENTS:
- Each scene 10-45 seconds of spoken content
- Target ~{n_scenes} scenes total (can vary ±2)
- text_overlay = SHORT punchy on-screen text (max 8 words, CAPS for emphasis)
- visual = detailed image generation prompt for that scene
- transition = one of: cut | fade | zoom_punch | glitch | slide
- shot_type = one of: extreme_close | close | medium | wide | aerial | screen_capture

Return ONLY a valid JSON object. No markdown, no explanation:
{{
  "title": "YouTube-optimised title (max 70 chars)",
  "platform": "{platform}",
  "style": "{style}",
  "target_audience": "{trend.target_audience}",
  "hook": "the 3-second hook sentence",
  "cta": "the CTA line",
  "total_duration": <number in seconds>,
  "scenes": [
    {{
      "id": 1,
      "duration": <seconds as float>,
      "voiceover": "exact words spoken in this scene",
      "visual": "detailed SD/Midjourney-style image prompt for this scene",
      "text_overlay": "SHORT ON-SCREEN TEXT",
      "transition": "cut",
      "shot_type": "close",
      "broll_description": "what B-roll footage would work here",
      "pacing": "fast",
      "keywords": ["kw1", "kw2"]
    }}
  ]
}}"""


def generate_script(trend: TrendResult, cfg: dict) -> Script:
    """Call Claude to produce the full script JSON, parse and return Script."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — building fallback script")
        return _fallback_script(trend, cfg)

    model    = cfg.get("script", {}).get("model", "claude-sonnet-4-6")
    max_tok  = cfg.get("script", {}).get("max_tokens", 4096)
    prompt   = build_script_prompt(trend, cfg)

    log.info(f"  Calling Claude ({model}) for script generation…")
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=max_tok,
            system=(
                "You are an expert viral video scriptwriter. "
                "Always output valid JSON only. No markdown fences."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip any accidental markdown fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error: {e}\nRaw: {raw[:500]}")
        return _fallback_script(trend, cfg)
    except Exception as e:
        log.error(f"Claude call failed: {e}")
        return _fallback_script(trend, cfg)

    return _parse_script_data(data)


def _parse_script_data(data: Dict[str, Any]) -> Script:
    scenes = []
    for s in data.get("scenes", []):
        scenes.append(Scene(
            id=int(s.get("id", len(scenes) + 1)),
            duration=float(s.get("duration", 15)),
            voiceover=s.get("voiceover", ""),
            visual=s.get("visual", "cinematic dark background"),
            text_overlay=s.get("text_overlay", ""),
            transition=s.get("transition", "cut"),
            shot_type=s.get("shot_type", "medium"),
            broll_description=s.get("broll_description", ""),
            pacing=s.get("pacing", "normal"),
            keywords=s.get("keywords", []),
        ))

    total = sum(s.duration for s in scenes)
    return Script(
        topic=data.get("title", "Untitled"),
        title=data.get("title", "Untitled"),
        platform=data.get("platform", "youtube"),
        total_duration=total,
        scenes=scenes,
        style=data.get("style", "tech-dark"),
        target_audience=data.get("target_audience", "general"),
        hook=data.get("hook", ""),
        cta=data.get("cta", "Subscribe for more."),
        description=data.get("description", ""),
    )


def _fallback_script(trend: TrendResult, cfg: dict) -> Script:
    """Build a minimal script from trend data when Claude is unavailable."""
    platform = cfg.get("video", {}).get("platform", "youtube")
    style    = cfg.get("video", {}).get("style_preset", "tech-dark")
    hook_text = (trend.emotional_hooks[0]
                 if trend.emotional_hooks
                 else f"What you're about to learn will change how you see {trend.topic}.")

    scenes = [
        Scene(id=1, duration=6,  voiceover=hook_text,
              visual=f"dramatic cinematic opener, {style}, 4K",
              text_overlay="WAIT FOR IT…", transition="zoom_punch",
              shot_type="extreme_close", pacing="fast"),
        Scene(id=2, duration=8,  voiceover=f"Today we're covering {trend.title}.",
              visual=f"title card, bold typography, {style}",
              text_overlay=trend.title[:60].upper(), transition="cut",
              shot_type="medium", pacing="normal"),
    ]
    for i, point in enumerate(trend.talking_points[:5], 3):
        scenes.append(Scene(
            id=i, duration=45,
            voiceover=f"Number {i - 2}. {point}",
            visual=f"{point}, cinematic, {style}, ultra detailed",
            text_overlay=f"#{i-2}: {point[:40].upper()}",
            transition="cut", shot_type="medium", pacing="normal",
        ))
    scenes.append(Scene(
        id=len(scenes)+1, duration=15,
        voiceover="If this helped you, hit subscribe and tap the bell.",
        visual="subscribe call to action, animated bell icon",
        text_overlay="SUBSCRIBE NOW", transition="fade",
        shot_type="close", pacing="slow",
    ))

    total = sum(s.duration for s in scenes)
    return Script(
        topic=trend.topic, title=trend.title,
        platform=platform, total_duration=total,
        scenes=scenes, style=style,
        target_audience=trend.target_audience,
        hook=hook_text,
        cta="Subscribe and hit the bell for daily content.",
    )


def save_storyboard(script: Script, path: str) -> None:
    """Save storyboard as pretty JSON (PDF requires reportlab — skipped by default)."""
    with open(path, "w") as f:
        json.dump({
            "title": script.title,
            "platform": script.platform,
            "total_duration": script.total_duration,
            "style": script.style,
            "target_audience": script.target_audience,
            "hook": script.hook,
            "cta": script.cta,
            "scenes": [s.to_dict() for s in script.scenes],
        }, f, indent=2)
    log.info(f"  Storyboard saved → {path}")


def run_phase2(trend: TrendResult, cfg: dict,
               output_dir: str) -> Script:
    """Full Phase 2 entry point."""
    log.info("Phase 2: Generating script & storyboard…")
    script = generate_script(trend, cfg)
    log.info(f"  Scenes   : {len(script.scenes)}")
    log.info(f"  Duration : {script.total_duration:.0f}s")
    log.info(f"  Hook     : {script.hook[:80]}…")

    import os
    os.makedirs(output_dir, exist_ok=True)
    save_storyboard(script, os.path.join(output_dir, "storyboard.json"))
    with open(os.path.join(output_dir, "script.txt"), "w") as f:
        f.write(f"TITLE: {script.title}\n\n")
        for s in script.scenes:
            f.write(f"[Scene {s.id} — {s.duration:.0f}s — {s.shot_type}]\n")
            f.write(f"{s.voiceover}\n")
            if s.text_overlay:
                f.write(f"ON SCREEN: {s.text_overlay}\n")
            f.write("\n")

    return script
