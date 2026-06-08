"""
Phase 7 — Metadata & Output Package
=====================================
Uses Claude to generate SEO-optimised title, description, tags, chapters,
and hashtags.  Saves the complete output package.
"""
import json
import logging
import os
import re
import shutil
from typing import Dict, Any, List, Optional

import anthropic

from utils.models import TrendResult, Script, PipelineContext

log = logging.getLogger("viral_pipeline.metadata")


def generate_metadata(trend: TrendResult, script: Script,
                       cfg: dict) -> Dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _fallback_metadata(trend, script)

    model = cfg.get("script", {}).get("model", "claude-sonnet-4-6")
    platform = cfg.get("video", {}).get("platform", "youtube")

    # Build chapter timestamps from scene durations
    chapters = _build_chapters(script)
    chapters_text = "\n".join(
        f"{c['timestamp']} {c['title']}" for c in chapters
    )

    prompt = f"""You are a YouTube SEO expert.

Video topic: {trend.title}
Platform: {platform}
Target audience: {trend.target_audience}
Category: {trend.category}
Keywords: {', '.join(trend.keywords[:10])}
Scene count: {len(script.scenes)}
Duration: ~{script.total_duration:.0f}s

Generate complete SEO metadata. Return ONLY valid JSON:
{{
  "title": "optimised YouTube title (max 70 chars, includes power word)",
  "description": "full 300-500 word SEO description with keyword density, timestamps placeholder, and subscribe CTA",
  "tags": ["tag1", "tag2", ...30 tags max 500 chars total],
  "hashtags": ["#tag1", "#tag2", ...15 hashtags],
  "chapters": {json.dumps(chapters)},
  "category_id": "27",
  "default_language": "en",
  "seo_keywords": ["primary keyword 1", "primary keyword 2", "primary keyword 3"],
  "engagement_hooks": ["comment prompt 1", "comment prompt 2"]
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model, max_tokens=2048,
            system="Output valid JSON only.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        log.info(f"  Title     : {data.get('title', '')[:70]}")
        log.info(f"  Tags      : {len(data.get('tags', []))} tags")
        return data
    except Exception as e:
        log.error(f"Metadata generation failed: {e}")
        return _fallback_metadata(trend, script)


def _build_chapters(script: Script) -> List[Dict[str, str]]:
    chapters = []
    cursor   = 0.0
    for scene in script.scenes:
        m = int(cursor // 60)
        s = int(cursor % 60)
        label = (scene.text_overlay or scene.voiceover[:50]).strip()
        label = re.sub(r"[^\w\s\-\!\?]", "", label)[:60]
        chapters.append({
            "timestamp": f"{m:02d}:{s:02d}",
            "title": label or f"Part {scene.id}",
        })
        cursor += scene.duration
    return chapters


def _fallback_metadata(trend: TrendResult, script: Script) -> Dict[str, Any]:
    chapters = _build_chapters(script)
    tags = (trend.keywords + [trend.category, trend.topic,
            "youtube", "viral", "trending", "2025"])[:30]
    return {
        "title": trend.title[:70],
        "description": (
            f"{trend.title}\n\n"
            f"{chr(10).join('• ' + p for p in trend.talking_points)}\n\n"
            f"Subscribe for daily content! 🔔\n\n"
            + "\n".join(f"{c['timestamp']} {c['title']}" for c in chapters)
        ),
        "tags": tags,
        "hashtags": [f"#{k.replace(' ', '')}" for k in trend.keywords[:15]],
        "chapters": chapters,
        "category_id": "27",
        "default_language": "en",
        "seo_keywords": trend.keywords[:3],
        "engagement_hooks": [
            "What did you think? Comment below!",
            "Share this with someone who needs to see it.",
        ],
    }


def save_output_package(ctx: PipelineContext, metadata: Dict[str, Any],
                        cfg: dict) -> str:
    """
    Organise all outputs into /output/{topic_slug}/:
      final_video.mp4
      thumbnail_v1/v2/v3.png
      metadata.json
      script.txt
      storyboard.json
    """
    out = ctx.output_dir
    os.makedirs(out, exist_ok=True)

    # Write metadata.json
    meta_path = os.path.join(out, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    # Write a summary report
    report_path = os.path.join(out, "pipeline_report.txt")
    with open(report_path, "w") as f:
        f.write(f"VIRAL PIPELINE — OUTPUT REPORT\n")
        f.write(f"{'='*60}\n\n")
        f.write(f"Topic    : {ctx.trend.topic if ctx.trend else '—'}\n")
        f.write(f"Title    : {metadata.get('title', '—')}\n")
        f.write(f"Platform : {ctx.platform}\n")
        f.write(f"Duration : {ctx.script.total_duration:.0f}s\n" if ctx.script else "")
        f.write(f"\nFILES\n{'-'*40}\n")
        f.write(f"Video    : {ctx.final_video or '—'}\n")
        for i, t in enumerate(ctx.thumbnails, 1):
            f.write(f"Thumb v{i} : {t}\n")
        f.write(f"\nSEO TITLE\n{'-'*40}\n{metadata.get('title', '')}\n")
        f.write(f"\nDESCRIPTION\n{'-'*40}\n{metadata.get('description', '')}\n")
        f.write(f"\nTAGS\n{'-'*40}\n{', '.join(metadata.get('tags', []))}\n")

    log.info(f"\n{'='*55}")
    log.info(f"OUTPUT PACKAGE: {out}")
    log.info(f"  Video     : {ctx.final_video}")
    log.info(f"  Thumbnails: {len(ctx.thumbnails)}")
    log.info(f"  Metadata  : {meta_path}")
    log.info(f"{'='*55}")
    return out
