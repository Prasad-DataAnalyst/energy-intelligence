"""
config/prompts.py — DriftWire326 Claude API Prompt Library
All 16 prompt constants for script generation, titles, descriptions, and compliance.
Use Python str.format(**kwargs) at call time — never mutate these at import.

Backward-compatible Template aliases are at the bottom of this file so existing
generators can continue to use .substitute() until they are individually upgraded.
"""
from string import Template

# ─────────────────────────────────────────────────────────────────────────────
# SHARED SYSTEM PERSONA  (pass as system= in every Claude call)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PERSONA = (
    "You are the head writer for DriftWire326, a high-energy YouTube finance channel "
    "targeting US Gen Z and Millennial investors aged 18-35. "
    "Tone: punchy, credible, fast-paced — like CNBC Fast Money with TikTok energy. "
    "Rules: never give financial advice; frame everything as news, education, or analysis; "
    "always include the disclaimer when covering individual stocks; never guarantee returns "
    "or predict specific prices; use plain English and explain jargon immediately; "
    "hook the viewer in the first 10 seconds; cite data and sources; end every video with a CTA."
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. TOPIC_SELECTOR_PROMPT
# Variables: {topics_json}, {date}, {day_type}
# Output: JSON array — top 3 topics ranked by audience appeal
# ─────────────────────────────────────────────────────────────────────────────

TOPIC_SELECTOR_PROMPT = """\
You are selecting the best finance story for a YouTube video on {date} ({day_type}).

AVAILABLE TOPICS (JSON):
{topics_json}

Rank the top 3 topics by their combined score across:
- Viral potential (will Gen Z / Millennial investors care?)
- News recency (happened in the last 24 hours scores highest)
- Emotional hook (fear, greed, surprise, outrage)
- Data richness (can we cite specific numbers?)
- Story arc (clear cause → effect → so-what)

Return ONLY valid JSON — no markdown, no explanation:
[
  {{
    "rank": 1,
    "topic": "<topic name>",
    "reason": "<one sentence why this leads>",
    "anchor_number": "<the single most compelling stat>",
    "tier": "tier1 | tier2 | tier3"
  }},
  {{
    "rank": 2,
    "topic": "<topic name>",
    "reason": "<one sentence>",
    "anchor_number": "<stat>",
    "tier": "tier1 | tier2 | tier3"
  }},
  {{
    "rank": 3,
    "topic": "<topic name>",
    "reason": "<one sentence>",
    "anchor_number": "<stat>",
    "tier": "tier1 | tier2 | tier3"
  }}
]"""


# ─────────────────────────────────────────────────────────────────────────────
# 2. WEEKDAY_SCRIPT_TIER1_PROMPT  — breakout move (≥5 % move)
# Variables: {topic}, {anchor_number}, {context}, {style}, {hook}
# Output: 280-420 word script, urgent breaking-news tone
# ─────────────────────────────────────────────────────────────────────────────

WEEKDAY_SCRIPT_TIER1_PROMPT = """\
Write a TIER-1 BREAKOUT script for DriftWire326. Style: {style}.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
CONTEXT / DATA: {context}
OPENING HOOK: {hook}

This is a major market move (≥5%). Match the energy of live breaking-news coverage.
Total word count: 280-420 words. Every second counts.

Use this EXACT structure with section headers:

[HOOK] — 5 seconds
One explosive sentence using the anchor number. Start mid-action.
Use the provided hook: "{hook}"

[WHAT HAPPENED] — 40 seconds
The who-what-when of the move. Cite two or more specific data points.
No padding. Short declarative sentences.

[WHY IT MATTERS] — 50 seconds
Cause → ripple effects. Who wins? Who loses? What does this signal?
Ground every claim with data or a named source.

[NUMBERS DEEP DIVE] — 40 seconds
Go granular: percentages, volume, comparisons, historical context.
Use at least three distinct numbers.

[WHAT HAPPENS NEXT] — 25 seconds
Forward-looking but NOT predictive. "Traders are watching…" / "Key level to monitor…"
No price targets, no guarantees.

[CTA] — 15 seconds
"If this surprised you, drop a comment below. Subscribe so you never miss a move like this."

Rules:
- Include at least one phrase from: "according to", "data shows", "reported", "as of today",
  "markets indicate", "analysts note", "figures show", "sources indicate", "market data suggests"
- Do NOT use: "you should buy", "guaranteed return", "this will go up", "best investment",
  "I recommend buying", "go all in", "this is a sure thing"
- End with: "This content is for informational and entertainment purposes only and does not
  constitute financial advice. Always consult a licensed financial advisor before making any
  investment decisions. Narration is AI-generated."

Return plain text only — no markdown, no commentary outside the script."""


