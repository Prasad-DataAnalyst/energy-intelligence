"""All Claude prompt templates — centralised so tone/style stays consistent."""
from string import Template

# ── System Persona ─────────────────────────────────────────────────────────
SYSTEM_PERSONA = """You are the head writer for Drift Wire326, a high-energy YouTube finance channel.
Audience: US Gen Z and Millennial investors, ages 18-35.
Tone: Punchy, credible, fast-paced. Like CNBC Fast Money but with TikTok energy.
Rules:
- No financial advice. Frame everything as news, education, or analysis.
- Always add the disclaimer when covering individual stocks.
- Never guarantee returns or predict specific prices.
- Use plain English. Avoid jargon unless you immediately explain it.
- Hook the viewer in the first 15 seconds.
- Use data. Cite sources.
- End every video with a call-to-action (subscribe, comment, next video).
"""

# ── Script Templates ───────────────────────────────────────────────────────
WEEKDAY_SCRIPT_PROMPT = Template("""
You are writing the complete script for today's Drift Wire326 market recap video.

TODAY'S MARKET DATA:
$market_data

EARNINGS NEWS:
$earnings_data

ECONOMIC NEWS:
$economic_data

TRENDING HEADLINES:
$headlines

Write a complete, punchy 8-minute script (approximately 1,200 words) following this EXACT structure:

[HOOK] (15 seconds) — Explosive opening stat or breaking headline. No pleasantries.
[OPEN] (20 seconds) — Channel intro, today's agenda (3 bullet points).
[SEGMENT 1 — Market Overview] (~2 min) — S&P, Nasdaq, Dow performance. Winners/losers. Volume. Key moves.
[SEGMENT 2 — Stock Spotlight] (~2.5 min) — 2-3 most significant individual stock stories with data.
[SEGMENT 3 — Macro Pulse] (~1.5 min) — Bond yields, VIX, Fed news, economic data if applicable.
[SEGMENT 4 — What to Watch] (~1 min) — Tomorrow's key events: earnings, data releases, Fed speeches.
[CTA & CLOSE] (30 seconds) — Subscribe reminder, comment prompt, teaser for next video.
[DISCLAIMER] — Read the standard disclaimer naturally.

Format each section with [SECTION NAME] headers. Include stage directions like [PAUSE], [B-ROLL: chart], [GRAPHIC: ticker], [ZOOM IN] where appropriate.
Use the data provided. Be specific with numbers. Be energetic.
""")

SUNDAY_SCRIPT_PROMPT = Template("""
You are writing the complete script for Drift Wire326's Sunday deep-dive educational video.

TOPIC: $topic
SUBTOPICS: $subtopics
KEY CONCEPTS: $key_concepts
CURRENT RELEVANCE: $current_relevance

Write a complete 10-minute educational script (~1,500 words) following this structure:

[HOOK] (20 seconds) — Why does this topic matter RIGHT NOW? Lead with a surprising stat.
[OPEN] (20 seconds) — Introduce the topic and what viewers will learn.
[BACKGROUND] (~2 min) — What is this? Where did it come from? Keep it simple.
[HOW IT WORKS] (~3 min) — Core mechanics. Use analogies. Real-world examples.
[CURRENT IMPACT] (~2 min) — How does this affect markets/investors TODAY?
[WHAT EXPERTS SAY] (~1 min) — Quote or paraphrase credible sources.
[ACTIONABLE TAKEAWAY] (~1 min) — What should viewers actually know/do with this info?
[CTA & CLOSE] (30 seconds) — Subscribe, suggest a related video, ask a question for comments.
[DISCLAIMER] — Read naturally.

Include [B-ROLL], [GRAPHIC], [CHART] cues. Make complex topics accessible.
""")

SHORTS_SCRIPT_PROMPT = Template("""
Write a punchy 55-second YouTube Shorts script for Drift Wire326.

TOPIC: $topic
KEY FACT: $key_fact

Structure (must fit 55 seconds / ~135 words):
[HOOK - 0-3s] — One explosive sentence that stops the scroll.
[CONTENT - 3-45s] — 3-5 rapid-fire facts or a quick explainer. Data-driven.
[CTA - 45-55s] — "Follow for daily market updates" or "Comment [word] if you knew this."

No fluff. Every word earns its place. Gen Z energy.
""")

# ── Title Generation ───────────────────────────────────────────────────────
TITLE_GENERATION_PROMPT = Template("""
Generate 10 YouTube title options for a finance video. Return ONLY a JSON array of strings.

Topic: $topic
Key stat/hook: $key_stat
Video type: $video_type (weekday_recap | sunday_educational | shorts)
Channel: Drift Wire326 — US finance, Gen Z/Millennial audience

Title rules:
- 50-70 characters for main videos, 40-60 for shorts
- Include numbers where possible
- Trigger curiosity or urgency (not clickbait — be factual)
- Use power words: Crashed, Surged, Revealed, Warning, Breaking
- Include the year 2026 where natural
- SEO keywords: stock market, investing, $specific_ticker if relevant

Return exactly: ["title1", "title2", ..., "title10"]
""")

DESCRIPTION_PROMPT = Template("""
Write a YouTube video description for Drift Wire326.

Title: $title
Script summary: $script_summary
Video type: $video_type

Format:
- First 150 chars: SEO-optimized hook (most critical — shown before "show more")
- Timestamps: [MM:SS] Section Name format for each major section
- 3-5 relevant links (placeholder as [LINK_NAME])
- Social links section
- Tags section (15-20 hashtags)
- Full disclaimer

Tone: Professional but energetic. Match the channel's punchy voice.
""")

# ── Thumbnail Copy ──────────────────────────────────────────────────────────
THUMBNAIL_TEXT_PROMPT = Template("""
Generate thumbnail text for a YouTube finance video.
Return ONLY valid JSON with these exact keys.

Video title: $title
Key stat: $key_stat
Sentiment: $sentiment (bullish | bearish | neutral | warning)

Return: {
  "headline": "2-4 word explosive headline in ALL CAPS",
  "subtext": "supporting stat or phrase, max 20 chars",
  "ticker": "optional stock ticker or index symbol",
  "emoji": "one relevant emoji"
}
""")

# ── Compliance Review ───────────────────────────────────────────────────────
COMPLIANCE_REVIEW_PROMPT = Template("""
Review this YouTube finance script for compliance issues.

SCRIPT:
$script

Check for:
1. Financial advice language (should/must buy, sell, invest)
2. Return guarantees or price targets stated as fact
3. Unsubstantiated claims
4. Missing disclaimer
5. Misleading statistics

Return valid JSON:
{
  "passed": true/false,
  "issues": ["issue1", "issue2"],
  "suggested_fixes": ["fix1", "fix2"],
  "risk_level": "low|medium|high",
  "disclaimer_present": true/false
}
""")
