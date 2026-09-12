"""
production/viewer_psychology.py — Viewer Psychology Engine
===========================================================
Engineers every script for maximum psychological engagement using:
  - Attention mechanics (open loops, Zeigarnik, pattern interrupts)
  - Emotional journey mapping (0s → end arc)
  - Comment bait engineering
  - Retention triggers every 60 seconds
  - 1-100 engagement score; scripts below 75 are rejected/upgraded

Usage:
    from production.viewer_psychology import ViewerPsychology
    vp = ViewerPsychology(memory)
    enhanced = vp.engineer(script)          # raises EngagementTooLow if score < 75
    score     = vp.score(script)            # 0-100 float
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("brain.viewer_psychology")

# ── Constants ──────────────────────────────────────────────────────────────────

MIN_SCORE = 75
PATTERN_INTERRUPT_INTERVAL_S = 47     # seconds between pattern interrupts
RETENTION_TRIGGER_INTERVAL_S = 60     # seconds between retention hooks
CURIOSITY_GAP_DENSITY = 0.70          # target proportion of scenes with gaps

# Emotional journey arc: (start_s, end_s, label, intensity 0-1)
EMOTIONAL_ARC = [
    (0,    10,  "shock",      0.9),
    (10,   30,  "relate",     0.7),
    (30,   60,  "hope",       0.6),
    (60,   180, "struggle",   0.5),
    (180,  420, "revelation", 0.8),
    (420,  None,"euphoria",   1.0),   # None = until end
]

# Zeigarnik open-loop templates — fill {topic} at render time
OPEN_LOOP_TEMPLATES = [
    "You won't believe what happens at the end of this…",
    "Before we finish, I need to show you something that changes everything.",
    "Stay until the end — the most important part is coming.",
    "I'm going to show you {topic}, but first you need to understand why most people get this wrong.",
    "There's a secret to {topic} that almost nobody talks about.",
    "The answer will surprise you — and I'll prove it with numbers.",
]

PATTERN_INTERRUPT_PHRASES = [
    "Wait — stop everything.",
    "Here's where it gets really interesting.",
    "Now here's the part nobody tells you.",
    "Let me show you something unexpected.",
    "This is the moment everything changes.",
    "Hold on — before we go further:",
]

CURIOSITY_GAP_PHRASES = [
    "The reason is counterintuitive — here's why:",
    "Most people assume {assumption} — they're wrong.",
    "What happens next is the opposite of what you'd expect.",
    "The data reveals something shocking:",
    "Scientists / experts didn't see this coming.",
]

COMMENT_BAIT_TEMPLATES = [
    {
        "type": "divisive_question",
        "text": "Do you think {option_a} or {option_b}? Comment below — I read every reply.",
    },
    {
        "type": "planted_misconception",
        "text": "Most people believe {wrong_fact} — is that actually true? Tell me what you think.",
    },
    {
        "type": "binary_vote",
        "text": "Type A if you believe {option_a}, or B if you believe {option_b}.",
    },
]

RETENTION_TRIGGER_TEMPLATES = [
    "Coming up in the next {seconds} seconds: {teaser}",
    "Stay with me — I'm about to show you {teaser}.",
    "Don't skip — the next part answers the question everyone's asking.",
    "Here's what most tutorials skip over: {teaser}.",
]


# ── Exception ──────────────────────────────────────────────────────────────────

class EngagementTooLow(Exception):
    """Raised when a script's engagement score is below MIN_SCORE and cannot be fixed."""
    def __init__(self, score: float):
        self.score = score
        super().__init__(f"Engagement score {score:.1f} < {MIN_SCORE} — script rejected")


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    hook_strength:        float = 0.0   # /20
    open_loops:           float = 0.0   # /15
    pattern_interrupts:   float = 0.0   # /10
    curiosity_gaps:       float = 0.0   # /10
    emotional_arc:        float = 0.0   # /15
    retention_triggers:   float = 0.0   # /10
    comment_bait:         float = 0.0   # /10
    pacing:               float = 0.0   # /10

    @property
    def total(self) -> float:
        return (
            self.hook_strength +
            self.open_loops +
            self.pattern_interrupts +
            self.curiosity_gaps +
            self.emotional_arc +
            self.retention_triggers +
            self.comment_bait +
            self.pacing
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            "hook_strength":      self.hook_strength,
            "open_loops":         self.open_loops,
            "pattern_interrupts": self.pattern_interrupts,
            "curiosity_gaps":     self.curiosity_gaps,
            "emotional_arc":      self.emotional_arc,
            "retention_triggers": self.retention_triggers,
            "comment_bait":       self.comment_bait,
            "pacing":             self.pacing,
            "total":              self.total,
        }