# ─────────────────────────────────────────────────────────────────────────────
# 3. WEEKDAY_SCRIPT_TIER2_PROMPT  — notable move (2 %–4.99 %)
# Variables: {topic}, {anchor_number}, {context}, {style}, {hook}
# Output: 280-420 words, confident market-anchor tone
# ─────────────────────────────────────────────────────────────────────────────

WEEKDAY_SCRIPT_TIER2_PROMPT = """\
Write a TIER-2 NOTABLE MOVE script for DriftWire326. Style: {style}.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
CONTEXT / DATA: {context}
OPENING HOOK: {hook}

This is a meaningful but not explosive market event (2-4.99% move).
Tone: confident, informed market anchor — not frantic, not boring.
Total word count: 280-420 words.

Structure (use section headers exactly):

[HOOK] — 5 seconds
Crisp, data-driven opener. Intrigue over alarm.
Use: "{hook}"

[WHAT HAPPENED] — 40 seconds
Clean narrative: what moved, by how much, when, and triggered by what catalyst.
Cite at least two data points.

[WHY IT MATTERS] — 50 seconds
Connect this move to the bigger picture: sector trends, macro forces, investor sentiment.
Source every claim.

[NUMBERS DEEP DIVE] — 40 seconds
Three or more specific figures: performance vs. peer, vs. index, vs. 52-week range.
Historical comparison adds credibility.

[WHAT HAPPENS NEXT] — 25 seconds
Upcoming catalysts to monitor (earnings date, Fed meeting, data release).
Frame as "watch for" — never as a price prediction.

[CTA] — 15 seconds
Invite engagement: a question for the comments, a subscribe reminder.

Rules:
- Include at least one phrase from: "according to", "data shows", "reported", "as of today",
  "markets indicate", "analysts note", "figures show", "sources indicate", "market data suggests"
- Do NOT use: "you should buy", "guaranteed return", "this will go up", "best investment",
  "I recommend buying", "go all in", "this is a sure thing"
- Close with the full disclaimer and AI disclosure.

Return plain text only."""


# ─────────────────────────────────────────────────────────────────────────────
# 4. WEEKDAY_SCRIPT_TIER3_PROMPT  — routine / educational day
# Variables: {topic}, {anchor_number}, {context}, {style}, {hook}
# Output: 280-420 words, warm educational reporter tone
# ─────────────────────────────────────────────────────────────────────────────

WEEKDAY_SCRIPT_TIER3_PROMPT = """\
Write a TIER-3 ROUTINE / EDUCATIONAL script for DriftWire326. Style: {style}.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
CONTEXT / DATA: {context}
OPENING HOOK: {hook}

Markets were quiet today — lean into education. Help viewers understand the underlying
concept or trend. Tone: warm, knowledgeable friend who happens to follow the market closely.
Total word count: 280-420 words.

Structure:

[HOOK] — 5 seconds
Open with a relatable question or a "did you know" angle. Use: "{hook}"

[WHAT HAPPENED] — 40 seconds
Describe today's action matter-of-factly. Ground the narrative in the anchor number.

[WHY IT MATTERS] — 50 seconds
Pivot to the educational angle. Why should a new investor care about this metric,
sector, or concept on a quiet day?

[NUMBERS DEEP DIVE] — 40 seconds
Contextualise the numbers. Year-to-date performance, sector vs. index, historical average.
Make the data approachable.

[WHAT HAPPENS NEXT] — 25 seconds
Upcoming data points or events that could change the picture.

[CTA] — 15 seconds
Ask a learning question in the comments ("What concept do you want us to break down next?").

Rules:
- Include at least one phrase from: "according to", "data shows", "reported", "as of today",
  "markets indicate", "analysts note", "figures show", "sources indicate", "market data suggests"
- Do NOT use advisory language (buy, sell, invest recommendations).
- Close with the full disclaimer and AI disclosure.

Return plain text only."""


