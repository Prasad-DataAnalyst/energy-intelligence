"""
production/shorts_factory.py — Viral Shorts Factory
=====================================================
Turns every long-form video into 5-10 platform-optimised Shorts automatically.

Pipeline per video:
  1. ViralMomentAnalyzer  — score every transcript segment 1-100
  2. ShortsFormatter      — build 9:16 spec, enforce 28-55s sweet spot,
                            hook-first reordering
  3. CaptionEngine        — TikTok-style word-by-word captions, SRT + ASS export
  4. TrendingAudioMatcher — match mood to trending audio category
  5. CrossPlatformExporter— ffmpeg commands + hashtag strategy per platform
  6. ShortsMonetizationTracker — separate revenue stream + expansion queue
  7. ShortsFactory        — orchestrator

Supported platforms: youtube_shorts | tiktok | instagram_reels | twitter_x | linkedin
"""
import json
import logging
import os
import re
import shutil
import subprocess
import textwrap
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("brain.shorts_factory")

HERE = Path(__file__).parent.parent

# ── Constants ─────────────────────────────────────────────────────────────────

DURATION_MIN    = 28    # algorithm kills videos shorter than this
DURATION_MAX    = 55    # completion rate drops sharply above this
DURATION_TARGET = 40    # sweet spot for Shorts completion rate

AVG_SPEAKING_WPM = 150  # words per minute for duration estimation

# ── Virality signal registry ──────────────────────────────────────────────────

VIRALITY_SIGNALS: Dict[str, Tuple[List[str], float]] = {
    "shocking_stat": (
        ["%", "billion", "million", "trillion", "times more", "times less",
         "shocking", "unbelievable", "you won't believe", "staggering",
         "record", "highest ever", "lowest ever", "x faster"],
        25.0,
    ),
    "surprising_reveal": (
        ["but here's the thing", "plot twist", "turns out", "nobody talks about",
         "secret", "hidden", "they don't want you to know", "the truth about",
         "actually", "in reality", "what really", "the real reason",
         "most people don't know"],
        22.0,
    ),
    "emotional_peak": (
        ["life-changing", "never forget", "terrifying", "devastating",
         "heartbreaking", "inspiring", "incredible", "amazing", "unreal",
         "emotional", "moving", "powerful", "changed everything"],
        20.0,
    ),
    "controversial": (
        ["wrong", "lie", "myth", "fake", "unpopular opinion", "hot take",
         "controversial", "banned", "censored", "they lied", "misinformation",
         "the problem with", "why everyone is wrong"],
        18.0,
    ),
    "hook_question": (
        ["did you know", "what if", "imagine if", "have you ever",
         "why does", "how is it possible", "can you believe",
         "what would happen"],
        15.0,
    ),
    "funny": (
        ["hilarious", "ironic", "absurd", "ridiculous", "awkward",
         "embarrassing", "awkward moment", "funny thing", "plot twist",
         "of course", "obviously not"],
        12.0,
    ),
}

# ── Platform export specifications ───────────────────────────────────────────

PLATFORM_SPECS: Dict[str, Dict] = {
    "youtube_shorts": {
        "width": 1080, "height": 1920, "fps": 60,
        "max_duration": 60,   "aspect": "9:16",
        "codec": "libx264",   "audio_codec": "aac",
        "crf": 22,            "preset": "fast",
        "color_grade": None,
        "cta_options": [
            "Subscribe for Part 2!",
            "Watch the FULL video on my channel!",
            "Follow for more energy facts!",
        ],
        "hashtag_count": 3,
        "hashtag_style": "topic_focused",
    },
    "tiktok": {
        "width": 1080, "height": 1920, "fps": 30,
        "max_duration": 60,   "aspect": "9:16",
        "codec": "libx264",   "audio_codec": "aac",
        "crf": 23,            "preset": "fast",
        "color_grade": None,
        "cta_options": [
            "Follow for Part 2!",
            "Duet this if you agree!",
            "Save this for later!",
        ],
        "hashtag_count": 8,
        "hashtag_style": "trending_first",
    },
    "instagram_reels": {
        "width": 1080, "height": 1920, "fps": 30,
        "max_duration": 90,   "aspect": "9:16",
        "codec": "libx264",   "audio_codec": "aac",
        "crf": 20,            "preset": "slow",
        "color_grade": "vivid",   # saturation +10%, contrast +5%
        "cta_options": [
            "Full video in bio link!",
            "Save this post!",
            "Tag someone who needs to see this!",
        ],
        "hashtag_count": 15,
        "hashtag_style": "niche_mix",
    },
    "twitter_x": {
        "width": 1280, "height": 720, "fps": 30,
        "max_duration": 140,  "aspect": "16:9",   # landscape for Twitter
        "codec": "libx264",   "audio_codec": "aac",
        "crf": 23,            "preset": "fast",
        "color_grade": None,
        "cta_options": [
            "Thread below 👇",
            "Full breakdown in replies",
            "RT if you agree",
        ],
        "hashtag_count": 2,
        "hashtag_style": "minimal",
    },
    "linkedin": {
        "width": 1080, "height": 1920, "fps": 30,
        "max_duration": 60,   "aspect": "9:16",
        "codec": "libx264",   "audio_codec": "aac",
        "crf": 20,            "preset": "slow",
        "color_grade": "professional",  # slight desaturation, sharp
        "cta_options": [
            "What's your take? Comment below.",
            "Agree or disagree? Let me know.",
            "Follow for more industry insights.",
        ],
        "hashtag_count": 5,
        "hashtag_style": "professional",
    },
}

