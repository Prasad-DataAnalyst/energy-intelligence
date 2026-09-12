"""
main.py — Single entry point for the Self-Evolving YouTube AI Engine
=====================================================================

Usage:
  python main.py --all                          Run full daily pipeline now
  python main.py --test                         Run test suite
  python main.py --dry-run                      Pick topic only, no media production
  python main.py --schedule                     Start the continuous scheduler (daemon)
  python main.py --status                       Print channel performance summary

  python main.py --mode full                    Run everything: ingest + feeds + archive +
                                                 competitor + score + select + produce
  python main.py --mode ingest /path/file.pdf   Ingest a single document and extract ideas
  python main.py --mode brief                   Print the Master Brain morning intelligence report
  python main.py --mode sprint                  Run monetization fast-track sprint check
  python main.py --mode archive --topic AI      Mine archives for a specific topic

  python main.py --phase trend                  Run trend hunter only
  python main.py --phase script                 Run script writer (needs --topic)
  python main.py --phase voice                  Run voice engine only
  python main.py --phase visual                 Run visual engine only
  python main.py --phase assemble               Run video editor only
  python main.py --phase thumbnail              Run thumbnail AI only
  python main.py --phase publish                Run publisher only
  python main.py --phase audit                  Run weekly code audit now
  python main.py --phase feedback               Run analytics feedback loop now
  python main.py --phase competitor             Run competitor spy now
  python main.py --phase ingest                 Scan /input folders for new documents
  python main.py --phase feeds                  Fetch all external news/research feeds
  python main.py --phase archive                Mine archive sources for content angles
  python main.py --phase category               Run category planning for today
  python main.py --phase synthesize             Run content synthesis engine
  python main.py --phase sprint                 Run monetization fast-track sprint
  python main.py --phase studio                 Run YouTube Studio daily maintenance
  python main.py --mode studio                  Run YouTube Studio daily maintenance
  python main.py --phase update                 Run self-modification update pipeline
  python main.py --mode update                  Run self-modification update pipeline
  python main.py --topic "Your topic"           Override topic selection
  python main.py --style tech-dark              Override style preset
  python main.py --length 600                   Override video length (seconds)
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


def run_monetize_phase(memory):
    from monetization.monetization_engine import MonetizationEngine
    import yaml
    cfg_path = Path(__file__).parent / "config" / "master_config.yaml"
    niche    = "technology"
    try:
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        niche = cfg.get("channel", {}).get("niche", "technology")
    except Exception:
        pass
    me = MonetizationEngine(memory, niche=niche)
    print("\n── Sponsor Discovery ───────────────────────────────")
    sponsors = me.discover_sponsors()
    print(f"Found {len(sponsors)} sponsor prospects")
    print("\n── Revenue Dashboard ───────────────────────────────")
    me.run_revenue_dashboard()


def run_shorts_phase(memory):
    from production.shorts_factory import ShortsFactory
    import yaml
    # Build a minimal script from recent trends/topics if no video available
    recent = memory.recent_topics(days=1)
    topic  = recent[0] if recent else "Energy Technology"
    script = {"title": topic, "hook": f"The truth about {topic}",
              "scenes": [
                  {"title": "Hook",    "narration": f"Did you know {topic} is changing?"},
                  {"title": "Content", "narration": f"Here are the key facts about {topic}."},
                  {"title": "CTA",     "narration": "Subscribe for more!"},
              ]}
    sf     = ShortsFactory(memory)
    result = sf.process_video(script, metadata={"title": topic})
    sf.print_report(result)
    print(f"\nShorts factory: {result.get('shorts_produced', 0)} Shorts produced "
          f"| {result.get('total_segments', 0)} segments analyzed")


def run_global_phase(memory):
    from intelligence.global_expansion import GlobalExpansion
    ge     = GlobalExpansion(memory)
    result = ge.run_daily()
    ge.print_report(result)
    print(f"\nGlobal expansion: {result.get('early_trend_count', 0)} early-mover trends "
          f"| markets sampled: {len(result.get('market_trends', {}))}")


def run_community_phase(memory):
    from intelligence.community_engine import CommunityEngine
    ce     = CommunityEngine(memory)
    result = ce.run_full_cycle()
    ce.print_report(result)
    print(f"\nCommunity engine: "
          f"replies={result.get('replies_sent', 0)} | "
          f"posts={result.get('posts_published', 0)} | "
          f"retention={result.get('retention_status', 'ok')}")


def run_growth_phase(memory):
    import yaml
    from intelligence.growth_hacker import GrowthHacker
    cfg_path = Path(__file__).parent / "config" / "master_config.yaml"
    niche    = "energy"
    try:
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        niche = cfg.get("channel", {}).get("niche", "energy")
    except Exception:
        pass
    gh     = GrowthHacker(memory)
    result = gh.run_full_cycle(niche=niche)
    gh.print_report(result)
    print(f"\nGrowth hacker: "
          f"{result.get('collab',{}).get('candidates_found',0)} collab candidates | "
          f"{result.get('search',{}).get('blue_ocean_keywords',0)} blue ocean keywords | "
          f"{result.get('playlists',{}).get('created',0)} playlists | "
          f"trajectory={result.get('growth_trajectory',{}).get('status','?')}")


def run_future_phase(memory):
    from intelligence.future_proof import FutureProof
    fp     = FutureProof(memory)
    result = fp.run_full_cycle(dry_run=False)
    fp.print_report(result)
    print(f"\nFuture proof: "
          f"{result.get('platform',{}).get('new_changes',0)} platform changes | "
          f"best_text={result.get('models',{}).get('best_text','?')} | "
          f"resolution={result.get('quality',{}).get('current_resolution','?')}")


def run_ingest_phase(memory, file_path: str = ""):
    from data.ingest_documents import DocumentIngestionEngine
    engine = DocumentIngestionEngine(memory)
    if file_path:
        result = engine.ingest_single_file(file_path)
        print(f"\nIngested: {file_path}")
        print(f"Insights extracted: {result.get('insight_count', 0)}")
        ideas = result.get("ideas", [])
        for i, idea in enumerate(ideas[:5], 1):
            print(f"  {i}. {idea.get('title', '')}")
    else:
        result = engine.run_full_scan()
        print(f"\nDocument scan complete:")
        print(f"  New files processed:  {result.get('new_files', 0)}")
        print(f"  Total insights saved: {result.get('total_insights', 0)}")
        ideas = engine.get_top_content_ideas(n=5)
        if ideas:
            print(f"\nTop content ideas:")
            for i, idea in enumerate(ideas, 1):
                print(f"  {i}. [{idea.get('video_potential', 0):.0f}] {idea.get('text', '')[:80]}")
    return result


def run_feeds_phase(memory, categories: list = None):
    from data.external_feeds import ExternalFeedEngine
    engine = ExternalFeedEngine(memory)
    result = engine.run_full_fetch()
    print(f"\nExternal feeds complete:")
    print(f"  Sources fetched:  {len(result.get('sources', []))}")
    print(f"  Items fetched:    {result.get('total_fetched', 0)}")
    print(f"  Unique items:     {result.get('unique_items', 0)}")
    top = result.get("top_items", [])
    if top:
        print(f"\nTop items:")
        for i, item in enumerate(top[:5], 1):
            score = item.get('video_score', item.get('score', 0))
            print(f"  {i}. [{score:.0f}] {item.get('title','')[:80]}")
    return result


def run_archive_phase(memory, topic: str = ""):
    from content.archive_miner import ArchiveMinerEngine
    engine = ArchiveMinerEngine(memory)
    if topic:
        result = engine.mine_for_topic(topic)
        print(f"\nArchive mining for '{topic}':")
        print(f"  Items fetched:  {result.get('total_fetched', 0)}")
        print(f"  Angles built:   {result.get('total_angles', 0)}")
        for i, angle in enumerate(result.get("top_angles", [])[:5], 1):
            score = angle.get("video_score", 0)
            title = angle.get("title", "")
            print(f"  {i}. [{score:.0f}] {title}")
    else:
        result = engine.run_daily_mining()
        print(f"\nArchive mining complete:")
        print(f"  Topics mined:      {result.get('topics_mined', 0)}")
        print(f"  Angles saved:      {result.get('total_angles_saved', 0)}")
        for s in result.get("summary", [])[:5]:
            print(f"  {s['topic']}: {s['angles']} angles — {s['top_title'][:60]}")
    return result


def run_category_phase(memory):
    from content.category_universe import CategoryUniverse
    engine = CategoryUniverse(memory)
    plan   = engine.run_daily_planning()
    suggestion = plan.get("suggestion", {})
    print(f"\nCategory plan for today:")
    print(f"  Primary:   {suggestion.get('category', '')} "
          f"(RPM ~${suggestion.get('predicted_rpm', 0):.0f})")
    print(f"  Reason:    {suggestion.get('reason', '')[:60]}")
    intersections = plan.get("top_intersections", [])
    if intersections:
        print(f"\nCross-category intersections ({len(intersections)}):")
        for i, inter in enumerate(intersections[:3], 1):
            print(f"  {i}. {inter.get('label', '')[:80]}")
    return plan


def run_synthesize_phase(memory):
    from content.synthesizer import ContentSynthesizer
    synth  = ContentSynthesizer(memory)
    result = synth.run_daily_synthesis()
    print(f"\nSynthesis complete:")
    print(f"  Sources used:   {result.get('sources_used', 0)}")
    print(f"  Ideas generated:{result.get('ideas_generated', 0)}")
    print(f"  Ideas saved:    {result.get('ideas_saved', 0)}")
    top = result.get("top_ideas", [])
    if top:
        print(f"\nTop synthesized ideas:")
        for i, idea in enumerate(top[:5], 1):
            print(f"  {i}. [{idea.get('score', 0):.0f}] {idea.get('title', '')[:80]}")
    return result


def run_sprint_phase(memory):
    from monetization.fast_track import FastTrackEngine
    engine  = FastTrackEngine(memory)
    result  = engine.run_daily_sprint()
    summary = engine.get_sprint_summary()
    phase   = result.get("phase", 0)
    gap     = result.get("ypp_gap", {})
    tl      = result.get("timeline", {})
    rev     = result.get("revenue_projection", {})

    print(f"\n{'='*60}")
    print(f"  MONETIZATION FAST TRACK — SPRINT STATUS")
    print(f"{'='*60}")
    print(f"  Phase {phase}: {result.get('phase_name', '')}")
    print(f"  Watch hours: {gap.get('watch_hours_pct', 0):.1f}% of 4,000h")
    print(f"  Subscribers: {gap.get('subscribers_pct', 0):.1f}% of 1,000")
    print(f"  YPP ready:   {'YES' if gap.get('ypp_ready') else 'NO'}")
    if not gap.get("ypp_ready"):
        print(f"  Est. days to YPP: {tl.get('days_to_ypp', '?')} "
              f"({tl.get('date_estimate', '?')})")
        print(f"  Bottleneck: {tl.get('bottleneck', '?')}")
    print(f"\n  Revenue projection:")
    print(f"    AdSense:      ${rev.get('adsense', 0):,.2f}/month")
    print(f"    Affiliate:    ${rev.get('affiliate', 0):,.2f}/month")
    print(f"    Sponsorships: ${rev.get('sponsorships', 0):,.2f}/month")
    print(f"    TOTAL:        ${rev.get('total_monthly', 0):,.2f}/month")
    print(f"    Annual est.:  ${rev.get('annual_est', 0):,.0f}/year")
    print(f"\n  TODAY: {result.get('today_action', '')}")

    milestones = result.get("sprint_plan", {}).get("milestones", [])
    if milestones:
        print(f"\n  Next milestones:")
        for m in milestones[:3]:
            print(f"    {m['label']:30s}  in {m['days_est']:4d} days  ({m['date_est']})")


def run_studio_phase(memory):
    from publishing.youtube_studio import YouTubeStudio
    studio = YouTubeStudio(memory)
    result = studio.run_daily_maintenance()
    studio.print_dashboard(result)


def run_update_phase(memory):
    from framework.update_pipeline import UpdatePipeline
    from framework.error_corrector import ErrorCorrector
    corrector = ErrorCorrector()
    pipeline  = UpdatePipeline(stop_on_first_failure=True)

    errors  = memory.get_error_log(unresolved_only=True, n=5)
    patches = []
    for err in errors:
        patch = corrector.generate_fix(
            file_path=err.get("file_path", ""),
            error_text=err.get("error_message", ""),
            traceback_text=err.get("traceback_text", ""),
        )
        if patch:
            patches.append(patch)

    if not patches:
        print("\n  No pending errors to fix.")
        return

    run = pipeline.run(patches)
    print(f"\n{'='*60}")
    print(f"  UPDATE PIPELINE RESULTS")
    print(f"{'='*60}")
    print(f"  Patches attempted: {run.total}")
    print(f"  Patches applied:   {run.succeeded}")
    print(f"  Patches failed:    {run.failed}")
    print(f"  Patches skipped:   {run.skipped}")
    print(f"  Duration:          {(run.finished_at - run.started_at).total_seconds():.1f}s")
    for r in run.results:
        status = "OK" if r.success else "FAIL"
        print(f"  [{status}] {r.patch.file_path}: {r.message[:60]}")

    active = result.get("active_streams", [])
    if active:
        print(f"\n  Active revenue streams: {', '.join(s['stream_id'] for s in active)}")

    upcoming = result.get("upcoming_streams", [])
    if upcoming:
        print(f"  Upcoming in 6 months:")
        for s in upcoming:
            print(f"    {s['stream_id']:20s}  in {s['months_away']} months — {s['notes'][:50]}")
    return result


def run_brief_phase(memory):
    """Master Brain morning intelligence brief."""
    print(f"\n{'='*60}")
    print(f"  MASTER BRAIN — MORNING INTELLIGENCE BRIEF")
    print(f"  {datetime.now().strftime('%A, %B %d %Y at %H:%M')}")
    print(f"{'='*60}")

    # 1. Channel status
    try:
        from core.brain import get_brain
        print(f"\n[CHANNEL STATUS]")
        print(get_brain().daily_brief())
    except Exception as e:
        print(f"  Channel status unavailable: {e}")

    # 2. Top external feed items
    try:
        items = memory.get_external_items(n=5)
        if items:
            print(f"\n[TOP NEWS ({len(items)} items)]")
            for item in items[:5]:
                print(f"  [{item.get('score',0):.0f}] {item.get('title','')[:75]}")
        else:
            print(f"\n[TOP NEWS] No items yet — run: python main.py --mode feeds")
    except Exception:
        pass

    # 3. Top content ideas
    try:
        ideas = memory.get_content_ideas(status="pending", n=5)
        if ideas:
            print(f"\n[TOP CONTENT IDEAS]")
            for idea in ideas[:5]:
                print(f"  [{idea.get('score',0):.0f}] {idea.get('title','')[:75]}")
        else:
            print(f"\n[CONTENT IDEAS] None yet — run: python main.py --mode full")
    except Exception:
        pass

    # 4. Archive angles
    try:
        archive = memory.get_archive_items(n=3)
        if archive:
            print(f"\n[ARCHIVE ANGLES]")
            for a in archive[:3]:
                print(f"  [{a.get('video_score',0):.0f}] {a.get('title','')[:75]}")
    except Exception:
        pass

    # 5. Sprint summary
    try:
        from monetization.fast_track import FastTrackEngine
        summary = FastTrackEngine(memory).get_sprint_summary()
        print(f"\n[MONETIZATION SPRINT]")
        print(f"  {summary}")
    except Exception as e:
        print(f"\n[SPRINT] Unavailable: {e}")

    # 6. Category for today
    try:
        from content.category_universe import CategoryUniverse
        plan       = CategoryUniverse(memory).run_daily_planning()
        suggestion = plan.get("suggestion", {})
        print(f"\n[TODAY'S FOCUS]")
        print(f"  Category: {suggestion.get('category','?')} "
              f"(RPM ~${suggestion.get('predicted_rpm',0):.0f})")
        print(f"  Reason:   {suggestion.get('reason','')[:60]}")
    except Exception as e:
        print(f"\n[TODAY'S FOCUS] Unavailable: {e}")

    print(f"\n{'='*60}")


def run_full_mode(memory):
    """--mode full: ingest → feeds → archive → competitor → synthesize → plan."""
    print(f"\n{'='*60}")
    print(f"  FULL MODE — UNIVERSAL CONTENT ENGINE")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    print("[1/6] Scanning input folders for new documents…")
    try:
        run_ingest_phase(memory)
    except Exception as e:
        print(f"  Ingest failed: {e}")

    print("\n[2/6] Pulling external feeds (news, research, data)…")
    try:
        run_feeds_phase(memory)
    except Exception as e:
        print(f"  Feeds failed: {e}")

    print("\n[3/6] Mining archives for historical parallels…")
    try:
        run_archive_phase(memory)
    except Exception as e:
        print(f"  Archive failed: {e}")

    print("\n[4/6] Running competitor intelligence scan…")
    try:
        run_competitor_phase(memory)
    except Exception as e:
        print(f"  Competitor failed: {e}")

    print("\n[5/6] Synthesizing content ideas across sources…")
    try:
        run_synthesize_phase(memory)
    except Exception as e:
        print(f"  Synthesis failed: {e}")

    print("\n[6/6] Planning today's content schedule…")
    try:
        run_category_phase(memory)
    except Exception as e:
        print(f"  Category planning failed: {e}")

    print("\n✓ Full mode complete. Run --mode brief for your intelligence summary.")
    print("  Best idea: python main.py --phase script --topic \"<title from above>\"")


def run_dominator_phase(memory):
    from intelligence.competitor_dominator import CompetitorDominator
    cd     = CompetitorDominator(memory)
    result = cd.run_daily()
    cd.print_report(result)
    print(f"\nCompetitor Dominator: {len(result.get('gap_list',[]))} gaps found, "
          f"{len(result.get('title_battles',[]))} active battles")


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
    parser.add_argument("--mode",      type=str, metavar="MODE",
                        choices=["full","ingest","brief","sprint","archive","feeds",
                                 "category","synthesize"],
                        help="High-level operation mode")
    parser.add_argument("--phase",     type=str, metavar="PHASE",
                        choices=["trend","script","voice","visual","assemble",
                                 "thumbnail","publish","audit","feedback",
                                 "competitor","meta","algo","monetize","dominate",
                                 "global","shorts","community","growth","future",
                                 "ingest","feeds","archive","category","synthesize",
                                 "sprint"],
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

    # ── --mode handling ────────────────────────────────────────────────────────
    if args.mode:
        if args.mode == "full":
            run_full_mode(memory)
        elif args.mode == "ingest":
            # Extra positional argument: file path
            file_path = args.topic  # re-use --topic as file path when mode=ingest
            run_ingest_phase(memory, file_path=file_path)
        elif args.mode == "brief":
            run_brief_phase(memory)
        elif args.mode == "sprint":
            run_sprint_phase(memory)
        elif args.mode == "archive":
            run_archive_phase(memory, topic=args.topic)
        elif args.mode == "feeds":
            run_feeds_phase(memory)
        elif args.mode == "category":
            run_category_phase(memory)
        elif args.mode == "synthesize":
            run_synthesize_phase(memory)
        elif args.mode == "studio":
            run_studio_phase(memory)
        elif args.mode == "update":
            run_update_phase(memory)
        return

    # Single-phase execution
    if not args.phase:
        parser.print_help()
        sys.exit(0)

    # Analysis/maintenance phases don't need a topic — run and return early
    # (avoids wasteful trend-fetching network calls).
    ANALYSIS_PHASES = {"audit", "feedback", "competitor", "meta", "algo",
                        "monetize", "dominate", "global", "shorts", "community",
                        "growth", "future", "ingest", "feeds", "archive",
                        "category", "synthesize", "sprint", "studio", "update"}
    if args.phase in ANALYSIS_PHASES:
        if args.phase == "algo":
            run_algo_phase(memory)
        elif args.phase == "monetize":
            run_monetize_phase(memory)
        elif args.phase == "dominate":
            run_dominator_phase(memory)
        elif args.phase == "global":
            run_global_phase(memory)
        elif args.phase == "shorts":
            run_shorts_phase(memory)
        elif args.phase == "community":
            run_community_phase(memory)
        elif args.phase == "growth":
            run_growth_phase(memory)
        elif args.phase == "future":
            run_future_phase(memory)
        elif args.phase == "ingest":
            run_ingest_phase(memory, file_path=args.topic)
        elif args.phase == "feeds":
            run_feeds_phase(memory)
        elif args.phase == "archive":
            run_archive_phase(memory, topic=args.topic)
        elif args.phase == "category":
            run_category_phase(memory)
        elif args.phase == "synthesize":
            run_synthesize_phase(memory)
        elif args.phase == "sprint":
            run_sprint_phase(memory)
        elif args.phase == "studio":
            run_studio_phase(memory)
        elif args.phase == "update":
            run_update_phase(memory)
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