# ─────────────────────────────────────────────────────────────────────────────
# 5. SHORTS_SCRIPT_PROMPT
# Variables: {topic}, {anchor_number}, {hook_text}, {key_stat}, {ticker}, {sentiment}
# Output: 5 text-card script, <55 seconds total
# ─────────────────────────────────────────────────────────────────────────────

SHORTS_SCRIPT_PROMPT = """\
Write a YouTube Shorts script for DriftWire326 that fits in 55 seconds max.

TOPIC: {topic}
TICKER / ASSET: {ticker}
SENTIMENT: {sentiment}
ANCHOR NUMBER: {anchor_number}
KEY STAT: {key_stat}
HOOK LINE: {hook_text}

Format as 5 TEXT CARDS — each card = one screen of bold on-screen text read aloud.
Cards must flow naturally when read back-to-back at conversational pace.

CARD 1 — HOOK (0-5 s)
One sentence. Start with the most shocking number or outcome. No "hey guys".
Use: "{hook_text}"

CARD 2 — CONTEXT (5-18 s)
What triggered this? Two to three short sentences. Cite the source or data.

CARD 3 — IMPACT (18-33 s)
Who wins, who loses, what ripples out. Keep it vivid and specific.

CARD 4 — THE NUMBER (33-45 s)
One killer stat displayed large. Benchmark it so viewers grasp its size.

CARD 5 — CTA (45-55 s)
"Follow @DriftWire326 for daily market moves." or a comment prompt ending with "👇"

Rules:
- Total narration word count: 110-145 words
- No financial advice language whatsoever
- Each card: max 25 words on screen
- End with: "Not financial advice. AI narration."

Return ONLY the 5 card texts labelled CARD 1 through CARD 5. No extra commentary."""


# ─────────────────────────────────────────────────────────────────────────────
# 6. SUNDAY_INVESTMENT_SCRIPT_PROMPT  — Week 1: investment banking
# Variables: {topic}, {week_context}, {audience_level}
# Output: 280-350 words, engaging teacher tone
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_INVESTMENT_SCRIPT_PROMPT = """\
Write a Sunday educational script for DriftWire326 on INVESTMENT BANKING & MARKETS.

TOPIC: {topic}
WEEK CONTEXT: {week_context}
AUDIENCE LEVEL: {audience_level}

Tone: engaging teacher who genuinely loves finance and wants everyone to understand it.
No condescension. Analogies welcome. Word count: 280-350 words.

Structure:

[HOOK] — 10 seconds
Lead with a surprising fact or counter-intuitive truth about investment banking.

[WHAT IS IT] — 60 seconds
Define the concept clearly. One strong real-world analogy.

[HOW IT WORKS] — 90 seconds
Walk through the mechanics step by step. Use 2024-2026 examples where possible.
Cite real data or credible sources.

[WHY IT MATTERS TO YOU] — 50 seconds
Connect it to everyday investing: how does this affect a 25-year-old's portfolio?

[KEY TAKEAWAY] — 20 seconds
One memorable sentence the viewer can repeat to a friend.

[CTA] — 15 seconds
Subscribe prompt + question for comments.

Rules:
- Include credibility anchors: "according to", "data shows", "as of today", etc.
- No advisory language.
- Close with full disclaimer and AI disclosure.

Return plain text script only."""


# ─────────────────────────────────────────────────────────────────────────────
# 7. SUNDAY_INSURANCE_SCRIPT_PROMPT  — Week 2: insurance & protection
# Variables: {topic}, {week_context}, {audience_level}
# Output: 280-350 words, reassuring-but-energetic tone
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_INSURANCE_SCRIPT_PROMPT = """\
Write a Sunday educational script for DriftWire326 on INSURANCE & FINANCIAL PROTECTION.

TOPIC: {topic}
WEEK CONTEXT: {week_context}
AUDIENCE LEVEL: {audience_level}

Tone: reassuring but energetic — the friend who explains why insurance actually matters
before you need it. Word count: 280-350 words.

Structure:

[HOOK] — 10 seconds
Open with a "what if" scenario or a real statistic about financial risk.

[THE PROBLEM] — 50 seconds
Why do people underestimate this risk? What's the cost of being unprotected?
Use real figures.

[HOW IT WORKS] — 80 seconds
Explain the insurance product or protection concept clearly.
Avoid sales language — stay educational. Real examples.

[WHAT TO LOOK FOR] — 60 seconds
Key features, common pitfalls, questions to ask. Empower the viewer, not advise them.

[KEY TAKEAWAY] — 20 seconds
The one thing to remember.

[CTA] — 15 seconds
Subscribe, share, and a comment question about their biggest financial concern.

Rules:
- Credibility anchors required ("according to", "data shows", "reported", etc.)
- Never recommend a specific product or provider.
- Close with full disclaimer and AI disclosure.

Return plain text only."""