# ── Trending audio catalogue (curated fallback when API unavailable) ──────────

AUDIO_CATALOGUE: Dict[str, List[Dict]] = {
    "shocking": [
        {"title": "Dramatic Impact", "artist": "Various", "bpm": 120, "mood": "tense"},
        {"title": "Bass Drop Reveal", "artist": "Various", "bpm": 140, "mood": "intense"},
    ],
    "inspiring": [
        {"title": "Rise Up", "artist": "Various", "bpm": 100, "mood": "uplifting"},
        {"title": "Momentum", "artist": "Various", "bpm": 110, "mood": "motivational"},
    ],
    "educational": [
        {"title": "Lo-Fi Study", "artist": "Various", "bpm": 80, "mood": "calm"},
        {"title": "Curious Mind", "artist": "Various", "bpm": 90, "mood": "curious"},
    ],
    "funny": [
        {"title": "Comedy Bounce", "artist": "Various", "bpm": 130, "mood": "playful"},
        {"title": "Quirky Walk", "artist": "Various", "bpm": 120, "mood": "humorous"},
    ],
    "controversial": [
        {"title": "Building Tension", "artist": "Various", "bpm": 105, "mood": "dramatic"},
        {"title": "Dark Undertone",   "artist": "Various", "bpm": 95,  "mood": "serious"},
    ],
}

SIGNAL_TO_MOOD: Dict[str, str] = {
    "shocking_stat":    "shocking",
    "surprising_reveal":"shocking",
    "emotional_peak":   "inspiring",
    "controversial":    "controversial",
    "hook_question":    "educational",
    "funny":            "funny",
}

# ── Hashtag packs ─────────────────────────────────────────────────────────────

HASHTAG_PACKS: Dict[str, List[str]] = {
    "trending_first":   ["#fyp", "#viral", "#trending", "#foryoupage", "#foryou",
                          "#shorts", "#reels"],
    "topic_focused":    ["#energy", "#solar", "#tech", "#science", "#facts"],
    "niche_mix":        ["#energytips", "#sustainableliving", "#cleanenergy",
                          "#greenenergy", "#solarpanel", "#renewableenergy",
                          "#climatechange", "#sustainability", "#ecofriendly",
                          "#solarpowered", "#solarenergy", "#greentech",
                          "#environment", "#future", "#innovation"],
    "minimal":          ["#energy", "#tech"],
    "professional":     ["#energy", "#sustainability", "#innovation",
                          "#climatetech", "#futurism"],
}


# ── 1. ViralMomentAnalyzer ────────────────────────────────────────────────────

