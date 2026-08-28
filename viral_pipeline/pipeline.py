#!/usr/bin/env python3
"""
Viral Pipeline — Main Orchestrator
=====================================
Full automated pipeline: Trends → Script → Audio → Visuals → Assembly
→ Thumbnails → Metadata → Output package.

Usage
-----
  python pipeline.py --all                    # run every phase end-to-end
  python pipeline.py --phase 1                # only trend analysis
  python pipeline.py --phase 2 --topic "AI"  # script for a given topic
  python pipeline.py --phase 3 4 5           # multiple phases
  python pipeline.py --all --platform tiktok --style motivation-epic

Environment
-----------
  Copy .env.example → .env and fill API keys.
  All keys are optional; the pipeline falls back gracefully.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

import yaml
from dotenv import load_dotenv
from tqdm import tqdm

# ── Bootstrap: resolve project root and .env ─────────────────────────────────
HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
load_dotenv(HERE / ".env")

from utils.logger  import setup_logger, get_logger
from utils.models  import PipelineContext, TrendResult

log = setup_logger(log_dir=str(HERE / "logs"))
log = get_logger("pipeline")


# ── Config loader ─────────────────────────────────────────────────────────────

def load_config(path: Optional[str] = None,
                overrides: dict = None) -> dict:
    cfg_path = path or str(HERE / "config.yaml")
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    if overrides:
        for k, v in overrides.items():
            if "." in k:
                section, key = k.split(".", 1)
                cfg.setdefault(section, {})[key] = v
            else:
                cfg[k] = v
    return cfg


# ── Slug helper ───────────────────────────────────────────────────────────────

def make_slug(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60]


# ── Phase runners ─────────────────────────────────────────────────────────────

def run_phase1(cfg: dict, ctx: PipelineContext,
               topic_override: Optional[str] = None) -> None:
    log.info("━" * 55)
    log.info("PHASE 1 — Trend Analysis")
    from phase1_trends.scorer import run_phase1 as _p1
    if topic_override:
        from phase1_trends.fetcher import RawTrend
        from phase1_trends.scorer import expand_with_claude, _minimal_expansion
        raw = RawTrend(title=topic_override, source="manual",
                       velocity=0.9, score=0.9)
        model = cfg.get("script", {}).get("model", "claude-sonnet-4-6")
        style = cfg.get("video", {}).get("style_preset", "tech-dark")
        ctx.trend = expand_with_claude(raw, model=model, style_preset=style)
    else:
        ctx.trend = _p1(cfg)
    ctx.topic_slug = make_slug(ctx.trend.title)
    ctx.output_dir = str(Path(cfg.get("output", {}).get("base_dir", "output"))
                         / ctx.topic_slug)
    os.makedirs(ctx.output_dir, exist_ok=True)
    _save_state(ctx)
    log.info(f"  → {ctx.trend.title}")


def run_phase2(cfg: dict, ctx: PipelineContext) -> None:
    log.info("━" * 55)
    log.info("PHASE 2 — Script & Storyboard")
    if not ctx.trend:
        raise RuntimeError("Phase 1 must run before Phase 2")
    from phase2_script.generator import run_phase2 as _p2
    ctx.script = _p2(ctx.trend, cfg, ctx.output_dir)
    _save_state(ctx)
    log.info(f"  → {len(ctx.script.scenes)} scenes, {ctx.script.total_duration:.0f}s")


def run_phase3(cfg: dict, ctx: PipelineContext) -> None:
    log.info("━" * 55)
    log.info("PHASE 3 — Audio (Voice + Music + Mix)")
    if not ctx.script:
        raise RuntimeError("Phase 2 must run before Phase 3")

    audio_dir = os.path.join(ctx.output_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    # 3a: Voiceover per scene
    from phase3_audio.voiceover import run_voiceover
    ctx.audio_assets = run_voiceover(ctx.script.scenes, audio_dir, cfg)

    # 3b: Background music
    from phase3_audio.music import get_background_music
    style          = cfg.get("video", {}).get("style_preset", "tech-dark")
    ctx.music_file = get_background_music(ctx.script.total_duration, style, audio_dir)

    # 3c: Mix
    from phase3_audio.mixer import run_phase3_audio
    _, ctx.mixed_audio = run_phase3_audio(
        ctx.audio_assets, ctx.music_file,
        ctx.script.total_duration, audio_dir, cfg,
    )
    _save_state(ctx)
    log.info(f"  → Mixed audio: {ctx.mixed_audio}")


def run_phase4(cfg: dict, ctx: PipelineContext) -> None:
    log.info("━" * 55)
    log.info("PHASE 4 — Visuals (Images + B-Roll + Motion)")
    if not ctx.script:
        raise RuntimeError("Phase 2 must run before Phase 4")

    vis_dir    = os.path.join(ctx.output_dir, "visuals")
    motion_dir = os.path.join(ctx.output_dir, "motion")
    os.makedirs(vis_dir, exist_ok=True)
    os.makedirs(motion_dir, exist_ok=True)

    style = cfg.get("video", {}).get("style_preset", "tech-dark")

    from phase4_visuals.image_gen import generate_scene_image
    from phase4_visuals.broll    import get_broll
    from phase4_visuals.motion   import process_scene_visuals

    ctx.visual_assets = []
    with tqdm(ctx.script.scenes, desc="  Generating visuals", unit="scene") as bar:
        for scene in bar:
            # 4a: AI image
            va = generate_scene_image(scene, style, vis_dir, cfg)

            # 4b: B-roll (set va.video_file if found)
            broll_mp4 = get_broll(scene, va, motion_dir, cfg)
            if broll_mp4 and os.path.exists(broll_mp4):
                va.video_file = broll_mp4

            # 4c: Motion effects
            va = process_scene_visuals(scene, va, motion_dir, cfg)

            ctx.visual_assets.append(va)

    _save_state(ctx)
    log.info(f"  → {len(ctx.visual_assets)} visual assets processed")


def run_phase5(cfg: dict, ctx: PipelineContext) -> None:
    log.info("━" * 55)
    log.info("PHASE 5 — Video Assembly")
    if not ctx.script or not ctx.visual_assets or not ctx.audio_assets:
        raise RuntimeError("Phases 2-4 must run before Phase 5")
    if not ctx.mixed_audio:
        raise RuntimeError("Phase 3 must run before Phase 5")

    from phase5_assembly.assembler import run_phase5 as _p5
    raw_final = _p5(
        ctx.script, ctx.visual_assets,
        ctx.audio_assets, ctx.mixed_audio,
        ctx.output_dir, cfg,
    )

    # Add captions
    from phase5_assembly.captions import add_captions_to_video
    captioned = add_captions_to_video(
        raw_final, ctx.script, ctx.audio_assets,
        ctx.output_dir, cfg,
    )
    ctx.final_video = captioned if os.path.exists(captioned) else raw_final
    _save_state(ctx)
    size = os.path.getsize(ctx.final_video) / 1_048_576
    log.info(f"  → Final video: {ctx.final_video} ({size:.1f} MB)")


def run_phase6(cfg: dict, ctx: PipelineContext) -> None:
    log.info("━" * 55)
    log.info("PHASE 6 — Thumbnails")
    if not ctx.trend or not ctx.script:
        raise RuntimeError("Phases 1-2 must run before Phase 6")
    from phase6_thumbnails.generator import run_phase6 as _p6
    thumb_dir = os.path.join(ctx.output_dir, "thumbnails")
    ctx.thumbnails = _p6(ctx.trend, ctx.script, thumb_dir)
    _save_state(ctx)
    log.info(f"  → {len(ctx.thumbnails)} thumbnail variants")


def run_phase7(cfg: dict, ctx: PipelineContext) -> None:
    log.info("━" * 55)
    log.info("PHASE 7 — Metadata & Output Package")
    if not ctx.trend or not ctx.script:
        raise RuntimeError("Phases 1-2 must run before Phase 7")
    from phase7_metadata.generator import generate_metadata, save_output_package
    ctx.metadata = generate_metadata(ctx.trend, ctx.script, cfg)
    save_output_package(ctx, ctx.metadata, cfg)
    _save_state(ctx)


# ── State persistence ─────────────────────────────────────────────────────────

def _save_state(ctx: PipelineContext) -> None:
    if ctx.output_dir:
        os.makedirs(ctx.output_dir, exist_ok=True)
        ctx.save_state(os.path.join(ctx.output_dir, "pipeline_state.json"))


def _load_state(output_dir: str) -> Optional[PipelineContext]:
    state_file = os.path.join(output_dir, "pipeline_state.json")
    if not os.path.exists(state_file):
        return None
    try:
        import dataclasses
        from utils.models import (TrendResult, Script, Scene,
                                  AudioAsset, VisualAsset)
        with open(state_file) as f:
            d = json.load(f)
        ctx = PipelineContext(
            topic_slug=d["topic_slug"],
            output_dir=d["output_dir"],
            platform=d.get("platform", "youtube"),
        )
        if d.get("trend"):
            ctx.trend = TrendResult(**d["trend"])
        if d.get("script"):
            ctx.script = Script.from_dict(d["script"])
        if d.get("audio_assets"):
            ctx.audio_assets = [AudioAsset(**a) for a in d["audio_assets"]]
        if d.get("music_file"):
            ctx.music_file = d["music_file"]
        if d.get("mixed_audio"):
            ctx.mixed_audio = d["mixed_audio"]
        if d.get("visual_assets"):
            ctx.visual_assets = [VisualAsset(**v) for v in d["visual_assets"]]
        ctx.final_video = d.get("final_video")
        ctx.thumbnails  = d.get("thumbnails", [])
        ctx.metadata    = d.get("metadata")
        return ctx
    except Exception as e:
        log.warning(f"Could not load state: {e}")
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Viral Video Pipeline — automated trending video production",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--all", action="store_true",
                        help="Run all phases end-to-end")
    parser.add_argument("--phase", nargs="+", type=int,
                        choices=range(1, 8),
                        help="Run specific phase(s): 1-7")
    parser.add_argument("--topic", type=str, default=None,
                        help="Skip trend fetch and use this topic directly")
    parser.add_argument("--platform", type=str,
                        choices=["youtube", "tiktok", "reels"],
                        default=None)
    parser.add_argument("--style",
                        choices=["tech-dark", "vlog-warm",
                                 "news-clean", "motivation-epic"],
                        default=None)
    parser.add_argument("--config", type=str, default=None,
                        help="Path to custom config.yaml")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Override output directory")
    parser.add_argument("--resume", type=str, default=None,
                        help="Resume a run from a pipeline_state.json path")
    parser.add_argument("--no-upload", action="store_true",
                        help="Skip YouTube upload even if Phase 7 runs")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.verbose:
        logging.getLogger("viral_pipeline").setLevel(logging.DEBUG)

    # Load config with CLI overrides
    overrides = {}
    if args.platform:
        overrides["video.platform"] = args.platform
    if args.style:
        overrides["video.style_preset"] = args.style

    cfg = load_config(args.config, overrides)

    # Change working dir to pipeline root
    os.chdir(HERE)

    # Determine which phases to run
    if args.all:
        phases = list(range(1, 8))
    elif args.phase:
        phases = sorted(set(args.phase))
    else:
        print("Specify --all or --phase <1-7>")
        sys.exit(1)

    # Build or resume context
    ctx: PipelineContext
    if args.resume and os.path.exists(args.resume):
        state_dir = os.path.dirname(args.resume)
        ctx = _load_state(state_dir) or PipelineContext(
            topic_slug="resume", output_dir=state_dir)
        log.info(f"Resuming from: {args.resume}")
    else:
        base_dir = (args.output_dir
                    or cfg.get("output", {}).get("base_dir", "output"))
        ctx = PipelineContext(
            topic_slug="pending",
            output_dir=str(Path(base_dir) / "pending"),
            platform=cfg.get("video", {}).get("platform", "youtube"),
        )

    # ── Run phases ────────────────────────────────────────────────────────────
    start = time.monotonic()
    RUNNERS = {
        1: lambda: run_phase1(cfg, ctx, args.topic),
        2: lambda: run_phase2(cfg, ctx),
        3: lambda: run_phase3(cfg, ctx),
        4: lambda: run_phase4(cfg, ctx),
        5: lambda: run_phase5(cfg, ctx),
        6: lambda: run_phase6(cfg, ctx),
        7: lambda: run_phase7(cfg, ctx),
    }

    for p in phases:
        try:
            RUNNERS[p]()
        except Exception as e:
            log.error(f"Phase {p} failed: {e}", exc_info=True)
            log.error("Pipeline aborted. Fix the error and re-run with --resume.")
            sys.exit(1)

    elapsed = time.monotonic() - start
    log.info("━" * 55)
    log.info(f"Pipeline complete in {elapsed:.1f}s")
    if ctx.final_video:
        log.info(f"Video : {ctx.final_video}")
    if ctx.thumbnails:
        for p in ctx.thumbnails:
            log.info(f"Thumb : {p}")
    if ctx.output_dir:
        log.info(f"All files in: {ctx.output_dir}/")


if __name__ == "__main__":
    main()