# ─────────────────────────────────────────────────────────────────────────────
# 8. SUNDAY_SAVINGS_SCRIPT_PROMPT  — Week 3: savings & wealth building
# Variables: {topic}, {week_context}, {audience_level}
# Output: 280-350 words, motivational wealth-building tone
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_SAVINGS_SCRIPT_PROMPT = """\
Write a Sunday educational script for DriftWire326 on SAVINGS & WEALTH BUILDING.

TOPIC: {topic}
WEEK CONTEXT: {week_context}
AUDIENCE LEVEL: {audience_level}

Tone: motivational, data-grounded wealth coach — compound interest is your friend,
but you have to start. Inspiring without being preachy. Word count: 280-350 words.

Structure:

[HOOK] — 10 seconds
A compound growth stat that makes viewers say "wait, really?"

[THE OPPORTUNITY] — 60 seconds
What this savings or wealth-building vehicle offers. Clear, jargon-free.
Ground in current rates or market context.

[HOW TO START] — 80 seconds
Concrete, actionable framework. Dollar amounts should be ranges ("$50-$100/month"),
never specific prescriptions. Use examples.

[COMMON MISTAKES] — 50 seconds
The top two or three mistakes young investors make here and how to avoid them.

[KEY TAKEAWAY] — 20 seconds
One sentence that sticks.

[CTA] — 15 seconds
Subscribe + ask viewers to share how they're building wealth.

Rules:
- Credibility anchors required.
- Frame actions as options ("one approach is…", "some investors choose…"), never commands.
- Close with full disclaimer and AI disclosure.

Return plain text only."""


# ─────────────────────────────────────────────────────────────────────────────
# 9. SUNDAY_BONUS_SCRIPT_PROMPT  — Week 4: rotating bonus theme
# Variables: {topic}, {bonus_theme}, {week_context}, {audience_level}
# Output: 280-350 words, tone adapts to bonus_theme
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_BONUS_SCRIPT_PROMPT = """\
Write a Sunday educational script for DriftWire326 on this week's BONUS THEME.

TOPIC: {topic}
BONUS THEME: {bonus_theme}
WEEK CONTEXT: {week_context}
AUDIENCE LEVEL: {audience_level}

Adapt tone to the theme:
- real_estate       → grounded, opportunity-focused
- crypto_digital    → balanced, hype-aware, data-first
- tax_strategy      → practical, detail-oriented, empowering
- retirement_planning → calm, long-horizon, reassuring
- side_income       → entrepreneurial, realistic, energetic
- macro_finance     → big-picture, analytical, connecting dots

Word count: 280-350 words.

Structure:

[HOOK] — 10 seconds
The sharpest angle on this theme right now.

[WHAT IT IS] — 60 seconds
Clear definition + why it matters in the current market environment.

[HOW IT WORKS / KEY MECHANICS] — 80 seconds
The practical framework. Real examples, current data.

[OPPORTUNITY & RISK] — 50 seconds
Both sides honestly. Not sales, not fear — balanced perspective with data.

[KEY TAKEAWAY] — 20 seconds
The one thing viewers should remember this week.

[CTA] — 15 seconds
Subscribe + theme-relevant comment prompt.

Rules:
- Credibility anchors required.
- No specific product recommendations.
- Crypto scripts must include extra caution: "highly speculative asset class".
- Close with full disclaimer and AI disclosure.

Return plain text only."""


# ─────────────────────────────────────────────────────────────────────────────
# 10. TITLE_MAIN_PROMPT  — weekday main video
# Variables: {topic}, {anchor_number}, {sentiment}, {ticker}
# Output: JSON array of 3 scored title options, 40-65 chars each
# ─────────────────────────────────────────────────────────────────────────────