class ViralMomentAnalyzer:
    """
    Scores every scene/segment in a script for virality potential (1-100).
    Uses keyword signal detection + position bonuses + Claude enhancement
    when ANTHROPIC_API_KEY is available.
    """

    def analyze_transcript(self, script: Dict,
                            min_dur: float = 8.0,
                            max_dur: float = 60.0) -> List[Dict]:
        """
        Return all script segments with virality_score, signal_type,
        estimated start/end times, and hook_text.
        """
        scenes  = script.get("scenes", [])
        timed   = self._estimate_scene_times(scenes)
        results = []

        for i, scene in enumerate(timed):
            text = " ".join(filter(None, [
                scene.get("title", ""),
                scene.get("narration", ""),
                scene.get("text_overlay", ""),
            ]))
            score, signal = self.score_segment(scene)
            dur = scene["end_s"] - scene["start_s"]

            if dur < min_dur or dur > max_dur:
                continue  # skip scenes too short or too long to be standalone

            # Position bonus: first 10% and last 10% of video get a boost
            n = len(timed)
            if i < max(1, n // 10) or i >= n - max(1, n // 10):
                score = min(100.0, score + 8.0)

            # Hook text = first sentence or first 12 words
            hook = self._extract_hook(text)

            results.append({
                "scene_index":   i,
                "title":         scene.get("title", f"Scene {i+1}"),
                "narration":     scene.get("narration", ""),
                "text_overlay":  scene.get("text_overlay", ""),
                "hook_text":     hook,
                "start_s":       round(scene["start_s"], 1),
                "end_s":         round(scene["end_s"], 1),
                "duration_s":    round(dur, 1),
                "virality_score": round(score, 1),
                "signal_type":   signal,
            })

        # AI enhancement for top candidates when Claude is available
        if os.getenv("ANTHROPIC_API_KEY") and results:
            results = self._ai_rescore_top(results, script.get("title", ""))

        return sorted(results, key=lambda x: x["virality_score"], reverse=True)

    def score_segment(self, scene: Dict) -> Tuple[float, str]:
        """
        Return (score 0-100, signal_type) for a single scene dict.
        Accumulates signal contributions; best signal type wins label.
        """
        text  = " ".join(filter(None, [
            scene.get("title", ""), scene.get("narration", ""),
            scene.get("text_overlay", ""),
        ])).lower()

        best_signal   = "generic"
        best_contrib  = 0.0
        total_score   = 10.0  # baseline

        for signal_type, (keywords, base) in VIRALITY_SIGNALS.items():
            hits = sum(1 for kw in keywords if kw.lower() in text)
            if hits:
                contrib = base * min(hits, 3)   # cap at 3 hits per signal type
                total_score += contrib
                if contrib > best_contrib:
                    best_contrib = contrib
                    best_signal  = signal_type

        # Bonus: number/stat presence
        if re.search(r'\b\d+(?:\.\d+)?[%xX]?\b', text):
            total_score += 8.0

        # Bonus: question mark (creates curiosity gap)
        if "?" in text:
            total_score += 5.0

        # Bonus: direct address ("you", "your")
        if re.search(r'\byou(r)?\b', text):
            total_score += 4.0

        return min(100.0, total_score), best_signal

    def extract_top_segments(self, segments: List[Dict], n: int = 5) -> List[Dict]:
        """
        Return top N segments, enforcing signal-type diversity (no two identical
        signal types in the top 5 unless unavoidable).
        """
        seen_signals: Counter = Counter()
        chosen: List[Dict]    = []

        for seg in segments:  # already sorted by virality_score desc
            sig  = seg.get("signal_type", "generic")
            # Allow max 2 segments per signal type
            if seen_signals[sig] < 2:
                chosen.append(seg)
                seen_signals[sig] += 1
            if len(chosen) >= n:
                break

        # If we didn't get n after diversity filter, fill from remainder
        if len(chosen) < n:
            remaining = [s for s in segments if s not in chosen]
            chosen += remaining[:n - len(chosen)]

        return chosen[:n]

    # ── Private helpers ───────────────────────────────────────────────────

    def _estimate_scene_times(self, scenes: List[Dict]) -> List[Dict]:
        """Assign estimated start/end timestamps based on word-count and WPM."""
        cursor = 0.0
        timed  = []
        for scene in scenes:
            narration = scene.get("narration", "")
            words     = len(narration.split()) if narration else 10
            dur       = max(12.0, (words / AVG_SPEAKING_WPM) * 60.0)
            s         = dict(scene)
            s["start_s"] = cursor
            s["end_s"]   = cursor + dur
            timed.append(s)
            cursor += dur
        return timed

    def _extract_hook(self, text: str) -> str:
        """Extract the most hook-worthy opening sentence (≤15 words)."""
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        for sent in sentences:
            words = sent.split()
            if 4 <= len(words) <= 20:
                return " ".join(words[:15])
        return " ".join(text.split()[:12])

    def _ai_rescore_top(self, segments: List[Dict], topic: str) -> List[Dict]:
        """Use Claude Haiku to re-score the top 8 candidates."""
        top8   = segments[:8]
        others = segments[8:]
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            snippets = "\n".join(
                f"{i+1}. [{s['signal_type']}] score={s['virality_score']} "
                f"hook={s['hook_text'][:80]!r}"
                for i, s in enumerate(top8)
            )
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Topic: {topic}\n"
                        f"Rate each clip's viral potential for YouTube Shorts (1-100). "
                        f"Reply with ONLY a JSON array of numbers in order:\n{snippets}"
                    ),
                }],
            )
            raw = msg.content[0].text.strip()
            scores = json.loads(re.search(r'\[.*?\]', raw, re.DOTALL).group())
            for i, new_score in enumerate(scores[:len(top8)]):
                if isinstance(new_score, (int, float)):
                    # Blend: 60% AI + 40% heuristic
                    top8[i]["virality_score"] = round(
                        0.6 * float(new_score) + 0.4 * top8[i]["virality_score"], 1
                    )
        except Exception as e:
            log.debug(f"AI rescore failed (non-fatal): {e}")
        return sorted(top8 + others,
                      key=lambda x: x["virality_score"], reverse=True)


# ── 2. ShortsFormatter ───────────────────────────────────────────────────────

