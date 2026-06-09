"""
core/cinematic_daily_pipeline.py
Full Hollywood-quality daily video production pipeline.

Stages:
  1. INTELLIGENCE  — trend analysis → best opportunity
  2. SCRIPT        — cinematic screenplay generation
  3. AUDIO         — broadcast-quality voice + music
  4. VISUALS       — 1080p 24fps animated scenes
  5. EDITING       — color grade + transitions
  6. QUALITY       — compare vs previous video
  7. UPLOAD        — YouTube with full metadata

Usage:
  python core/cinematic_daily_pipeline.py --now
  python core/cinematic_daily_pipeline.py --schedule
  python core/cinematic_daily_pipeline.py --test --topic "AI just changed everything"
  python core/cinematic_daily_pipeline.py --now --no-upload
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

# ── Logging setup ─────────────────────────────────────────────────────────────
LOGS_DIR = HERE / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
today_str = datetime.date.today().isoformat()
log_file  = LOGS_DIR / f"cinematic_{today_str}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("pipeline")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider(label: str = ""):
    line = "═" * 60
    if label:
        pad = (60 - len(label) - 2) // 2
        log.info(f"{'═' * pad} {label} {'═' * pad}")
    else:
        log.info(line)


def _elapsed(start: float) -> str:
    s = time.time() - start
    return f"{s:.1f}s" if s < 60 else f"{s/60:.1f}min"


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_cinematic_pipeline(upload: bool = True, test_topic: str = "") -> dict:
    """
    Execute the full cinematic production pipeline.
    Returns production report dict.
    """
    pipeline_start = time.time()
    date_tag = today_str
    report: dict = {
        "date": date_tag,
        "stages": {},
        "output_files": {},
        "errors": [],
        "success": False,
    }

    # ── Output directory ──────────────────────────────────────────────────────
    out_base = HERE / "output" / date_tag
    out_base.mkdir(parents=True, exist_ok=True)
    shorts_dir    = out_base / "shorts"
    thumbnails_dir = out_base / "thumbnails"
    audio_dir     = out_base / "audio"
    shorts_dir.mkdir(exist_ok=True)
    thumbnails_dir.mkdir(exist_ok=True)
    audio_dir.mkdir(exist_ok=True)

    # ── Stage 1: INTELLIGENCE ─────────────────────────────────────────────────
    _divider("STAGE 1 — TREND INTELLIGENCE")
    stage_start = time.time()
    content = {}

    try:
        if test_topic:
            log.info(f"TEST MODE — using topic: {test_topic}")
            from content_generator import build_daily_content
            content = build_daily_content()
            content["topic"] = test_topic
            content["hook"] = f"What {test_topic} means for you."
        else:
            try:
                from modules.intelligence.trend_analyzer import get_best_opportunity_sync
                opportunity = get_best_opportunity_sync()
                content = {
                    "topic":    opportunity.topic,
                    "hook":     opportunity.hook,
                    "bullets":  opportunity.key_facts[:5],
                    "takeaway": opportunity.key_facts[-1] if opportunity.key_facts else "",
                    "category": opportunity.category,
                    "tags":     [],
                    "date":     datetime.date.today(),
                    "trend_score": opportunity.score,
                    "predicted_ctr": opportunity.predicted_ctr,
                }
                log.info(f"Trend: {opportunity.topic} (score={opportunity.score:.2f})")
            except Exception as e:
                log.warning(f"Trend analysis failed ({e}), using content library")
                from content_generator import build_daily_content
                content = build_daily_content()

        # Fallback: ensure all fields exist
        content.setdefault("date", datetime.date.today())
        content.setdefault("bullets", ["Key insight about this topic."] * 5)
        content.setdefault("takeaway", "Take action on what you learned today.")
        content.setdefault("category", "Tech")
        content.setdefault("tags", [])

        log.info(f"Topic    : {content['topic']}")
        log.info(f"Category : {content['category']}")
        report["stages"]["intelligence"] = {
            "ok": True, "topic": content["topic"], "elapsed": _elapsed(stage_start)
        }
    except Exception as e:
        log.error(f"Intelligence stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"intelligence: {e}")
        # Hard fallback
        from content_generator import build_daily_content
        content = build_daily_content()
        report["stages"]["intelligence"] = {"ok": False, "error": str(e)}

    # ── Stage 2: SCRIPT ───────────────────────────────────────────────────────
    _divider("STAGE 2 — CINEMATIC SCRIPT")
    stage_start = time.time()
    script = None

    try:
        from modules.production.cinematic_script_writer import CinematicScriptWriter
        writer = CinematicScriptWriter()
        script = writer.generate(content)
        log.info(f"Script quality score: {script.quality_score:.1f}/100")
        log.info(f"Total duration: {script.total_duration_s}s, words: {script.word_count}")

        if script.quality_score < writer.QUALITY_THRESHOLD:
            log.warning(f"Script quality {script.quality_score:.1f} below threshold "
                        f"{writer.QUALITY_THRESHOLD} — continuing anyway")

        # Save script
        script_path = out_base / "script.txt"
        with open(script_path, "w") as f:
            f.write(f"TOPIC: {script.topic}\n")
            f.write(f"HOOK: {script.hook}\n")
            f.write(f"QUALITY SCORE: {script.quality_score:.1f}/100\n\n")
            for scene in script.scenes:
                f.write(f"[{scene.timestamp_start}–{scene.timestamp_end}] "
                        f"Scene {scene.scene_id}: {scene.visual.type.upper()}\n")
                f.write(f"  VO: {scene.audio.voiceover}\n")
                f.write(f"  TONE: {scene.audio.tone}  TRIGGER: {scene.psychological_trigger}\n\n")

        report["stages"]["script"] = {
            "ok": True,
            "quality_score": script.quality_score,
            "duration_s": script.total_duration_s,
            "elapsed": _elapsed(stage_start),
        }
    except Exception as e:
        log.error(f"Script stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"script: {e}")
        report["stages"]["script"] = {"ok": False, "error": str(e)}

    # ── Stage 3: AUDIO ────────────────────────────────────────────────────────
    _divider("STAGE 3 — BROADCAST AUDIO")
    stage_start = time.time()

    voice_path = str(audio_dir / f"voice_{date_tag}.wav")
    music_path = str(audio_dir / f"music_{date_tag}.wav")
    mixed_path = str(audio_dir / f"mixed_{date_tag}.wav")

    try:
        from modules.production.cinematic_audio_engine import CinematicAudioEngine
        audio_engine = CinematicAudioEngine()

        # Build narration text
        if script:
            from modules.production.cinematic_script_writer import CinematicScriptWriter
            narration = CinematicScriptWriter().build_voiceover_text(script)
        else:
            # Fallback to old script builder
            from audio_generator import build_script
            narration = build_script(content)

        log.info(f"Narration: {len(narration.split())} words")

        # Generate voice
        tone = "authoritative"
        if content.get("category") in ("Money", "Career"):
            tone = "urgent"
        elif content.get("category") in ("Health", "Mindset"):
            tone = "warm"

        voice_path = audio_engine.generate_voiceover_sync(narration, voice_path, tone=tone)
        log.info(f"Voice: {voice_path}")

        # Generate music
        total_s = script.total_duration_s if script else 50
        music_path = audio_engine.generate_background_music(
            duration_s=total_s + 5,
            out_path=music_path,
            energy="medium",
        )
        log.info(f"Music: {music_path}")

        # Mix
        mixed_path = audio_engine.mix_audio(voice_path, music_path, mixed_path, total_s)
        log.info(f"Mixed: {mixed_path}")

        report["stages"]["audio"] = {"ok": True, "elapsed": _elapsed(stage_start)}
        report["output_files"]["voice"] = voice_path
        report["output_files"]["music"] = music_path
        report["output_files"]["mixed_audio"] = mixed_path

    except Exception as e:
        log.error(f"Audio stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"audio: {e}")
        report["stages"]["audio"] = {"ok": False, "error": str(e)}

        # FALLBACK: old audio pipeline
        log.info("Falling back to legacy audio pipeline...")
        try:
            from audio_generator import build_script, generate_voiceover, \
                generate_background_music, mix_audio
            narration = build_script(content)
            generate_voiceover(narration, voice_path)
            total_s = script.total_duration_s if script else 50
            generate_background_music(total_s + 5, music_path)
            mix_audio(voice_path, music_path, mixed_path, total_s)
            log.info("Legacy audio pipeline succeeded")
        except Exception as e2:
            log.error(f"Legacy audio also failed: {e2}")
            report["errors"].append(f"audio_fallback: {e2}")

    # ── Stage 4: VISUALS ──────────────────────────────────────────────────────
    _divider("STAGE 4 — HOLLYWOOD VISUALS")
    stage_start = time.time()

    silent_path = str(out_base / f"silent_{date_tag}.mp4")
    final_path  = str(out_base / f"mind_fuel_{date_tag}.mp4")

    try:
        from modules.production.hollywood_visual_engine import HollywoodVisualEngine

        # Pick style based on category
        style_map = {
            "Tech": "tech_dark",
            "Money": "tech_dark",
            "Career": "tech_dark",
            "Health": "education",
            "Science": "documentary",
            "Mindset": "education",
            "Lifestyle": "education",
        }
        style = style_map.get(content.get("category", "Tech"), "tech_dark")

        visual_engine = HollywoodVisualEngine(style=style)
        silent_path, total_s = visual_engine.generate_video(content, silent_path)
        log.info(f"Visual: {silent_path} ({total_s:.1f}s)")

        report["stages"]["visuals"] = {
            "ok": True, "duration_s": total_s, "elapsed": _elapsed(stage_start)
        }
        report["output_files"]["silent_video"] = silent_path

    except Exception as e:
        log.error(f"Visual stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"visuals: {e}")
        report["stages"]["visuals"] = {"ok": False, "error": str(e)}

        # FALLBACK: old video generator
        log.info("Falling back to legacy video pipeline...")
        try:
            from video_generator import generate_video
            result = generate_video(content, silent_path)
            total_s = result[1] if isinstance(result, tuple) else 50
            log.info(f"Legacy video generated: {silent_path}")
        except Exception as e2:
            log.error(f"Legacy video also failed: {e2}")
            report["errors"].append(f"visuals_fallback: {e2}")
            total_s = 50

    # ── Mux audio + video ─────────────────────────────────────────────────────
    _divider("MUXING")
    try:
        mux_cmd = [
            "ffmpeg", "-y",
            "-i", silent_path,
            "-i", mixed_path,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            final_path,
        ]
        result = subprocess.run(mux_cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode()[-300:])
        size_mb = os.path.getsize(final_path) / 1_048_576
        log.info(f"Final video: {final_path} ({size_mb:.1f} MB)")
        report["output_files"]["final_video"] = final_path
    except Exception as e:
        log.error(f"Mux failed: {e}")
        report["errors"].append(f"mux: {e}")
        # Try copying silent video as fallback
        shutil.copy2(silent_path, final_path)
        report["output_files"]["final_video"] = final_path

    # ── Stage 5: COLOR GRADING (optional post-processing) ─────────────────────
    _divider("STAGE 5 — COLOR GRADE")
    try:
        from modules.production.cinematic_editor import CinematicEditor
        editor = CinematicEditor()

        grade = "tech_dark"
        graded_path = str(out_base / f"mind_fuel_{date_tag}_graded.mp4")
        graded_path = editor.apply_color_grade(final_path, graded_path, grade)
        if os.path.exists(graded_path) and os.path.getsize(graded_path) > 1_000_000:
            final_path = graded_path
            report["output_files"]["final_video"] = final_path
            log.info(f"Color graded: {final_path}")
        report["stages"]["color_grade"] = {"ok": True}
    except Exception as e:
        log.warning(f"Color grade skipped ({e})")
        report["stages"]["color_grade"] = {"ok": False, "error": str(e)}

    # ── Stage 6: QUALITY REPORT ───────────────────────────────────────────────
    _divider("STAGE 6 — QUALITY CHECK")
    try:
        from modules.evolution.quality_comparator import QualityComparator
        comparator = QualityComparator()

        # Find previous video for comparison
        output_root = HERE / "output"
        prev_video = None
        for d in sorted(output_root.iterdir(), reverse=True):
            if d.name != date_tag and d.is_dir():
                vids = list(d.glob("mind_fuel_*.mp4"))
                if vids:
                    prev_video = str(vids[0])
                    break

        if prev_video and os.path.exists(prev_video):
            comparison = comparator.compare(prev_video, final_path)
            report_path = str(LOGS_DIR / "quality" / f"comparison_{date_tag}.txt")
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)
            comparator.generate_report(comparison, report_path)
            log.info(f"Quality: OLD {comparison['old_score']}/100 → "
                     f"NEW {comparison['new_score']}/100 "
                     f"(+{comparison['improvement_pct']}%)")
            report["stages"]["quality"] = {
                "ok": True,
                "new_score": comparison["new_score"],
                "improvement_pct": comparison["improvement_pct"],
            }
        else:
            metrics = comparator.analyze_video(final_path)
            score   = metrics.get("overall_score", 0)
            log.info(f"Quality score: {score}/100 (no previous video to compare)")
            report["stages"]["quality"] = {"ok": True, "new_score": score}
    except Exception as e:
        log.warning(f"Quality check skipped ({e})")
        report["stages"]["quality"] = {"ok": False, "error": str(e)}

    # ── Stage 7: UPLOAD ───────────────────────────────────────────────────────
    if upload:
        _divider("STAGE 7 — YOUTUBE UPLOAD")
        stage_start = time.time()
        try:
            from youtube_uploader import upload_video
            video_url = upload_video(final_path, content)
            log.info(f"Uploaded → {video_url}")
            report["stages"]["upload"] = {
                "ok": True, "url": video_url, "elapsed": _elapsed(stage_start)
            }
            report["output_files"]["youtube_url"] = video_url
        except Exception as e:
            log.error(f"Upload failed: {e}\n{traceback.format_exc()}")
            report["errors"].append(f"upload: {e}")
            report["stages"]["upload"] = {"ok": False, "error": str(e)}
    else:
        log.info("Upload skipped (--no-upload flag)")
        report["stages"]["upload"] = {"ok": True, "skipped": True}

    # ── Final report ──────────────────────────────────────────────────────────
    report["success"]       = len(report["errors"]) == 0
    report["total_elapsed"] = _elapsed(pipeline_start)

    report_path = out_base / "production_log.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    _divider("COMPLETE")
    status = "✅ SUCCESS" if report["success"] else f"⚠️  COMPLETED WITH {len(report['errors'])} ERRORS"
    log.info(f"Pipeline: {status} in {report['total_elapsed']}")
    for stage, data in report["stages"].items():
        ok = "✅" if data.get("ok") else "❌"
        log.info(f"  {ok}  {stage}")
    if report["errors"]:
        for e in report["errors"]:
            log.warning(f"  ERROR: {e}")
    if report["output_files"].get("youtube_url"):
        log.info(f"  🎬  {report['output_files']['youtube_url']}")

    return report


# ── Scheduler ─────────────────────────────────────────────────────────────────

def run_scheduled(upload: bool = True):
    """Run daily pipeline at configured upload time."""
    try:
        import config
        upload_time = config.UPLOAD_TIME
    except Exception:
        upload_time = "15:00"

    log.info(f"Cinematic scheduler started — daily at {upload_time} UTC")

    try:
        import schedule
        schedule.every().day.at(upload_time).do(
            lambda: _safe_run(upload)
        )
        while True:
            schedule.run_pending()
            time.sleep(30)
    except ImportError:
        log.warning("schedule not installed — running once immediately")
        run_cinematic_pipeline(upload=upload)


def _safe_run(upload: bool):
    try:
        run_cinematic_pipeline(upload=upload)
    except Exception:
        log.error("Pipeline crashed:\n" + traceback.format_exc())


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cinematic YouTube Pipeline")
    parser.add_argument("--now",       action="store_true", help="Run immediately")
    parser.add_argument("--schedule",  action="store_true", help="Run daily on schedule")
    parser.add_argument("--no-upload", action="store_true", help="Generate only, don't upload")
    parser.add_argument("--test",      action="store_true", help="Test mode")
    parser.add_argument("--topic",     type=str, default="", help="Override topic for test")
    args = parser.parse_args()

    do_upload = not args.no_upload

    if args.now or args.test:
        run_cinematic_pipeline(upload=do_upload, test_topic=args.topic)
    elif args.schedule:
        run_scheduled(upload=do_upload)
    else:
        parser.print_help()