TITLE_MAIN_PROMPT = """\
Generate 3 YouTube title options for a DriftWire326 weekday finance video.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
SENTIMENT: {sentiment}
TICKER / ASSET: {ticker}

Title rules:
- Length: 40-65 characters (including spaces)
- Must include at least one specific number
- Trigger: curiosity or urgency — factual, never misleading clickbait
- Power words allowed: Crashed, Surged, Shocked, Breaking, Warning, Record, Revealed
- Do NOT start with "How to" — this is news, not a tutorial
- Include ticker symbol if it fits naturally

Score each title 0-100 across:
  ctr_potential (will it stop the scroll?): 0-40 pts
  seo_strength (searchable keywords): 0-30 pts
  brand_fit (matches DriftWire326 voice): 0-30 pts

Return ONLY valid JSON — no markdown:
[
  {{"title": "<title 1>", "score": <int>, "ctr_potential": <int>, "seo_strength": <int>, "brand_fit": <int>}},
  {{"title": "<title 2>", "score": <int>, "ctr_potential": <int>, "seo_strength": <int>, "brand_fit": <int>}},
  {{"title": "<title 3>", "score": <int>, "ctr_potential": <int>, "seo_strength": <int>, "brand_fit": <int>}}
]"""


# ─────────────────────────────────────────────────────────────────────────────
# 11. TITLE_SHORTS_PROMPT
# Variables: {topic}, {anchor_number}, {sentiment}, {ticker}
# Output: JSON array of 3 scored titles, <40 chars, declarative punch
# ─────────────────────────────────────────────────────────────────────────────

TITLE_SHORTS_PROMPT = """\
Generate 3 YouTube Shorts title options for DriftWire326.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
SENTIMENT: {sentiment}
TICKER / ASSET: {ticker}

Shorts title rules:
- Length: under 40 characters (shorter = better for mobile)
- Declarative statement, not a question
- Include the number if it fits (e.g. "+8% TODAY")
- All caps or mixed caps for punch (e.g. "MARKET JUST CRASHED")
- Add #shorts only if character budget allows
- Optimise for mobile thumbnail readability

Score each 0-100: ctr_potential (0-50), mobile_readability (0-30), brand_fit (0-20).

Return ONLY valid JSON:
[
  {{"title": "<title 1>", "score": <int>, "ctr_potential": <int>, "mobile_readability": <int>, "brand_fit": <int>}},
  {{"title": "<title 2>", "score": <int>, "ctr_potential": <int>, "mobile_readability": <int>, "brand_fit": <int>}},
  {{"title": "<title 3>", "score": <int>, "ctr_potential": <int>, "mobile_readability": <int>, "brand_fit": <int>}}
]"""


# ─────────────────────────────────────────────────────────────────────────────
# 12. TITLE_SUNDAY_PROMPT
# Variables: {topic}, {theme}, {audience_level}
# Output: JSON array of 3 scored educational-formula titles, 45-65 chars
# ─────────────────────────────────────────────────────────────────────────────

TITLE_SUNDAY_PROMPT = """\
Generate 3 YouTube title options for a DriftWire326 Sunday educational video.

TOPIC: {topic}
THEME: {theme}
AUDIENCE LEVEL: {audience_level}

Sunday title rules:
- Length: 45-65 characters
- Use educational formulas: "What Is X?", "X Explained", "The Truth About X",
  "Why X Matters", "X: What Nobody Tells You", "X in 3 Minutes"
- Promise a clear learning outcome
- Do NOT use scare tactics or urgent language — this is educational
- Numbers are a bonus but not required

Score each 0-100: educational_value (0-40), search_intent_match (0-35), brand_fit (0-25).

Return ONLY valid JSON:
[
  {{"title": "<title 1>", "score": <int>, "educational_value": <int>, "search_intent_match": <int>, "brand_fit": <int>}},
  {{"title": "<title 2>", "score": <int>, "educational_value": <int>, "search_intent_match": <int>, "brand_fit": <int>}},
  {{"title": "<title 3>", "score": <int>, "educational_value": <int>, "search_intent_match": <int>, "brand_fit": <int>}}
]"""


# ─────────────────────────────────────────────────────────────────────────────
# 13. DESCRIPTION_PROMPT
# Variables: {title}, {script_summary}, {video_type}, {timestamps}, {tags}
# Output: full YouTube description, SEO-optimised, <5000 chars
# ─────────────────────────────────────────────────────────────────────────────