class ShortsFormatter:
    """
    Builds the production spec for each Short:
    - Enforces 28-55s sweet spot (trim or pad with CTA)
    - Hook-first reordering (most shocking moment as frame 1)
    - Caption layout spec for CaptionEngine
    - CTA selection per platform
    """

    def format_segment(self, segment: Dict,
                        source_metadata: Dict = None,
                        short_index: int = 1) -> Dict:
        """Return a complete Short production spec dict."""
        source_meta  = source_metadata or {}
        topic        = source_meta.get("title", segment.get("title", "video"))
        duration_raw = segment["end_s"] - segment["start_s"]

        start_s, end_s = self._enforce_sweet_spot(
            segment["start_s"], segment["end_s"]
        )
        duration = end_s - start_s

        # Reorder: if the hook is mid-narration, note it in spec for editor
        hook_order = self._plan_hook_order(segment)

        short_title = self._build_title(segment, topic, short_index)

        return {
            "short_index":    short_index,
            "source_title":   topic,
            "title":          short_title,
            "hook_text":      segment.get("hook_text", ""),
            "hook_order":     hook_order,
            "start_s":        round(start_s, 1),
            "end_s":          round(end_s, 1),
            "duration_s":     round(duration, 1),
            "virality_score": segment.get("virality_score", 50),
            "signal_type":    segment.get("signal_type", "generic"),
            "narration":      segment.get("narration", ""),
            "caption_spec":   self._build_caption_spec(segment, duration),
            "needs_cta_pad":  duration_raw < DURATION_MIN,
        }

    def get_cta(self, signal_type: str, platform: str) -> str:
        cta_map = {
            "shocking_stat":    "Full breakdown in my channel →",
            "surprising_reveal":"Part 2 is WILD — watch now →",
            "emotional_peak":   "Full story on my channel →",
            "controversial":    "Fight me in the comments 👇",
            "hook_question":    "Answer revealed on my channel →",
            "funny":            "More like this — follow me!",
        }
        platform_ctas = PLATFORM_SPECS.get(platform, {}).get("cta_options", [])
        if platform_ctas:
            return platform_ctas[0]
        return cta_map.get(signal_type, "Full video in bio →")

    # ── Private helpers ───────────────────────────────────────────────────

    def _enforce_sweet_spot(self, start: float, end: float) -> Tuple[float, float]:
        dur = end - start
        if dur < DURATION_MIN:
            end = start + DURATION_MIN
        elif dur > DURATION_MAX:
            # Keep start; trim end to DURATION_MAX
            end = start + DURATION_MAX
        return start, end

    def _plan_hook_order(self, segment: Dict) -> str:
        """
        Determine how to reorder for hook-first.
        Returns: 'as_is' | 'reverse_first' | 'extract_stat_first'
        """
        sig = segment.get("signal_type", "")
        if sig in ("shocking_stat", "surprising_reveal"):
            return "extract_stat_first"  # editor should pull the stat line first
        if sig == "emotional_peak":
            return "reverse_first"       # play the peak moment then provide context
        return "as_is"

    def _build_caption_spec(self, segment: Dict, duration: float) -> Dict:
        text = segment.get("narration", segment.get("title", ""))
        return {
            "text":        text,
            "style":       "tiktok",          # large, centered, word-by-word
            "font_size_px": 72,               # ~40% width at 1080px
            "position":    "center_lower",    # bottom 40% of screen
            "highlight_keywords": True,
            "word_by_word": True,
            "words_per_caption": 3,
        }

    def _build_title(self, segment: Dict, topic: str, index: int) -> str:
        hooks = {
            "shocking_stat":    f"SHOCKING: {segment.get('hook_text', topic)[:50]}",
            "surprising_reveal":f"Nobody Talks About This… ({topic[:40]})",
            "emotional_peak":   f"This Changed Everything About {topic[:40]}",
            "controversial":    f"Unpopular Opinion: {segment.get('hook_text', topic)[:45]}",
            "hook_question":    segment.get("hook_text", topic)[:60],
            "funny":            f"😂 {segment.get('hook_text', topic)[:55]}",
        }
        sig   = segment.get("signal_type", "generic")
        title = hooks.get(sig, f"#{index} — {topic[:50]}")
        return title[:100]


# ── 3. CaptionEngine ─────────────────────────────────────────────────────────

