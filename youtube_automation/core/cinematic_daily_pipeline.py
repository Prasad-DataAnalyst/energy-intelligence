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


# ── Self-enhancement loop: start immediately in background ────────────────────
try:
    from modules.intelligence.self_enhancement_loop import start_background_loop
    _enhancement_loop = start_background_loop()
    log.info("SelfEnhancementLoop started in background")
except Exception as _enh_exc:
    log.debug("SelfEnhancementLoop skipped: %s", _enh_exc)


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
                from modules.intelligence.trend_analyzer import TrendAnalyzer
                analyzer = TrendAnalyzer()
                opportunity = analyzer.get_best_opportunity_sync()
                content = {
                    "topic":        opportunity.topic,
                    "hook":         opportunity.hook_sentence,
                    "bullets":      opportunity.key_facts[:5],
                    "takeaway":     opportunity.key_facts[-1] if opportunity.key_facts else "",
                    "category":     opportunity.category,
                    "tags":         [],
                    "date":         datetime.date.today(),
                    "trend_score":  opportunity.opportunity_score,
                    "predicted_ctr": opportunity.predicted_ctr_range,
                    "rpm_category": opportunity.rpm_category,
                    "color_grade":  opportunity.color_grade,
                    "_opportunity": opportunity,  # keep full object for packager
                }
                log.info(f"Trend: {opportunity.topic} (score={opportunity.opportunity_score:.2f})")
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
        opportunity = content.get("_opportunity")
        script = writer.generate(opportunity) if opportunity else writer.generate(content)
        log.info(f"Script quality score: {script.quality_score:.1f}/100")
        log.info(f"Total duration: {script.total_duration_seconds}s, words: {script.word_count}")

        if script.quality_score < 70.0:
            log.warning(f"Script quality {script.quality_score:.1f} below 70 — continuing anyway")

        # Save script
        script_path = out_base / "script.txt"
        with open(script_path, "w") as f:
            f.write(f"TOPIC: {script.title}\n")
            f.write(f"HOOK: {script.hook_sentence}\n")
            f.write(f"QUALITY SCORE: {script.quality_score:.1f}/100\n")
            f.write(f"DURATION: {script.total_duration_seconds}s | WORDS: {script.word_count}\n\n")
            for scene in script.scenes:
                f.write(f"[{scene.timestamp_start}–{scene.timestamp_end}] "
                        f"Scene {scene.scene_id}: {scene.visual.type.upper()}\n")
                f.write(f"  VO: {scene.audio.voiceover}\n")
                f.write(f"  TONE: {scene.audio.tone}  TRIGGER: {scene.psychological_trigger}\n\n")

        report["stages"]["script"] = {
            "ok": True,
            "quality_score": script.quality_score,
            "duration_s": script.total_duration_seconds,
            "word_count": script.word_count,
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
        if script and hasattr(script, 'scenes') and script.scenes:
            narration = " ".join(
                s.audio.voiceover for s in script.scenes if s.audio.voiceover
            )
        elif script:
            try:
                from modules.production.cinematic_script_writer import CinematicScriptWriter
                narration = CinematicScriptWriter().build_voiceover_text(script)
            except Exception:
                narration = script.hook_sentence
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
        total_s = getattr(script, "total_duration_seconds", None) or getattr(script, "total_duration_s", None) or 50
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
            total_s = getattr(script, "total_duration_seconds", None) or getattr(script, "total_duration_s", None) or 50
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
        result = visual_engine.generate_video(content, silent_path)
        silent_path, total_s = (result if isinstance(result, tuple) else (result, 50))
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

    # ── Record outcome for self-enhancement loop ─────────────────────────────
    try:
        from modules.intelligence.self_enhancement_loop import get_loop
        q_score = report.get("stages", {}).get("quality", {}).get("new_score", 0) or 0
        sources_used = [
            s for s in [
                "google_trends", "hackernews", "reddit_all",
                content.get("rpm_category", ""), content.get("category", ""),
            ] if s
        ]
        get_loop().record_video_outcome(sources_used, float(q_score))
    except Exception:
        pass

    # ── Stage 6.5: SEO PACKAGE ────────────────────────────────────────────────
    _divider("STAGE 6.5 — SEO PACKAGE")
    seo_package = None
    try:
        from modules.youtube.youtube_packager import YouTubePackager
        packager = YouTubePackager()
        opportunity = content.get("_opportunity")
        seo_package = packager.generate_package(script, opportunity, final_path)
        # Merge SEO data into content for upload
        if seo_package:
            content["title"]       = seo_package.get("selected_title", content.get("topic", ""))
            content["description"] = seo_package.get("description", "")
            content["tags"]        = seo_package.get("tags", content.get("tags", []))
            pkg_path = out_base / "seo_package.json"
            with open(pkg_path, "w") as f:
                json.dump(seo_package, f, indent=2, default=str)
            log.info(f"SEO package saved: {pkg_path}")
            log.info(f"Selected title: {content['title']}")
        report["stages"]["seo_package"] = {"ok": True, "title": content.get("title", "")}
    except Exception as e:
        log.warning(f"SEO package skipped ({e})")
        report["stages"]["seo_package"] = {"ok": False, "error": str(e)}

    # ── Quality gate enforcement ───────────────────────────────────────────────
    quality_score = (
        report.get("stages", {}).get("quality", {}).get("new_score") or 0
    )
    if upload and quality_score and quality_score < 50:
        log.error(
            f"Quality score {quality_score}/100 is below minimum 50 — "
            "video NOT uploaded. Saved for manual review."
        )
        (out_base / "HELD_FOR_REVIEW.txt").write_text(
            f"Score: {quality_score}/100 (min 50)\n"
            f"File: {final_path}\n"
            f"Date: {date_tag}\n"
        )
        report["errors"].append(f"quality_gate: score {quality_score} < 50")
        report["stages"]["upload"] = {
            "ok": False,
            "reason": f"quality gate ({quality_score}/100 < 50)",
        }
        upload = False

    # ── Stage 7: UPLOAD ───────────────────────────────────────────────────────
    video_id = None
    if upload:
        _divider("STAGE 7 — YOUTUBE UPLOAD")
        stage_start = time.time()
        try:
            import youtube_uploader
            video_id = youtube_uploader.upload_video(final_path, content, seo_package)
            video_url = f"https://youtu.be/{video_id}" if video_id else ""
            log.info(f"Uploaded → {video_url}")
            report["stages"]["upload"] = {
                "ok": True,
                "video_id": video_id,
                "url": video_url,
                "elapsed": _elapsed(stage_start),
            }
            report["output_files"]["youtube_url"] = video_url
        except Exception as e:
            log.error(f"Upload failed: {e}\n{traceback.format_exc()}")
            report["errors"].append(f"upload: {e}")
            report["stages"]["upload"] = {"ok": False, "error": str(e)}
    else:
        if not report["stages"].get("upload"):
            log.info("Upload skipped (--no-upload flag)")
            report["stages"]["upload"] = {"ok": True, "skipped": True}

    # ── Stage 8: POST-UPLOAD AUTOMATION ───────────────────────────────────────
    if video_id:
        _divider("STAGE 8 — POST-UPLOAD AUTOMATION")
        video_url = f"https://youtu.be/{video_id}"

        # 8a — Thumbnail
        try:
            from modules.youtube.thumbnail_generator import generate_and_upload
            ok = generate_and_upload(
                video_id,
                content.get("title", ""),
                seo_package=seo_package,
                work_dir=str(thumbnails_dir),
            )
            log.info(f"Thumbnail: {'uploaded' if ok else 'failed'}")
            report["stages"]["thumbnail"] = {"ok": ok}
        except Exception as e:
            log.warning(f"Thumbnail stage failed: {e}")
            report["stages"]["thumbnail"] = {"ok": False, "error": str(e)}

        # 8b — YouTube Shorts
        try:
            from modules.youtube.shorts_publisher import publish_all_shorts
            short_ids = publish_all_shorts(
                final_path,
                content.get("title", ""),
                seo_package=seo_package,
                script=script,
                work_dir=str(shorts_dir),
            )
            log.info(f"Shorts published: {len(short_ids)}")
            report["stages"]["shorts"] = {"ok": True, "count": len(short_ids), "ids": short_ids}
        except Exception as e:
            log.warning(f"Shorts stage failed: {e}")
            report["stages"]["shorts"] = {"ok": False, "error": str(e)}

        # 8c — Pinned comment (5 min delay)
        try:
            from modules.youtube.post_scheduler import schedule_pinned_comment
            schedule_pinned_comment(video_id, delay_minutes=5)
            log.info("Pinned comment scheduled (+5 min)")
            report["stages"]["pinned_comment"] = {"ok": True}
        except Exception as e:
            log.warning(f"Pin comment schedule failed: {e}")
            report["stages"]["pinned_comment"] = {"ok": False, "error": str(e)}

        # 8d — Community post (60 min delay)
        try:
            from modules.youtube.post_scheduler import schedule_community_post
            schedule_community_post(video_id, video_url, delay_minutes=60)
            log.info("Community post scheduled (+60 min)")
            report["stages"]["community_post"] = {"ok": True}
        except Exception as e:
            log.warning(f"Community post schedule failed: {e}")
            report["stages"]["community_post"] = {"ok": False, "error": str(e)}

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