DESCRIPTION_PROMPT = """\
Write a complete YouTube description for a DriftWire326 video. Max 5000 characters.

TITLE: {title}
VIDEO TYPE: {video_type}
SCRIPT SUMMARY: {script_summary}
TIMESTAMPS: {timestamps}
TAGS: {tags}

Description structure (in this order):

HOOK LINE (first 150 chars — critical for search snippet):
One punchy sentence that mirrors the title's promise. Include the anchor number.

BODY (200-350 words):
Expand on what viewers will learn/see. Use 3-5 short paragraphs or bullet points.
Include: key statistics mentioned, why this matters today, what's covered.
Naturally weave in SEO keywords (stock market, investing, [ticker], 2026, etc.)

TIMESTAMPS:
{timestamps}
(Format: 00:00 Section Name — one per line)

LINKS SECTION:
📊 Track our picks (placeholder): [PORTFOLIO_LINK]
📰 Source data: [FRED_LINK] | [YAHOO_FINANCE_LINK]
📧 Business inquiries: [CONTACT_EMAIL]

SOCIAL:
🐦 Twitter/X: @DriftWire326
📸 Instagram: @DriftWire326
💬 Discord: [DISCORD_LINK]

HASHTAGS (20 max):
{tags}

DISCLAIMER:
This content is for informational and entertainment purposes only and does not constitute \
financial advice. Always consult a licensed financial advisor before making any investment \
decisions. Narration is AI-generated.

---
Write all sections in order. Keep tone punchy and professional. Return plain text."""


# ─────────────────────────────────────────────────────────────────────────────
# 14. TAGS_PROMPT
# Variables: {title}, {topic}, {ticker}, {video_type}
# Output: JSON array of exactly 20 YouTube tags
# ─────────────────────────────────────────────────────────────────────────────

TAGS_PROMPT = """\
Generate exactly 20 YouTube tags for a DriftWire326 video.

TITLE: {title}
TOPIC: {topic}
TICKER / ASSET: {ticker}
VIDEO TYPE: {video_type}

Tag rules:
- Always include these 4 channel tags: "DriftWire326", "drift wire326", "stock market 2026",
  "investing for beginners"
- Remaining 16 tags: mix of broad (stock market, investing) and specific ({ticker}, topic keywords)
- Tags should be 1-5 words each
- No hashtag symbols — plain text only
- Prioritise what people actually search on YouTube
- Include at least one "how to" keyword variant and one news keyword variant

Return ONLY a valid JSON array of exactly 20 strings:
["tag1", "tag2", ..., "tag20"]"""


# ─────────────────────────────────────────────────────────────────────────────
# 15. PROMISE_MATCH_PROMPT
# Variables: {title}, {script_excerpt}
# Output: JSON {matched: bool, reason: str, confidence: float}
# ─────────────────────────────────────────────────────────────────────────────

PROMISE_MATCH_PROMPT = """\
Check whether this YouTube video's script delivers on the promise made in its title.

TITLE: {title}

SCRIPT EXCERPT (first 300 words):
{script_excerpt}

Evaluate:
1. Does the script address the specific claim or question raised in the title?
2. Is the anchor number / key fact from the title present in the script?
3. Would a viewer who clicked for the title's promise feel satisfied by this content?

Return ONLY valid JSON — no markdown, no explanation:
{{
  "matched": true,
  "reason": "<one sentence explaining why it matches or doesn't>",
  "confidence": 0.92
}}

confidence is a float 0.0-1.0 representing how certain you are of your matched verdict."""


# ─────────────────────────────────────────────────────────────────────────────
# 16. SUNDAY_TOPIC_SELECTOR_PROMPT
# Variables: {theme}, {week_market_summary}, {available_topics_list}
# Output: JSON — selected topic with rationale and content angle
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_TOPIC_SELECTOR_PROMPT = """\
Select the best Sunday educational topic for DriftWire326 this week.

THEME FOR THIS SUNDAY: {theme}
WEEK'S MARKET SUMMARY: {week_market_summary}
AVAILABLE TOPICS (not recently used):
{available_topics_list}

Selection criteria:
1. Relevance — does this topic connect naturally to what happened in markets this week?
2. Timeliness — is there a current news hook that makes this topic more compelling right now?
3. Audience fit — will Gen Z / Millennial investors find this genuinely useful?
4. Content richness — enough data, examples, and angles for a 3-5 minute educational video?

Return ONLY valid JSON:
{{
  "selected_topic": "<exact topic name from the available list>",
  "theme": "{theme}",
  "market_connection": "<one sentence linking this topic to this week's markets>",
  "content_angle": "<the specific angle or hook to lead with>",
  "key_stat_to_research": "<one data point to look up before scripting>",
  "confidence": 0.88
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# BACKWARD-COMPATIBLE TEMPLATE ALIASES
# Generators built before the Module 1 rebuild use string.Template.substitute().
# These aliases preserve that API so existing modules keep working unchanged.
# ─────────────────────────────────────────────────────────────────────────────

WEEKDAY_SCRIPT_PROMPT = Template("""\
You are writing the complete script for today's DriftWire326 market recap video.