class CaptionEngine:
    """
    Generates TikTok-style large captions.
    Exports to .srt (compatibility) and .ass (styled overlays with ffmpeg).
    """

    HIGHLIGHT_WORDS = {
        "shocking", "secret", "truth", "never", "always", "warning", "alert",
        "exposed", "revealed", "banned", "illegal", "free", "instantly",
        "guaranteed", "proven", "breakthrough", "exclusive",
    }

    def generate_word_by_word(self, text: str, duration: float,
                               words_per_caption: int = 3) -> List[Dict]:
        """
        Split text into timed caption groups.
        Returns list of {start_s, end_s, text, highlight}.
        """
        words    = text.split()
        if not words:
            return []
        groups   = [words[i:i+words_per_caption]
                    for i in range(0, len(words), words_per_caption)]
        time_per = duration / len(groups) if groups else 1.0
        captions = []
        for i, group in enumerate(groups):
            chunk = " ".join(group)
            captions.append({
                "start_s":   round(i * time_per, 2),
                "end_s":     round((i + 1) * time_per, 2),
                "text":      chunk,
                "highlight": any(w.lower().rstrip(".,!?") in self.HIGHLIGHT_WORDS
                                 for w in group),
            })
        return captions

    def to_srt(self, captions: List[Dict], output_path: str) -> str:
        """Write captions to .srt file and return path."""
        def fmt(s):
            h, r = divmod(int(s * 1000), 3_600_000)
            m, r = divmod(r, 60_000)
            sec, ms = divmod(r, 1000)
            return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

        lines = []
        for i, cap in enumerate(captions, 1):
            lines.append(f"{i}")
            lines.append(f"{fmt(cap['start_s'])} --> {fmt(cap['end_s'])}")
            lines.append(cap["text"])
            lines.append("")

        Path(output_path).write_text("\n".join(lines), encoding="utf-8")
        return output_path

    def to_ass(self, captions: List[Dict], output_path: str,
                style: str = "tiktok") -> str:
        """
        Write a styled .ass (Advanced SubStation Alpha) file for ffmpeg overlay.
        TikTok style: large bold centered text, word-by-word, white + black outline.
        """
        header = textwrap.dedent("""\
            [Script Info]
            ScriptType: v4.00+
            PlayResX: 1080
            PlayResY: 1920
            ScaledBorderAndShadow: yes

            [V4+ Styles]
            Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
            Style: TikTok,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,120,1
            Style: TikTokHL,Arial,72,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,120,1

            [Events]
            Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        """)

        def ass_time(s: float) -> str:
            h, r = divmod(s, 3600)
            m, r = divmod(r, 60)
            return f"{int(h)}:{int(m):02d}:{r:05.2f}"

        events = []
        for cap in captions:
            style_name = "TikTokHL" if cap.get("highlight") else "TikTok"
            text       = cap["text"].replace("\n", "\\N")
            events.append(
                f"Dialogue: 0,{ass_time(cap['start_s'])},{ass_time(cap['end_s'])},"
                f"{style_name},,0,0,0,,{text}"
            )

        Path(output_path).write_text(
            header + "\n".join(events) + "\n", encoding="utf-8"
        )
        return output_path


# ── 4. TrendingAudioMatcher ──────────────────────────────────────────────────

class TrendingAudioMatcher:
    """
    Suggests trending audio for each Short based on mood/signal type.
    Falls back to curated catalogue when no external API is reachable.
    """

    def suggest_audio(self, signal_type: str, topic: str,
                       n: int = 3) -> List[Dict]:
        """Return N audio suggestions for a segment."""
        mood     = SIGNAL_TO_MOOD.get(signal_type, "educational")
        tracks   = AUDIO_CATALOGUE.get(mood, AUDIO_CATALOGUE["educational"])

        # Attempt to fetch trending Shorts audio from YouTube Music shelf
        trending = self._fetch_trending_audio(mood)
        combined = trending + tracks
        return combined[:n]

    def match_to_segment(self, segment: Dict) -> Dict:
        """Return single best-match audio suggestion."""
        suggestions = self.suggest_audio(
            segment.get("signal_type", "generic"),
            segment.get("title", ""),
            n=1,
        )
        return suggestions[0] if suggestions else {"title": "Original Audio",
                                                    "artist": "Creator"}

    def _fetch_trending_audio(self, mood: str) -> List[Dict]:
        """
        Attempt InnerTube music shelf fetch; return [] on failure.
        Real implementation would query YouTube Music trending.
        """
        return []  # placeholder — real API requires auth tokens


# ── 5. CrossPlatformExporter ─────────────────────────────────────────────────