# ── Main class ─────────────────────────────────────────────────────────────────

class ViewerPsychology:
    """
    Analyses and engineers scripts for maximum viewer retention and engagement.
    """

    def __init__(self, memory=None):
        self.memory = memory

    # ────────────────────────── Public API ──────────────────────────────────

    def score(self, script: Dict) -> float:
        """Return 0-100 engagement score without modifying the script."""
        return self._score_breakdown(script).total

    def engineer(self, script: Dict, max_attempts: int = 2) -> Dict:
        """
        Analyse and enhance a script for maximum psychological engagement.
        Returns the enhanced script dict.
        Raises EngagementTooLow if score remains below MIN_SCORE after max_attempts.
        """
        for attempt in range(1, max_attempts + 1):
            breakdown = self._score_breakdown(script)
            log.info(f"[ViewerPsychology] Score attempt {attempt}: {breakdown.total:.1f}/100")
            if breakdown.total >= MIN_SCORE:
                script["psychology_score"] = round(breakdown.total, 1)
                script["psychology_breakdown"] = breakdown.to_dict()
                return script
            log.info(f"  Score {breakdown.total:.1f} < {MIN_SCORE} — upgrading (attempt {attempt})")
            script = self._upgrade(script, breakdown)

        final = self._score_breakdown(script).total
        if final >= MIN_SCORE:
            script["psychology_score"] = round(final, 1)
            script["psychology_breakdown"] = self._score_breakdown(script).to_dict()
            return script

        raise EngagementTooLow(final)

    def build_emotional_arc(self, scenes: List[Dict]) -> List[Dict]:
        """
        Assign emotional arc labels to scenes based on their cumulative time position.
        Mutates scenes in-place (adds 'emotion' key) and returns them.
        """
        cumulative = 0
        for scene in scenes:
            dur = scene.get("duration_s", 30)
            label = self._arc_label(cumulative)
            scene["emotion"] = label
            cumulative += dur
        return scenes

    def generate_comment_bait(self, topic: str, script: Dict) -> str:
        """Return a comment-bait CTA string tailored to the topic."""
        try:
            return self._ai_comment_bait(topic, script)
        except Exception:
            return self._fallback_comment_bait(topic)

    # ───────────────────────── Scoring engine ───────────────────────────────

    def _score_breakdown(self, script: Dict) -> ScoreBreakdown:
        bd = ScoreBreakdown()
        scenes: List[Dict] = script.get("scenes", [])
        hook:   str        = script.get("hook", "")
        full_text          = self._full_text(script)
        total_s            = self._estimate_duration(script)

        bd.hook_strength      = self._score_hook(hook)
        bd.open_loops         = self._score_open_loops(full_text, scenes)
        bd.pattern_interrupts = self._score_pattern_interrupts(scenes, total_s)
        bd.curiosity_gaps     = self._score_curiosity_gaps(scenes)
        bd.emotional_arc      = self._score_emotional_arc(scenes, total_s)
        bd.retention_triggers = self._score_retention_triggers(scenes, total_s)
        bd.comment_bait       = self._score_comment_bait(full_text, script)
        bd.pacing             = self._score_pacing(scenes, total_s)
        return bd

    def _score_hook(self, hook: str) -> float:
        """Max 20 points."""
        if not hook:
            return 0.0
        pts  = 0.0
        text = hook.lower()
        if len(hook) >= 40:
            pts += 5.0
        if any(w in text for w in ("you", "your", "you'll", "you're")):
            pts += 4.0
        if any(w in text for w in ("secret", "nobody", "never", "shocking", "truth", "mistake")):
            pts += 4.0
        if "?" in hook:
            pts += 3.0
        if any(w in text for w in ("wait", "stop", "before", "first", "actually")):
            pts += 2.0
        if re.search(r"\d", hook):
            pts += 2.0
        return min(pts, 20.0)

    def _score_open_loops(self, full_text: str, scenes: List[Dict]) -> float:
        """Max 15 points. Rewards unresolved teasers early in the script."""
        if not full_text:
            return 0.0
        pts = 0.0
        text_lower = full_text.lower()
        teasers = [
            "you'll find out", "coming up", "stay until", "don't go anywhere",
            "before we finish", "at the end", "i'll show you", "the secret is",
            "later in this", "you won't believe",
        ]
        count = sum(1 for t in teasers if t in text_lower)
        pts += min(count * 3.0, 9.0)

        # Extra points for open loop in first scene (Zeigarnik setup)
        if scenes:
            first_text = (scenes[0].get("narration", "") + " " + scenes[0].get("text", "")).lower()
            if any(t in first_text for t in teasers):
                pts += 6.0
        return min(pts, 15.0)

    def _score_pattern_interrupts(self, scenes: List[Dict], total_s: float) -> float:
        """Max 10 points. One interrupt every 47s = full score."""
        if total_s <= 0 or not scenes:
            return 0.0
        expected = max(1, int(total_s / PATTERN_INTERRUPT_INTERVAL_S))
        interrupt_phrases = [p.lower() for p in PATTERN_INTERRUPT_PHRASES]
        found = 0
        for scene in scenes:
            text = (scene.get("narration", "") + " " + scene.get("text", "")).lower()
            if any(p in text for p in interrupt_phrases):
                found += 1
        ratio = min(found / expected, 1.0)
        return round(ratio * 10.0, 2)

    def _score_curiosity_gaps(self, scenes: List[Dict]) -> float:
        """Max 10 points. Target: 70% of scenes contain a curiosity gap."""
        if not scenes:
            return 0.0
        gap_markers = [
            "why", "how", "what", "secret", "truth", "wrong", "mistake",
            "nobody", "actually", "surprising", "unexpected",
        ]
        gap_count = 0
        for scene in scenes:
            text = (scene.get("narration", "") + " " + scene.get("text", "")).lower()
            if any(m in text for m in gap_markers):
                gap_count += 1
        density = gap_count / len(scenes)
        score   = min(density / CURIOSITY_GAP_DENSITY, 1.0) * 10.0
        return round(score, 2)

    def _score_emotional_arc(self, scenes: List[Dict], total_s: float) -> float:
        """Max 15 points. Checks that the script hits the expected emotional beats."""
        if not scenes or total_s <= 0:
            return 0.0

        # Tag each scene with arc position
        arc_hits = {label: False for _, _, label, _ in EMOTIONAL_ARC}
        cumulative = 0
        for scene in scenes:
            dur   = scene.get("duration_s", 30)
            label = self._arc_label(cumulative)
            arc_hits[label] = True
            cumulative += dur

        # Required beats: shock, revelation, euphoria (the three most critical)
        critical = {"shock", "revelation", "euphoria"}
        hits = sum(1 for k in critical if arc_hits.get(k))
        pts  = (hits / len(critical)) * 10.0

        # Bonus: every covered arc beat
        covered = sum(1 for v in arc_hits.values() if v)
        pts += (covered / len(arc_hits)) * 5.0
        return min(round(pts, 2), 15.0)

    def _score_retention_triggers(self, scenes: List[Dict], total_s: float) -> float:
        """Max 10 points. One trigger every 60s = full score."""
        if total_s <= 0 or not scenes:
            return 0.0
        expected = max(1, int(total_s / RETENTION_TRIGGER_INTERVAL_S))
        trigger_phrases = [
            "coming up", "stay with me", "don't skip", "next part", "in a moment",
            "before this ends", "here's what", "you'll want to see",
        ]
        found = 0
        for scene in scenes:
            text = (scene.get("narration", "") + " " + scene.get("text", "")).lower()
            if any(p in text for p in trigger_phrases):
                found += 1
        ratio = min(found / expected, 1.0)
        return round(ratio * 10.0, 2)

    def _score_comment_bait(self, full_text: str, script: Dict) -> float:
        """Max 10 points."""
        if not full_text:
            return 0.0
        pts  = 0.0
        text = full_text.lower()

        # Explicit comment invitation
        if any(w in text for w in ("comment below", "let me know", "tell me", "what do you think")):
            pts += 4.0
        # Question at end
        if script.get("outro", ""):
            if "?" in script["outro"]:
                pts += 3.0
        # Binary choice / poll
        if re.search(r"\btype [ab]\b", text) or " or " in text:
            pts += 3.0
        return min(pts, 10.0)

    def _score_pacing(self, scenes: List[Dict], total_s: float) -> float:
        """Max 10 points. Rewards scene variety and appropriate pacing rhythm."""
        if not scenes:
            return 0.0
        pts = 0.0
        durations = [s.get("duration_s", 30) for s in scenes]
        avg_dur = sum(durations) / len(durations)

        # Ideal scene duration: 15-45s
        if 15 <= avg_dur <= 45:
            pts += 5.0
        elif 10 <= avg_dur <= 60:
            pts += 3.0

        # Reward variety in scene durations (stddev > 5s)
        if len(durations) > 1:
            mean  = avg_dur
            stddev = (sum((d - mean) ** 2 for d in durations) / len(durations)) ** 0.5
            if stddev > 5:
                pts += 3.0
            elif stddev > 2:
                pts += 1.5

        # At least 5 scenes for good segmentation
        if len(scenes) >= 5:
            pts += 2.0
        elif len(scenes) >= 3:
            pts += 1.0

        return min(pts, 10.0)

    # ────────────────────────── Upgrade engine ──────────────────────────────

    def _upgrade(self, script: Dict, breakdown: ScoreBreakdown) -> Dict:
        """
        Fix the lowest-scoring dimension first, then try AI upgrade via Claude.
        Returns the upgraded script dict.
        """
        try:
            return self._ai_upgrade(script, breakdown)
        except Exception as e:
            log.debug(f"AI upgrade unavailable ({e}) — applying heuristic upgrades")
            return self._heuristic_upgrade(script, breakdown)

    def _heuristic_upgrade(self, script: Dict, breakdown: ScoreBreakdown) -> Dict:
        """Apply rule-based fixes to the weakest dimensions."""
        topic = script.get("title", "this topic")
        scenes: List[Dict] = script.get("scenes", [])

        # Fix hook if weak
        if breakdown.hook_strength < 12 and not script.get("hook"):
            script["hook"] = (
                f"The secret to {topic} that nobody tells you — "
                "stay until the end, the last 30 seconds changes everything."
            )

        # Inject open loop into first scene
        if breakdown.open_loops < 8 and scenes:
            first = scenes[0]
            narr  = first.get("narration", "")
            if not any(t in narr.lower() for t in ("stay until", "by the end", "you'll find out")):
                first["narration"] = (
                    narr.rstrip() +
                    f" By the end of this video you'll know the one thing that makes all the difference."
                )

        # Inject pattern interrupts
        if breakdown.pattern_interrupts < 5 and len(scenes) >= 3:
            mid = len(scenes) // 2
            scenes[mid]["narration"] = (
                "Wait — stop everything. " + scenes[mid].get("narration", "")
            )

        # Inject retention trigger every 60s
        if breakdown.retention_triggers < 5:
            cumulative = 0
            for i, scene in enumerate(scenes):
                if cumulative > 0 and cumulative % RETENTION_TRIGGER_INTERVAL_S < scene.get("duration_s", 30):
                    narr = scene.get("narration", "")
                    if not any(p in narr.lower() for p in ("coming up", "stay with", "don't skip")):
                        scene["narration"] = "Stay with me — the next part is where it gets interesting. " + narr
                        break
                cumulative += scene.get("duration_s", 30)

        # Add comment bait to outro if missing
        if breakdown.comment_bait < 6:
            script["outro"] = (
                script.get("outro", "").rstrip() +
                f" Now I want to hear from you: do you think {topic} is overhyped, or is it the real deal? "
                "Type A for overhyped, B for real deal — comment below."
            )

        # Assign emotional arc to scenes
        script["scenes"] = self.build_emotional_arc(scenes)
        return script

    def _ai_upgrade(self, script: Dict, breakdown: ScoreBreakdown) -> Dict:
        """Use Claude to rewrite weak sections."""
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        import anthropic

        dims = breakdown.to_dict()
        weak = sorted(
            [(k, v) for k, v in dims.items() if k != "total"],
            key=lambda x: x[1],
        )[:3]

        prompt = f"""You are an expert YouTube scriptwriter specialising in viewer psychology and retention.

Current script title: {script.get('title', 'unknown')}
Current hook: {script.get('hook', '(none)')}
Scenes count: {len(script.get('scenes', []))}

Engagement score breakdown (lower = needs improvement):
{json.dumps({k: round(v, 1) for k, v in dims.items()}, indent=2)}

The three weakest areas are: {[k for k, _ in weak]}

Rewrite ONLY the hook, the first scene narration, and the outro to fix these weak areas.
Apply these psychology principles:
- Hook: open a Zeigarnik loop, use "you / your", add a specific number or claim
- First scene: set up the emotional arc (shock/relate), add a pattern interrupt phrase
- Outro: add divisive comment bait as a binary A/B question, invite replies

Return ONLY a JSON object with keys: "hook", "first_scene_narration", "outro"
No other text."""

        client = anthropic.Anthropic(api_key=api_key)
        msg    = client.messages.create(
            model  = "claude-haiku-4-5-20251001",
            max_tokens = 600,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()

        # Extract JSON even if wrapped in markdown
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("Claude returned no JSON")

        data = json.loads(m.group())

        if data.get("hook"):
            script["hook"] = data["hook"]
        if data.get("outro"):
            script["outro"] = data["outro"]
        if data.get("first_scene_narration") and script.get("scenes"):
            script["scenes"][0]["narration"] = data["first_scene_narration"]

        script["scenes"] = self.build_emotional_arc(script.get("scenes", []))
        return script

    # ───────────────────── Comment bait generation ──────────────────────────

    def _ai_comment_bait(self, topic: str, script: Dict) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg    = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 120,
            messages   = [{
                "role": "user",
                "content": (
                    f"Write one short comment-bait CTA for a YouTube video about '{topic}'. "
                    "It should be a binary A/B question that sparks debate. "
                    "One sentence max. No hashtags."
                ),
            }],
        )
        return msg.content[0].text.strip()

    def _fallback_comment_bait(self, topic: str) -> str:
        return (
            f"Do you think {topic} is the future, or just another overhyped trend? "
            "Type A for future, B for overhyped — I read every comment."
        )

    # ─────────────────────── Helpers ────────────────────────────────────────

    def _full_text(self, script: Dict) -> str:
        parts = [
            script.get("hook", ""),
            script.get("intro", ""),
            script.get("outro", ""),
        ]
        for scene in script.get("scenes", []):
            parts.append(scene.get("narration", ""))
            parts.append(scene.get("text", ""))
        return " ".join(p for p in parts if p)

    def _estimate_duration(self, script: Dict) -> float:
        """Return total video duration in seconds from scene data."""
        total = sum(s.get("duration_s", 30) for s in script.get("scenes", []))
        return total if total > 0 else script.get("duration_s", 300)

    def _arc_label(self, elapsed_s: float) -> str:
        for start, end, label, _ in EMOTIONAL_ARC:
            if end is None:
                return label
            if start <= elapsed_s < end:
                return label
        return "euphoria"