TODAY'S MARKET DATA:
$market_data

EARNINGS NEWS:
$earnings_data

ECONOMIC NEWS:
$economic_data

TRENDING HEADLINES:
$headlines

Write a complete, punchy 2-3 minute script (approximately 350-420 words) following this EXACT structure:

[HOOK] — 5 seconds. One explosive opening stat. Start mid-action.
[WHAT HAPPENED] — 40 seconds. Who, what, when, triggered by what catalyst. Two or more data points.
[WHY IT MATTERS] — 50 seconds. Cause → ripple effects. Source every claim.
[NUMBERS DEEP DIVE] — 40 seconds. Three or more specific figures. Historical context.
[WHAT HAPPENS NEXT] — 25 seconds. Key catalysts to watch. No price targets.
[CTA] — 15 seconds. Subscribe prompt + comment question.

Rules:
- Include at least one credibility anchor: "according to", "data shows", "reported", "as of today",
  "markets indicate", "analysts note", "figures show", "sources indicate", "market data suggests"
- NEVER use: "you should buy", "guaranteed return", "this will go up", "best investment",
  "I recommend buying", "go all in", "this is a sure thing"
- Close with: "This content is for informational and entertainment purposes only and does not
  constitute financial advice. Always consult a licensed financial advisor before making any
  investment decisions. Narration is AI-generated."

Return plain text only — no markdown outside the section headers.\
""")

SUNDAY_SCRIPT_PROMPT = Template("""\
You are writing the complete script for DriftWire326's Sunday educational video.

TOPIC: $topic
SUBTOPICS: $subtopics
KEY CONCEPTS: $key_concepts
CURRENT RELEVANCE: $current_relevance

Write a complete 280-350 word educational script following this structure:

[HOOK] — 10 seconds. A surprising fact or counter-intuitive truth.
[WHAT IS IT] — 60 seconds. Clear definition + one strong real-world analogy.
[HOW IT WORKS] — 80 seconds. Step-by-step mechanics. Real 2025-2026 examples with data.
[WHY IT MATTERS TO YOU] — 50 seconds. Connection to everyday investing for a 25-year-old.
[KEY TAKEAWAY] — 20 seconds. One memorable sentence.
[CTA] — 15 seconds. Subscribe prompt + comment question.

Rules:
- Credibility anchors required ("according to", "data shows", "as of today", etc.)
- No advisory language.
- Close with full disclaimer and AI disclosure.

Return plain text script only.\
""")

TITLE_GENERATION_PROMPT = Template("""\
Generate 10 YouTube title options for a finance video. Return ONLY a JSON array of strings.

Topic: $topic
Key stat/hook: $key_stat
Video type: $video_type (weekday_recap | sunday_educational | shorts)
Ticker: $specific_ticker
Channel: DriftWire326 — US finance, Gen Z/Millennial audience

Title rules:
- 40-65 characters for main videos, under 40 for shorts
- Include numbers where possible
- Trigger curiosity or urgency (factual — not misleading clickbait)
- Power words: Crashed, Surged, Revealed, Warning, Breaking, Record, Shocked
- SEO keywords: stock market, investing, specific ticker if relevant

Return exactly: ["title1", "title2", ..., "title10"]\
""")

COMPLIANCE_REVIEW_PROMPT = Template("""\
Review this YouTube finance script for compliance issues.

SCRIPT:
$script

Check for:
1. Financial advice language (should/must buy, sell, invest recommendations)
2. Return guarantees or price targets stated as fact
3. Unsubstantiated claims lacking a cited source
4. Missing disclaimer
5. Misleading or cherry-picked statistics

Return valid JSON only:
{
  "passed": true,
  "issues": ["issue1", "issue2"],
  "suggested_fixes": ["fix1", "fix2"],
  "risk_level": "low|medium|high",
  "disclaimer_present": true
}\
""")