class CrossPlatformExporter:
    """
    Generates per-platform export specs and ffmpeg command strings.
    Executes ffmpeg when available; otherwise returns commands for manual use.
    """

    def export_specs(self, short_spec: Dict, topic: str) -> Dict[str, Dict]:
        """Return full export spec for every platform."""
        specs = {}
        for platform, platform_cfg in PLATFORM_SPECS.items():
            cta      = short_spec.get("cta", platform_cfg["cta_options"][0])
            hashtags = self.generate_hashtags(topic, platform,
                                               platform_cfg["hashtag_count"])
            specs[platform] = {
                "platform":     platform,
                "width":        platform_cfg["width"],
                "height":       platform_cfg["height"],
                "fps":          platform_cfg["fps"],
                "max_duration": platform_cfg["max_duration"],
                "aspect":       platform_cfg["aspect"],
                "cta":          cta,
                "hashtags":     hashtags,
                "color_grade":  platform_cfg.get("color_grade"),
                "codec":        platform_cfg["codec"],
                "crf":          platform_cfg["crf"],
            }
        return specs

    def generate_hashtags(self, topic: str, platform: str, n: int) -> List[str]:
        """Generate platform-optimised hashtag list."""
        style  = PLATFORM_SPECS.get(platform, {}).get("hashtag_style", "minimal")
        base   = HASHTAG_PACKS.get(style, [])[:n]

        # Derive topic-specific hashtags from title words
        topic_words = re.findall(r'\b[a-zA-Z]{4,}\b', topic.lower())
        topic_tags  = [f"#{w}" for w in topic_words[:3] if f"#{w}" not in base]

        combined = list(dict.fromkeys(base + topic_tags))[:n]
        return combined

    def build_ffmpeg_command(self, short_spec: Dict, platform: str,
                              input_path: str, output_path: str,
                              caption_ass_path: str = "") -> str:
        """
        Build complete ffmpeg command string for a platform.
        Handles 9:16 crop, color grades, caption overlay, duration trimming.
        """
        cfg      = PLATFORM_SPECS.get(platform, PLATFORM_SPECS["youtube_shorts"])
        start    = short_spec["start_s"]
        end      = short_spec["end_s"]
        w, h     = cfg["width"], cfg["height"]
        fps      = cfg["fps"]
        crf      = cfg["crf"]
        codec    = cfg["codec"]
        acodec   = cfg["audio_codec"]

        # Video filter chain
        if cfg["aspect"] == "9:16":
            # Center-crop 9:16 from source then scale to target resolution
            vf_parts = [
                f"crop=in_h*{w}/{h}:in_h:(in_w-in_h*{w}/{h})/2:0",
                f"scale={w}:{h}",
                f"fps={fps}",
            ]
        else:
            # 16:9 landscape (Twitter/X) — scale with padding
            vf_parts = [
                f"scale={w}:{h}:force_original_aspect_ratio=decrease",
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                f"fps={fps}",
            ]

        # Color grade
        grade = cfg.get("color_grade")
        if grade == "vivid":
            vf_parts.append("eq=saturation=1.1:contrast=1.05")
        elif grade == "professional":
            vf_parts.append("eq=saturation=0.9:contrast=1.08")

        # Caption overlay
        if caption_ass_path and Path(caption_ass_path).exists():
            vf_parts.append(f"ass={caption_ass_path}")

        vf = ",".join(vf_parts)

        cmd = (
            f'ffmpeg -y -i "{input_path}" '
            f'-ss {start:.1f} -to {end:.1f} '
            f'-vf "{vf}" '
            f'-c:v {codec} -preset {cfg["preset"]} -crf {crf} '
            f'-c:a {acodec} -ar 44100 -ac 2 '
            f'-movflags +faststart '
            f'"{output_path}"'
        )
        return cmd

    def execute_export(self, short_spec: Dict, platform: str,
                        input_path: str, output_dir: str,
                        caption_ass_path: str = "") -> Optional[str]:
        """
        Execute ffmpeg export.  Returns output path on success, None otherwise.
        If ffmpeg is not installed, logs the command for manual use.
        """
        if not shutil.which("ffmpeg"):
            log.debug("ffmpeg not found — skipping actual export")
            return None

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^\w]", "_", platform)
        out  = str(Path(output_dir) / f"short_{short_spec['short_index']}_{slug}_{ts}.mp4")
        cmd  = self.build_ffmpeg_command(short_spec, platform, input_path,
                                          out, caption_ass_path)
        log.debug(f"  ffmpeg: {cmd}")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                return out
            log.warning(f"  ffmpeg failed for {platform}: {result.stderr[-200:]}")
        except subprocess.TimeoutExpired:
            log.warning(f"  ffmpeg timeout for {platform}")
        except Exception as e:
            log.warning(f"  ffmpeg error for {platform}: {e}")
        return None


# ── 6. ShortsMonetizationTracker ─────────────────────────────────────────────

