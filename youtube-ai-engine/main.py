"""
main.py — Single entry point for the Self-Evolving YouTube AI Engine
=====================================================================

Usage:
  python main.py --all                   Run full daily pipeline now
  python main.py --test                  Run test suite
  python main.py --dry-run               Pick topic only, no media production
  python main.py --phase trend           Run trend hunter only
  python main.py --phase script          Run script writer only (needs --topic)
  python main.py --phase voice           Run voice engine only
  python main.py --phase visual          Run visual engine only
  python main.py --phase assemble        Run video editor only
  python main.py --phase thumbnail       Run thumbnail AI only
  python main.py --phase publish         Run publisher only
  python main.py --phase audit           Run weekly code audit now
  python main.py --phase feedback        Run analytics feedback loop now
  python main.py --phase competitor      Run competitor spy now
  python main.py --schedule              Start the continuous scheduler (daemon)
  python main.py --topic "Your topic"   Override topic selection
  python main.py --style tech-dark       Override style preset
  python main.py --status                Print channel performance summary
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

# ── Logging setup ──────────────────────────────────────────────────────────────

LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"engine_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8",
        ),
    ],
)

log = logging.getLogger("engine.main")


# ── Phase runners ──────────────────────────────────────────────────────────────

def run_test():
    """Run the automated test suite."""
    import subprocess
    result = subprocess.run(
        [sys.executable, str(HERE / "tests" / "auto_test.py")],
        cwd=str(HERE),
    )
    sys.exit(result.returncode)


def run_status(memory):
    """Print a performance summary."""
    from core.brain import get_brain
    print(get_brain().daily_brief())
    summary = memory.performance_summary()
    print(f"\nVideos in DB:   {summary['total_videos']}")
    print(f"Average CTR:    {summary['avg_ctr']:.2f}%")
    print(f"Avg retention:  {summary['avg_avd_pct']:.1f}%")
    print(f"Avg RPM:        ${summary['avg_rpm']:.2f}")


def run_trend_phase(memory):
    from intelligence.trend_hunter import TrendHunter
    th       = TrendHunter(memory)
    results  = th.fetch_all()
    memory.save_trends(results)
    print(f"\nTop 10 trends:")
    for i, t in enumerate(results[:10], 1):
        print(f"  {i:2d}. [{t['score']:.3f}] {t['title'][:70]}")
    return results


def run_script_phase(topic, style, length, memory):
    from production.script_writer import ScriptWriter
    sw     = ScriptWriter(memory)
    script = sw.generate(topic, style=style, target_length_s=length)
    print(f"\nScript: {script.get('title','')}")
    print(f"Scenes: {len(script.get('scenes',[]))}")
    print(f"Hook:   {script.get('hook','')[:100]}")
    return script


def run_voice_phase(script, style, output_dir):
    from production.voice_engine import VoiceEngine
    ve    = VoiceEngine()
    audio = ve.produce(script, style=style, output_dir=output_dir)
    if audio:
        print(f"\nAudio: {audio}")
    else:
        print("\nVoice engine: no audio produced")
    return audio


def run_visual_phase(script, style, output_dir):
    from production.visual_engine import VisualEngine
    ve     = VisualEngine()
    frames = ve.produce(script, style=style, output_dir=output_dir)
    print(f"\nVisuals: {sum(1 for f in frames if f)}/{len(frames)} scenes rendered")
    return frames


def run_assemble_phase(script, frames, audio, style, output_dir):
    from production.editor import VideoEditor
    ed    = VideoEditor()
    video = ed.assemble(script, frames, audio, style=style, output_dir=output_dir)
    if video:
        size_mb = Path(video).stat().st_size / 1024 / 1024
        print(f"\nVideo: {video}  ({size_mb:.1f} MB)")
    return video


def run_thumbnail_phase(topic, style, output_dir, memory):
    from production.thumbnail_ai import ThumbnailAI
    ta     = ThumbnailAI(memory)
    thumbs = ta.generate_variants(topic, style=style, output_dir=output_dir)
    print(f"\nThumbnails: {thumbs}")
    return thumbs


def run_publish_phase(video, metadata, thumbnail, memory):
    from publishing.youtube_publisher import YouTubePublisher
    pub   = YouTubePublisher(memory)
    yt_id = pub.upload(video, metadata, thumbnail=thumbnail)
    if yt_id:
        print(f"\nPublished: https://youtu.be/{yt_id}")
    return yt_id


def run_audit_phase(memory):
    from evolution.code_auditor import CodeAuditor
    result = CodeAuditor(memory).run_weekly_audit()
    print(f"\nAudit: {len(result.get('upgraded',[]))} files upgraded, "
          f"{result.get('skipped',0)} skipped")


def run_feedback_phase(memory):
    from evolution.feedback_loop import FeedbackLoop
    n = FeedbackLoop(memory).run_pending_checkpoints()
    print(f"\nFeedback loop: updated {n} video checkpoints")


def run_competitor_phase(memory):
    from intelligence.competitor_spy import CompetitorSpy
    formula = CompetitorSpy(memory).build_formula_db()
    print(f"\nCompetitor DB: {formula.get('channels_analysed',0)} channels analysed")


def run_algo_phase(memory, n_sessions: int = 50):
    from intelligence.algorithm_hacker import AlgorithmHacker
    ah       = AlgorithmHacker(memory)
    analysis = ah.run_daily(n_sessions=n_sessions)
    ah.print_report(analysis)
    cr = analysis.get("change_report", {})
    print(f"\nAlgorithm status: {cr.get('status','stable')} "
          f"(severity={cr.get('severity','low')})")
    print(f"Auto-adjustments: {len(analysis.get('auto_adjustments',[]))}")


def run_meta_phase(memory):
    from evolution.meta_learner import MetaLearner
    ml = MetaLearner(memory)
    # Force a cycle on manual invocation (don't wait for the milestone)
    result = ml.run_cycle()
    plan = result.get("plan", {})
    print(f"\nMeta-learning cycle (milestone {result.get('milestone')}):")
    print(f"  Diagnosis: {plan.get('diagnosis','')}")
    print(f"  Priority:  {plan.get('priority','')}")
    print(f"  Actions executed: {result['execution'].get('actions_done',0)}"
          f"/{len(plan.get('actions',[]))}")
    for r in result["execution"].get("results", []):
        print(f"    - [{r.get('action')}] {r.get('status')}"
              f"{(' — ' + r.get('why')) if r.get('why') else ''}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Self-Evolving YouTube AI Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--all",       action="store_true", help="Run full pipeline")
    parser.add_argument("--test",      action="store_true", help="Run test suite")
    parser.add_argument("--dry-run",   action="store_true", help="Topic selection only")
    parser.add_argument("--schedule",  action="store_true", help="Start continuous scheduler")
    parser.add_argument("--status",    action="store_true", help="Show performance summary")
    parser.add_argument("--phase",     type=str, metavar="PHASE",
                        choices=["trend","script","voice","visual","assemble",
                                 "thumbnail","publish","audit","feedback",
                                 "competitor","meta","algo"],
                        help="Run a specific phase only")
    parser.add_argument("--topic",     type=str, default="", help="Override topic")
    parser.add_argument("--style",     type=str, default="",
                        choices=["tech-dark","vlog-warm","news-clean","motivation-epic",""],
                        help="Override video style")
    parser.add_argument("--length",    type=int, default=0, help="Override video length (seconds)")
    args = parser.parse_args()

    if args.test:
        run_test()
        return

    # Initialise memory
    from core.memory import get_memory
    memory = get_memory()

    if args.status:
        run_status(memory)
        return

    if args.schedule:
        from core.scheduler import Scheduler
        Scheduler(memory, dry_run=args.dry_run).start()
        return

    if args.all or args.dry_run:
        from core.scheduler import DailyPipeline
        result = DailyPipeline(memory, dry_run=args.dry_run).run()
        log.info(f"Pipeline result: {list(result.keys())}")
        return

    # Single-phase execution
    if not args.phase:
        parser.print_help()
        sys.exit(0)

    # Analysis/maintenance phases don't need a topic — run and return early
    # (avoids wasteful trend-fetching network calls).
    ANALYSIS_PHASES = {"audit", "feedback", "competitor", "meta", "algo"}
    if args.phase in ANALYSIS_PHASES:
        if args.phase == "algo":
            run_algo_phase(memory)
        else:
            {"audit":      run_audit_phase,
             "feedback":   run_feedback_phase,
             "competitor": run_competitor_phase,
             "meta":       run_meta_phase}[args.phase](memory)
        return

    from core.brain import get_brain
    brain  = get_brain()

    # Resolve topic
    if args.topic:
        topic = {"title": args.topic, "source": "manual", "score": 1.0}
    else:
        candidates = run_trend_phase(memory) if args.phase != "trend" else []
        topic      = brain.decide_topic(candidates or [], memory.recent_topics(days=7))

    style  = args.style or brain.decide_style(topic.get("title", ""))
    length = args.length or brain.decide_video_length()

    slug    = topic.get("title", "video")[:30].lower().replace(" ", "_")
    out_dir = str(HERE / "output" / slug)

    if args.phase == "trend":
        run_trend_phase(memory)

    elif args.phase == "script":
        run_script_phase(topic, style, length, memory)

    elif args.phase == "voice":
        script = run_script_phase(topic, style, length, memory)
        run_voice_phase(script, style, out_dir)

    elif args.phase == "visual":
        script = run_script_phase(topic, style, length, memory)
        run_visual_phase(script, style, out_dir)

    elif args.phase == "assemble":
        script = run_script_phase(topic, style, length, memory)
        frames = run_visual_phase(script, style, out_dir)
        audio  = run_voice_phase(script, style, out_dir)
        run_assemble_phase(script, frames, audio, style, out_dir)

    elif args.phase == "thumbnail":
        run_thumbnail_phase(topic, style, out_dir, memory)

    elif args.phase == "publish":
        print("Publish phase requires a completed video. Run --all instead.")


if __name__ == "__main__":
    main()