class ShortsMonetizationTracker:
    """
    Treats Shorts as a separate revenue stream.
    Tracks performance per short+platform, surfaces expansion candidates.

    Shorts RPM context (2025-2026):
      YouTube Shorts: ~$0.03–0.07 per 1000 views (Shorts Feed RPM)
      TikTok Creator Fund: ~$0.02–0.04 per 1000 views
      Instagram Reels Bonus: varies widely
    """

    EXPANSION_THRESHOLD  = 65   # virality_score to auto-queue for full video
    SHORTS_REVENUE_TIERS = {    # RPM per platform (USD per 1000 views)
        "youtube_shorts":   0.05,
        "tiktok":           0.03,
        "instagram_reels":  0.04,
        "twitter_x":        0.01,
        "linkedin":         0.02,
    }

    def estimate_revenue(self, platform: str, views: int) -> float:
        rpm = self.SHORTS_REVENUE_TIERS.get(platform, 0.03)
        return round(views * rpm / 1000, 4)

    def identify_expansion_candidates(self, memory,
                                       threshold: int = None) -> List[Dict]:
        """
        Return Shorts whose virality_score exceeds threshold — prime candidates
        to expand into full-length videos (same topic, more depth).
        """
        t = threshold or self.EXPANSION_THRESHOLD
        try:
            top_shorts = memory.get_top_shorts(min_score=t, n=20)
        except Exception:
            top_shorts = []

        candidates = []
        for short in top_shorts:
            candidates.append({
                "topic":         short.get("title", ""),
                "short_db_id":   short.get("id"),
                "virality_score":short.get("virality_score", 0),
                "views":         short.get("views", 0),
                "signal_type":   short.get("signal_type", ""),
                "reason": f"Shorts virality {short.get('virality_score',0):.0f}/100 "
                          f"({short.get('views',0):,} views) — audience demand confirmed",
            })
        return candidates

    def weekly_shorts_report(self, memory) -> Dict:
        """Aggregate weekly Shorts performance across all platforms."""
        try:
            shorts = memory.get_shorts_analytics(days=7)
        except Exception:
            shorts = []

        total_views   = sum(s.get("views", 0) for s in shorts)
        total_revenue = sum(
            self.estimate_revenue(s.get("platform", "youtube_shorts"),
                                  s.get("views", 0))
            for s in shorts
        )
        by_platform: Dict[str, Dict] = {}
        for s in shorts:
            p = s.get("platform", "unknown")
            if p not in by_platform:
                by_platform[p] = {"views": 0, "count": 0, "revenue": 0.0}
            by_platform[p]["views"]   += s.get("views", 0)
            by_platform[p]["count"]   += 1
            by_platform[p]["revenue"] += self.estimate_revenue(p, s.get("views", 0))

        return {
            "week_total_views":   total_views,
            "week_total_revenue": round(total_revenue, 2),
            "by_platform":        by_platform,
            "shorts_count":       len(shorts),
        }


# ── 7. ShortsFactory (orchestrator) ──────────────────────────────────────────

class ShortsFactory:
    """
    Turns every long-form video into 5-10 Shorts automatically.

    For each video:
      1. Analyze transcript → rank viral segments
      2. Format each segment (hook-first, 28-55s, captions)
      3. Match trending audio
      4. Export to 5 platforms
      5. Track in memory + surface expansion candidates
    """

    DEFAULT_N_SHORTS = 5

    def __init__(self, memory=None):
        self._memory   = memory
        self._analyzer = ViralMomentAnalyzer()
        self._formatter= ShortsFormatter()
        self._captions = CaptionEngine()
        self._audio    = TrendingAudioMatcher()
        self._exporter = CrossPlatformExporter()
        self._tracker  = ShortsMonetizationTracker()

    # ── Main entry points ──────────────────────────────────────────────────

    def process_video(self, script: Dict,
                       video_path: str = "",
                       metadata: Dict = None,
                       source_video_db_id: int = None,
                       n_shorts: int = DEFAULT_N_SHORTS) -> Dict:
        """
        Full pipeline: analyze → format → caption → export → persist.
        Returns result dict with all generated Short specs.
        """
        source_meta  = metadata or {}
        topic        = source_meta.get("title", script.get("title", "video"))
        out_dir      = self._resolve_output_dir(topic, video_path)

        log.info(f"  Shorts factory: analyzing {topic[:50]!r} for viral moments…")

        # Step 1 — viral moment analysis
        all_segments = self._analyzer.analyze_transcript(script)
        top_segments = self._analyzer.extract_top_segments(all_segments, n=n_shorts)
        log.info(f"  Found {len(all_segments)} segments, "
                 f"selected {len(top_segments)} for Shorts")

        shorts_produced: List[Dict] = []

        for idx, seg in enumerate(top_segments, 1):
            try:
                short = self._produce_one_short(
                    seg, idx, topic, source_meta,
                    video_path, out_dir, source_video_db_id
                )
                shorts_produced.append(short)
                log.info(f"  Short {idx}/{len(top_segments)}: "
                         f"[{seg['signal_type']}] score={seg['virality_score']:.0f} "
                         f"{seg['duration_s']:.0f}s — {short['spec']['title'][:50]}")
            except Exception as e:
                log.error(f"  Short {idx} failed (non-fatal): {e}", exc_info=True)

        # Expansion queue check
        expansion_candidates = self._tracker.identify_expansion_candidates(
            self._memory
        ) if self._memory else []

        result = {
            "topic":               topic,
            "total_segments":      len(all_segments),
            "shorts_produced":     len(shorts_produced),
            "shorts":              shorts_produced,
            "expansion_candidates":expansion_candidates,
            "output_dir":          str(out_dir),
        }
        return result

    def run_daily(self, video_data: Dict = None) -> Dict:
        """Called from DailyPipeline — pass in today's pipeline result dict."""
        if not video_data:
            return {"shorts_produced": 0, "reason": "no video data"}
        return self.process_video(
            script              = video_data.get("script", {}),
            video_path          = video_data.get("video", "") or "",
            metadata            = video_data.get("metadata", {}),
            source_video_db_id  = video_data.get("db_id"),
        )

    def get_expansion_queue(self, n: int = 10) -> List[Dict]:
        """Return Shorts that should be expanded to full videos."""
        return self._tracker.identify_expansion_candidates(self._memory, n)

    def print_report(self, result: Dict) -> None:
        log.info("\n" + "=" * 60)
        log.info("SHORTS FACTORY REPORT")
        log.info("=" * 60)
        log.info(f"  Topic:         {result.get('topic', '')}")
        log.info(f"  Segments found:{result.get('total_segments', 0)}")
        log.info(f"  Shorts created:{result.get('shorts_produced', 0)}")
        log.info(f"  Output dir:    {result.get('output_dir', '')}")

        for s in result.get("shorts", []):
            spec    = s.get("spec", {})
            exports = s.get("exports", {})
            score   = spec.get("virality_score", 0)
            dur     = spec.get("duration_s", 0)
            sig     = spec.get("signal_type", "")
            title   = spec.get("title", "")[:55]
            files   = sum(1 for v in exports.values() if v.get("file_path"))
            log.info(f"  [{spec.get('short_index',0)}] {sig:20s} "
                     f"score={score:.0f} dur={dur:.0f}s "
                     f"files={files} — {title}")

        if result.get("expansion_candidates"):
            log.info(f"\n  Expansion queue: "
                     f"{len(result['expansion_candidates'])} Shorts ready to become full videos")
            for c in result["expansion_candidates"][:3]:
                log.info(f"    → {c['topic'][:60]} (score={c['virality_score']:.0f})")

    # ── Per-short production ──────────────────────────────────────────────

    def _produce_one_short(self, segment: Dict, idx: int, topic: str,
                            source_meta: Dict, video_path: str,
                            out_dir: Path,
                            source_video_db_id: Optional[int]) -> Dict:
        """Full pipeline for a single Short across all platforms."""

        # Format spec
        spec = self._formatter.format_segment(segment, source_meta, idx)

        # Audio suggestion
        audio = self._audio.match_to_segment(segment)
        spec["suggested_audio"] = audio

        # Generate captions
        cap_dir     = out_dir / "captions"
        cap_dir.mkdir(parents=True, exist_ok=True)
        captions    = self._captions.generate_word_by_word(
            spec["narration"], spec["duration_s"]
        )
        srt_path    = str(cap_dir / f"short_{idx}.srt")
        ass_path    = str(cap_dir / f"short_{idx}.ass")
        spec["srt_path"] = self._captions.to_srt(captions, srt_path)
        spec["ass_path"] = self._captions.to_ass(captions, ass_path)

        # Export specs + optional ffmpeg execution per platform
        export_specs = self._exporter.export_specs(spec, topic)
        exports: Dict[str, Dict] = {}

        for platform, p_spec in export_specs.items():
            file_path = None
            if video_path and Path(video_path).exists():
                p_out_dir = str(out_dir / platform)
                file_path = self._exporter.execute_export(
                    spec, platform, video_path, p_out_dir, ass_path
                )
                # Log ffmpeg command even if not executed (for manual use)
                if not file_path:
                    p_out_dir_path = out_dir / platform
                    p_out_dir_path.mkdir(parents=True, exist_ok=True)
                    dummy_out = str(p_out_dir_path / f"short_{idx}_{platform}.mp4")
                    cmd = self._exporter.build_ffmpeg_command(
                        spec, platform, video_path, dummy_out, ass_path
                    )
                    p_spec["ffmpeg_command"] = cmd

            p_spec["file_path"] = file_path
            exports[platform]   = p_spec

        # Persist to memory
        db_id = self._persist_short(spec, source_video_db_id, source_meta)

        return {
            "spec":         spec,
            "captions":     captions[:5],   # first 5 for quick preview
            "exports":      exports,
            "audio":        audio,
            "db_id":        db_id,
        }

    def _persist_short(self, spec: Dict, source_video_db_id: Optional[int],
                        source_meta: Dict) -> Optional[int]:
        if not self._memory:
            return None
        try:
            return self._memory.save_short({
                "source_video_db_id": source_video_db_id,
                "source_youtube_id":  source_meta.get("youtube_id", ""),
                "short_index":        spec.get("short_index", 0),
                "segment_type":       spec.get("signal_type", ""),
                "virality_score":     spec.get("virality_score", 0),
                "start_s":            spec.get("start_s", 0),
                "end_s":              spec.get("end_s", 0),
                "duration_s":         spec.get("duration_s", 0),
                "title":              spec.get("title", ""),
                "hook_text":          spec.get("hook_text", ""),
                "platform":           "multi",
                "status":             "produced",
            })
        except Exception as e:
            log.debug(f"Memory save failed for short: {e}")
            return None

    def _resolve_output_dir(self, topic: str, video_path: str) -> Path:
        if video_path:
            return Path(video_path).parent / "shorts"
        slug = re.sub(r"[^\w]", "_", topic.lower())[:30]
        return HERE / "output" / slug / "shorts"
