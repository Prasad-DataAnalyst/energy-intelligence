"""
config/prompts.py — DriftWire326 Claude API Prompt Library
All 16 production prompts for script generation, titles, descriptions, and compliance.
Use str.format(**kwargs) at call-site — never mutate these constants.

Backward-compatible string.Template aliases at the bottom preserve the
.substitute() API used by existing generators until each is upgraded.
"""
from string import Template

# ─────────────────────────────────────────────────────────────────────────────
# SHARED SYSTEM PERSONA  (pass as system= on every Claude call)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PERSONA = (
    "You are the head writer for DriftWire326, a high-energy YouTube finance channel "
    "targeting US Gen Z and Millennial investors aged 18-35. "
    "Tone: punchy, credible, fast-paced — like CNBC Fast Money with TikTok energy. "
    "Rules: never give financial advice; frame everything as news, education, or analysis; "
    "always include the disclaimer when covering individual stocks; never guarantee returns "
    "or predict specific prices; use plain English and explain jargon immediately; "
    "hook the viewer in the first 10 seconds; cite data and sources; end with a CTA."
)

# ─────────────────────────────────────────────────────────────────────────────
# 1. TOPIC_SELECTOR_PROMPT
# Input:  {topics_json}, {date}, {day_type}
# Output: JSON array — top 3 topics ranked by gossip/news value
# ─────────────────────────────────────────────────────────────────────────────

TOPIC_SELECTOR_PROMPT = """\
You are selecting the best finance story for a DriftWire326 YouTube video.

DATE: {date}
DAY TYPE: {day_type}
AVAILABLE TOPICS (JSON):
{topics_json}

Rank the top 3 topics by combined gossip/news value:
- Viral potential (will Gen Z / Millennial investors stop their scroll?)
- Recency (happened in the last 24 hours scores highest)
- Emotional hook strength (fear, greed, surprise, outrage)
- Data richness (specific numbers available to cite)
- Story clarity (clear cause → effect → so-what)

Return ONLY valid JSON — no markdown, no commentary:
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
# 2. WEEKDAY_SCRIPT_TIER1_PROMPT — breakout story (≥5% move)
# Input:  {topic}, {anchor_number}, {context}, {style}, {hook}
# Output: 420-500 word script, urgent breaking-news tone
# Structure: Hook(5s) → What Happened(40s) → Why It Matters(50s) →
#            Numbers Deep Dive(40s) → What Happens Next(25s) → CTA(15s)
# ─────────────────────────────────────────────────────────────────────────────

WEEKDAY_SCRIPT_TIER1_PROMPT = """\
Write a TIER-1 BREAKOUT script for DriftWire326. Style: {style}.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
CONTEXT / DATA: {context}
OPENING HOOK: {hook}

This is a MAJOR market move (≥5%). Energy: live breaking-news coverage, urgent, electric.
Word count: 420-500 words. Every second earns its place.

Use these EXACT section headers:

[HOOK] — 5 seconds
One explosive sentence built around the anchor number. Start mid-action, no preamble.
Open with: "{hook}"

[WHAT HAPPENED] — 40 seconds
The who-what-when of the move. Minimum two specific data points.
Short declarative sentences. Zero filler.

[WHY IT MATTERS] — 50 seconds
Cause → ripple effects. Who wins? Who loses? What does this signal for the broader market?
Every claim grounded in data or a named source.

[NUMBERS DEEP DIVE] — 40 seconds
Go granular: percentage moves, trading volume, comparisons to 52-week range or peer group,
one historical parallel. Minimum three distinct numbers.

[WHAT HAPPENS NEXT] — 25 seconds
Forward-looking but never predictive. Frame as: "Traders are watching…" / "Key level to monitor…"
No price targets. No guarantees.

[CTA] — 15 seconds
"If this caught you off guard, subscribe so the next move doesn't. Drop a comment — \
did you see this coming?"

Compliance rules (non-negotiable):
- Must include at least one of: "according to", "data shows", "reported", "as of today",
  "markets indicate", "analysts note", "figures show", "sources indicate", "market data suggests"
- Must NOT contain: "you should buy", "you should sell", "guaranteed return", "this will go up",
  "best investment", "I recommend buying", "do not miss this opportunity",
  "you need to purchase", "go all in", "this is a sure thing"
- Final line must be: "This content is for informational and entertainment purposes only and \
does not constitute financial advice. Always consult a licensed financial advisor before \
making any investment decisions. Narration is AI-generated."

Return plain script text only — section headers included, no other markdown."""

# ─────────────────────────────────────────────────────────────────────────────
# 3. WEEKDAY_SCRIPT_TIER2_PROMPT — notable story (2%–4.99% move)
# Input:  {topic}, {anchor_number}, {context}, {style}, {hook}
# Output: 420-500 word script, confident anchor tone
# Structure: Hook(5s) → Story(60s) → Analysis(60s) → Context(25s) → CTA(15s)
# ─────────────────────────────────────────────────────────────────────────────

WEEKDAY_SCRIPT_TIER2_PROMPT = """\
Write a TIER-2 NOTABLE MOVE script for DriftWire326. Style: {style}.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
CONTEXT / DATA: {context}
OPENING HOOK: {hook}

This is a meaningful but not explosive event (2–4.99% move).
Tone: confident, informed market anchor — authoritative without being breathless.
Word count: 420-500 words.

Use these EXACT section headers:

[HOOK] — 5 seconds
Crisp data-driven opener. Intrigue over alarm. Use: "{hook}"

[STORY] — 60 seconds
Full narrative: what moved, by how much, when, and triggered by which catalyst.
Two or more specific data points. Cause and effect clearly connected.

[ANALYSIS] — 60 seconds
Deeper read: what does this move reveal about the sector, macro environment, or investor sentiment?
Connect to at least one broader theme. Source every assertion.

[CONTEXT] — 25 seconds
Upcoming catalysts to monitor: earnings dates, Fed meetings, data releases.
Frame as "watch for" — never as a price prediction.

[CTA] — 15 seconds
Invite engagement: a specific question for the comments + subscribe reminder.

Compliance rules (non-negotiable):
- Must include at least one credibility anchor: "according to", "data shows", "reported",
  "as of today", "markets indicate", "analysts note", "figures show",
  "sources indicate", "market data suggests"
- Must NOT contain advisory language: "you should buy/sell", "guaranteed return",
  "this will go up", "best investment", "I recommend buying", "go all in",
  "this is a sure thing"
- Final line: "This content is for informational and entertainment purposes only and \
does not constitute financial advice. Always consult a licensed financial advisor before \
making any investment decisions. Narration is AI-generated."

Return plain script text only."""

# ─────────────────────────────────────────────────────────────────────────────
# 4. WEEKDAY_SCRIPT_TIER3_PROMPT — routine / educational day
# Input:  {topic}, {context}, {style}, {hook}
# Output: 420-500 word script, educational tone
# Structure: Hook(5s) → Context(50s) → Explanation(70s) → Takeaway(25s) → CTA(15s)
# ─────────────────────────────────────────────────────────────────────────────

WEEKDAY_SCRIPT_TIER3_PROMPT = """\
Write a TIER-3 ROUTINE / EDUCATIONAL script for DriftWire326. Style: {style}.

TOPIC: {topic}
CONTEXT / DATA: {context}
OPENING HOOK: {hook}

Markets were quiet today — lean into education. Help viewers understand the underlying
concept, trend, or data point. Tone: warm, knowledgeable friend who happens to follow
the market closely. Word count: 420-500 words.

Use these EXACT section headers:

[HOOK] — 5 seconds
Open with a relatable question or "did you know" angle. Use: "{hook}"

[CONTEXT] — 50 seconds
Describe today's market action matter-of-factly grounded in real numbers.
What happened? Where did markets close? What was the standout data point?

[EXPLANATION] — 70 seconds
Pivot to the educational angle. Explain the underlying concept, metric, or sector
dynamic that drives today's story. Use a plain-language analogy. Cite at least
two data points (year-to-date, vs. historical average, vs. index, etc.).

[TAKEAWAY] — 25 seconds
One clear insight the viewer can carry with them. Not advice — perspective.
"What this tells us is…" / "The pattern here suggests…"

[CTA] — 15 seconds
Ask a learning question: "What concept do you want us to break down next?
Drop it below and subscribe so you don't miss it."

Compliance rules (non-negotiable):
- Must include at least one credibility anchor: "according to", "data shows", "reported",
  "as of today", "markets indicate", "analysts note", "figures show",
  "sources indicate", "market data suggests"
- Must NOT contain advisory language
- Final line: "This content is for informational and entertainment purposes only and \
does not constitute financial advice. Always consult a licensed financial advisor before \
making any investment decisions. Narration is AI-generated."

Return plain script text only."""

# ─────────────────────────────────────────────────────────────────────────────
# 5. SHORTS_SCRIPT_PROMPT
# Input:  {topic}, {anchor_number}, {tier}
# Output: exactly 5 text cards, total read time under 55 seconds
# Card specs: word limits + display color
# ─────────────────────────────────────────────────────────────────────────────

SHORTS_SCRIPT_PROMPT = """\
Write a YouTube Shorts script for DriftWire326. Format: exactly 5 TEXT CARDS.
Total narration read time must be UNDER 55 SECONDS at a natural speaking pace.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
STORY TIER: {tier}

Each card = one screen of on-screen text read aloud by the narrator.
Follow the word limits and color specs exactly.

CARD 1 — HOOK  |  display color: NEON GREEN (#00FF41)
Max 8 words. The most shocking outcome or number. No greeting, no "hey guys".
Example: "Apple just lost 180 billion in one day."

CARD 2 — THE NUMBER  |  display color: WHITE (#FFFFFF)
Max 4 words + the anchor number. Make the stat the visual centerpiece.
Example: "Down {anchor_number} in hours."

CARD 3 — THE REASON  |  display color: WHITE (#FFFFFF)
Max 12 words. One sentence. Clear cause. Cite the source or name the trigger.
Example: "Revenue missed estimates by 8% — worst beat in three years."

CARD 4 — THE CONTEXT  |  display color: WHITE (#FFFFFF)
Max 20 words. Two short sentences. Zoom out: what this means for investors or the sector.
Example: "Tech stocks fell across the board. Traders are watching the Fed's next move closely."

CARD 5 — CTA  |  display color: NEON GREEN (#00FF41)
Max 8 words. Drive the action.
Example: "Follow @DriftWire326 for daily market moves 👇"

Hard rules:
- Total word count across all 5 cards: 75-110 words (max 120)
- No financial advice language whatsoever
- End note appended below Card 5 (not read aloud): "Not financial advice. AI narration."

Return ONLY the 5 cards in this exact format — no other text:
CARD 1: <text>
CARD 2: <text>
CARD 3: <text>
CARD 4: <text>
CARD 5: <text>"""

# ─────────────────────────────────────────────────────────────────────────────
# 6. SUNDAY_INVESTMENT_SCRIPT_PROMPT — Week 1: investment banking & markets
# Input:  {topic}, {week_context}
# Output: 420-500 word educational script, engaging teacher tone
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_INVESTMENT_SCRIPT_PROMPT = """\
Write a Sunday educational script for DriftWire326 on INVESTMENT BANKING & MARKETS.

TOPIC: {topic}
THIS WEEK'S MARKET CONTEXT: {week_context}

Tone: engaging teacher who genuinely loves finance and wants everyone to understand it.
Real numbers and real examples only. Word count: 420-500 words.

Structure (use these section headers):

[HOOK] — 10 seconds
Lead with a surprising fact or counter-intuitive truth about investment banking or markets.
Tie it to something that happened this week: "{week_context}"

[WHAT IS IT] — 60 seconds
Define the concept clearly. One strong real-world analogy that a 22-year-old gets instantly.

[HOW IT WORKS] — 90 seconds
Walk through the mechanics step by step. Use real 2025-2026 examples and cite data.
At least two specific numbers (dollar amounts, percentages, timelines).

[WHY IT MATTERS TO YOU] — 50 seconds
Connect it to everyday investing: how does this affect someone's 401k, brokerage, or returns?

[KEY TAKEAWAY] — 20 seconds
One memorable sentence the viewer can repeat to a friend.

[CTA] — 15 seconds
"Subscribe for a new deep-dive every Sunday. What should we cover next — drop it below."

Compliance rules:
- Must include at least one credibility anchor ("according to", "data shows", "as of today", etc.)
- No advisory language ("you should buy", "invest in", "guaranteed return", etc.)
- Final line: "This content is for informational and entertainment purposes only and \
does not constitute financial advice. Always consult a licensed financial advisor before \
making any investment decisions. Narration is AI-generated."

Return plain script text only."""

# ─────────────────────────────────────────────────────────────────────────────
# 7. SUNDAY_INSURANCE_SCRIPT_PROMPT — Week 2: insurance & protection
# Input:  {topic}, {week_context}
# Output: 420-500 word educational script, insurance/protection tone
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_INSURANCE_SCRIPT_PROMPT = """\
Write a Sunday educational script for DriftWire326 on INSURANCE & FINANCIAL PROTECTION.

TOPIC: {topic}
THIS WEEK'S MARKET CONTEXT: {week_context}

Tone: reassuring but energetic — the friend who explains why protection matters
before you need it. Empowering, never fear-mongering. Word count: 420-500 words.

Structure:

[HOOK] — 10 seconds
Open with a "what if" scenario or a real statistic about financial risk.
Anchor it in this week's market news: "{week_context}"

[THE PROBLEM] — 50 seconds
Why do people underestimate this risk? What is the real cost of going unprotected?
Use real figures (average claim costs, percentage of Americans uninsured, etc.).

[HOW IT WORKS] — 80 seconds
Explain the insurance product or protection concept clearly.
Educational framing only — no sales language, no provider recommendations.
Real dollar examples.

[WHAT TO LOOK FOR] — 60 seconds
Key features, common pitfalls, questions to ask an advisor.
Empower the viewer with knowledge — not prescriptions.

[KEY TAKEAWAY] — 20 seconds
The one thing to walk away remembering.

[CTA] — 15 seconds
"Subscribe and share with someone who needs to hear this. What's your biggest financial concern?"

Compliance rules:
- Credibility anchors required ("according to", "data shows", "reported", etc.)
- Never recommend a specific product, provider, or coverage amount
- Final line: "This content is for informational and entertainment purposes only and \
does not constitute financial advice. Always consult a licensed financial advisor before \
making any investment decisions. Narration is AI-generated."

Return plain script text only."""

# ─────────────────────────────────────────────────────────────────────────────
# 8. SUNDAY_SAVINGS_SCRIPT_PROMPT — Week 3: savings & wealth building
# Input:  {topic}, {week_context}
# Output: 420-500 word educational script, motivational wealth-building tone
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_SAVINGS_SCRIPT_PROMPT = """\
Write a Sunday educational script for DriftWire326 on SAVINGS & WEALTH BUILDING.

TOPIC: {topic}
THIS WEEK'S MARKET CONTEXT: {week_context}

Tone: motivational, data-grounded wealth coach — compound interest is your friend,
but you have to start today. Inspiring without being preachy. Word count: 420-500 words.

Structure:

[HOOK] — 10 seconds
A compound growth stat that makes viewers say "wait, really?"
Tie it to this week if possible: "{week_context}"

[THE OPPORTUNITY] — 60 seconds
What this savings or wealth-building vehicle offers. Clear, jargon-free.
Ground in current rates or recent market context. Real numbers.

[HOW TO START] — 80 seconds
Concrete, accessible framework. Dollar amounts as ranges ("$50-$200/month"),
never specific prescriptions. Walk through a realistic example.

[COMMON MISTAKES] — 50 seconds
The top two or three mistakes young investors make here and how to sidestep them.
Cite data where possible (average savings rate, median emergency fund, etc.).

[KEY TAKEAWAY] — 20 seconds
One sentence that sticks: short, punchy, shareable.

[CTA] — 15 seconds
"Subscribe and share — tag someone who needs to start building wealth today."

Compliance rules:
- Credibility anchors required
- Frame actions as options ("one approach is…", "some investors choose…"), never commands
- Final line: "This content is for informational and entertainment purposes only and \
does not constitute financial advice. Always consult a licensed financial advisor before \
making any investment decisions. Narration is AI-generated."

Return plain script text only."""

# ─────────────────────────────────────────────────────────────────────────────
# 9. SUNDAY_BONUS_SCRIPT_PROMPT — Week 4: rotating bonus theme
# Input:  {topic}, {bonus_theme}, {week_context}
# Output: 420-500 word script, tone adapts to bonus_theme
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_BONUS_SCRIPT_PROMPT = """\
Write a Sunday educational script for DriftWire326 on this week's BONUS THEME.

TOPIC: {topic}
BONUS THEME: {bonus_theme}
THIS WEEK'S MARKET CONTEXT: {week_context}

Adapt tone to the theme:
- real_estate       → grounded, opportunity-focused, patient investor mindset
- crypto_digital    → balanced, hype-aware, data-first, includes volatility caution
- tax_strategy      → practical, detail-oriented, empowering ("you can do this")
- retirement_planning → calm, long-horizon, reassuring, compound-interest optimism
- side_income       → entrepreneurial, realistic, energetic, avoid get-rich-quick framing
- macro_finance     → big-picture, analytical, connecting market dots for the everyday investor

Word count: 420-500 words.

Structure:

[HOOK] — 10 seconds
The sharpest current angle on this theme. Connect to this week: "{week_context}"

[WHAT IT IS] — 60 seconds
Clear definition + why it matters in the current market environment. No jargon without explanation.

[HOW IT WORKS / KEY MECHANICS] — 80 seconds
Practical framework with real examples and current data.
At least two specific figures.

[OPPORTUNITY & RISK] — 50 seconds
Both sides honestly. Not a sales pitch, not fear — balanced, data-backed perspective.
Crypto scripts must note: "highly speculative asset class."

[KEY TAKEAWAY] — 20 seconds
The one thing viewers remember this week.

[CTA] — 15 seconds
Theme-relevant comment prompt + subscribe.

Compliance rules:
- Credibility anchors required
- No specific product or investment recommendations
- Final line: "This content is for informational and entertainment purposes only and \
does not constitute financial advice. Always consult a licensed financial advisor before \
making any investment decisions. Narration is AI-generated."

Return plain script text only."""

# ─────────────────────────────────────────────────────────────────────────────
# 10. TITLE_MAIN_PROMPT — weekday main video
# Input:  {topic}, {anchor_number}, {script_summary}
# Output: JSON with 3 scored options (40-65 chars each) + recommended
# ─────────────────────────────────────────────────────────────────────────────

TITLE_MAIN_PROMPT = """\
Generate 3 YouTube title options for a DriftWire326 weekday finance video.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
SCRIPT SUMMARY: {script_summary}

Title rules — every option must:
- Be 40-65 characters long (count spaces)
- Include the anchor number ({anchor_number}) in the title
- Lead with a target keyword in the first 5 words (stock, market, S&P, Fed, earnings, etc.)
- Trigger curiosity or urgency — factual, never misleading
- Avoid starting with "How to" (this is news, not a tutorial)

Score each option 0-100:
  - keyword_placement (target word in first 5 words): 0-35
  - emotional_hook (urgency / curiosity / surprise): 0-35
  - clarity (viewer instantly knows what the video is about): 0-30

Pick the highest-scoring option as recommended.

Return ONLY valid JSON:
{{
  "options": [
    {{"title": "<title>", "score": <int>, "keyword_placement": <int>, "emotional_hook": <int>, "clarity": <int>}},
    {{"title": "<title>", "score": <int>, "keyword_placement": <int>, "emotional_hook": <int>, "clarity": <int>}},
    {{"title": "<title>", "score": <int>, "keyword_placement": <int>, "emotional_hook": <int>, "clarity": <int>}}
  ],
  "recommended": "<exact title string of highest scoring option>"
}}"""

# ─────────────────────────────────────────────────────────────────────────────
# 11. TITLE_SHORTS_PROMPT
# Input:  {topic}, {anchor_number}, {hook_card_text}
# Output: JSON with 3 options (under 40 chars hard cap) + recommended
# ─────────────────────────────────────────────────────────────────────────────

TITLE_SHORTS_PROMPT = """\
Generate 3 YouTube Shorts title options for DriftWire326.

TOPIC: {topic}
ANCHOR NUMBER: {anchor_number}
HOOK CARD TEXT (Card 1 of the short): {hook_card_text}

Title rules — every option must:
- Be UNDER 40 characters — hard cap, no exceptions
- Be a declarative statement (NO questions — no "?")
- Echo the hook card text (use similar keywords or the same number)
- Use ALL CAPS or Mixed CAPS for visual punch on mobile
- Include the anchor number if it fits within the character cap

Score each option 0-100:
  - hook_echo (mirrors the hook card text): 0-40
  - mobile_impact (punchy, readable on small screen): 0-35
  - declarative_strength (bold statement, not a question): 0-25

Return ONLY valid JSON:
{{
  "options": [
    {{"title": "<title>", "score": <int>, "hook_echo": <int>, "mobile_impact": <int>, "declarative_strength": <int>}},
    {{"title": "<title>", "score": <int>, "hook_echo": <int>, "mobile_impact": <int>, "declarative_strength": <int>}},
    {{"title": "<title>", "score": <int>, "hook_echo": <int>, "mobile_impact": <int>, "declarative_strength": <int>}}
  ],
  "recommended": "<exact title string of highest scoring option>"
}}"""

# ─────────────────────────────────────────────────────────────────────────────
# 12. TITLE_SUNDAY_PROMPT
# Input:  {topic}, {sunday_theme}
# Output: JSON with 3 educational-formula options + recommended
# ─────────────────────────────────────────────────────────────────────────────

TITLE_SUNDAY_PROMPT = """\
Generate 3 YouTube title options for a DriftWire326 Sunday educational video.

TOPIC: {topic}
SUNDAY THEME: {sunday_theme}

Use educational title formulas — pick the 3 that best fit this topic:
- "What Is [Term]? 2-Minute Answer"
- "The [Term] Rule Nobody Talks About"
- "How [Term] Actually Works (And Why It Matters)"
- "The Truth About [Term] in 2026"
- "[Term] Explained: What Every Investor Should Know"
- "Why [Term] Changes Everything for Your Money"

Title rules — every option must:
- Be 45-65 characters long
- Promise a clear learning outcome
- Avoid scare tactics or urgency language (this is educational, not breaking news)
- Include the topic keyword naturally

Score each option 0-100:
  - educational_clarity (promise is obvious, outcome is clear): 0-40
  - search_intent_match (what someone would actually search for): 0-35
  - brand_fit (sounds like DriftWire326's voice): 0-25

Return ONLY valid JSON:
{{
  "options": [
    {{"title": "<title>", "score": <int>, "educational_clarity": <int>, "search_intent_match": <int>, "brand_fit": <int>}},
    {{"title": "<title>", "score": <int>, "educational_clarity": <int>, "search_intent_match": <int>, "brand_fit": <int>}},
    {{"title": "<title>", "score": <int>, "educational_clarity": <int>, "search_intent_match": <int>, "brand_fit": <int>}}
  ],
  "recommended": "<exact title string of highest scoring option>"
}}"""

# ─────────────────────────────────────────────────────────────────────────────
# 13. DESCRIPTION_PROMPT
# Input:  {title}, {script_summary}, {tags}, {video_type}, {date}
# Output: full YouTube description, SEO-optimised, under 5000 chars
# ─────────────────────────────────────────────────────────────────────────────

DESCRIPTION_PROMPT = """\
Write a complete YouTube description for a DriftWire326 video. Hard cap: 5000 characters.

TITLE: {title}
VIDEO TYPE: {video_type}
DATE: {date}
SCRIPT SUMMARY: {script_summary}
TAGS / KEYWORDS: {tags}

Write all sections in order:

HOOK PARAGRAPH (first 150 chars are critical — shown before "show more"):
2-3 punchy sentences that mirror the title's promise and include the key stat.
This is the most important section for SEO and click-through.

WHAT YOU WILL LEARN (bullet points):
• 3-5 bullet points covering the main takeaways of this video.
• Use the keywords from TAGS naturally.

TIMESTAMPS (main videos only — skip for Shorts):
00:00 Introduction
[Fill in based on script_summary — estimate realistic timestamps]

LINKS:
📊 Portfolio tracker (placeholder): [PORTFOLIO_LINK]
📰 Data sources: [FRED_LINK] | [YAHOO_FINANCE_LINK]
📧 Business: [CONTACT_EMAIL]

SOCIAL:
🐦 Twitter/X: @DriftWire326
📸 Instagram: @DriftWire326
💬 Discord: [DISCORD_LINK]

HASHTAGS (10-15 relevant tags from the provided list):
{tags}

CHANNEL CTA:
Subscribe to DriftWire326 for daily stock market news, financial education, and market \
analysis built for the next generation of investors.

DISCLAIMER (auto-append exactly as written):
This content is for informational and entertainment purposes only and does not constitute \
financial advice. Always consult a licensed financial advisor before making any investment \
decisions. Narration is AI-generated.

Return plain text — all sections in order, no extra commentary."""

# ─────────────────────────────────────────────────────────────────────────────
# 14. TAGS_PROMPT
# Input:  {topic}, {title}, {video_type}
# Output: JSON array of exactly 20 YouTube tags
# ─────────────────────────────────────────────────────────────────────────────

TAGS_PROMPT = """\
Generate exactly 20 YouTube tags for a DriftWire326 video.

TITLE: {title}
TOPIC: {topic}
VIDEO TYPE: {video_type}

Tag rules:
- Always include ALL of these channel tags: "finance", "stocks", "market news",
  "investing", "DriftWire326"
- Remaining 15 tags: mix of broad terms (stock market, Wall Street, S&P 500) and
  specific terms (topic keywords, named tickers, named data events)
- Each tag: 1-5 words, no hashtag symbols, plain text only
- Prioritise what people actually search on YouTube Finance
- Include at least one "how to" keyword variant (e.g., "how to invest", "how to read stocks")
- Include at least one trending news term matching the topic

Return ONLY a valid JSON array of exactly 20 strings — no markdown, no commentary:
["tag1", "tag2", "tag3", ..., "tag20"]"""

# ─────────────────────────────────────────────────────────────────────────────
# 15. PROMISE_MATCH_PROMPT
# Input:  {title}, {script_opening_150_words}
# Output: JSON {matched: bool, reason: str}
# Rule:   title claim must appear within the first ~15 seconds of script
# ─────────────────────────────────────────────────────────────────────────────

PROMISE_MATCH_PROMPT = """\
Check whether this video's script opening delivers on the promise made in its title.

TITLE: {title}

SCRIPT OPENING (first ~150 words / first ~15 seconds):
{script_opening_150_words}

Evaluation criteria:
1. Does the script opening directly address the specific claim or event in the title?
2. Is the anchor number or key fact from the title present in the first 15 seconds?
3. Would a viewer who clicked expecting the title's promise feel immediately satisfied?

Rule: the title's core claim must appear within the first 15 seconds.
If it doesn't, explain exactly what is missing and where it appears instead (if at all).

Return ONLY valid JSON — no markdown, no preamble:
{{
  "matched": true,
  "reason": "<one sentence — either confirming the match or stating precisely what is missing>"
}}"""

# ─────────────────────────────────────────────────────────────────────────────
# 16. SUNDAY_TOPIC_SELECTOR_PROMPT
# Input:  {theme}, {week_market_summary}, {available_topics_list}
# Output: best topic for this Sunday given market context
# ─────────────────────────────────────────────────────────────────────────────

SUNDAY_TOPIC_SELECTOR_PROMPT = """\
Select the best Sunday educational topic for DriftWire326 this week.

SUNDAY THEME: {theme}
THIS WEEK'S MARKET SUMMARY: {week_market_summary}
AVAILABLE TOPICS (not recently used):
{available_topics_list}

Your goal: pick the topic that keeps Sunday content contextually relevant to
what actually happened in markets this week, while fitting the theme.

Selection criteria:
1. Market relevance — does this topic connect naturally to this week's events?
2. Timeliness — is there a current news hook that makes it more compelling right now?
3. Audience fit — will Gen Z / Millennial investors find this genuinely useful?
4. Content richness — enough data, real examples, and angles for a 420-500 word script?

Return ONLY valid JSON:
{{
  "selected_topic": "<exact topic name from the available list>",
  "theme": "{theme}",
  "market_connection": "<one sentence linking this topic to this week's market events>",
  "content_angle": "<the specific angle or hook to lead the script with>",
  "key_stat_to_research": "<one data point worth looking up before scripting>",
  "confidence": 0.88
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# BACKWARD-COMPATIBLE TEMPLATE ALIASES
# Existing generators call .substitute() — these aliases preserve that API
# so modules keep working until individually upgraded to str.format().
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

Write a 420-500 word script using this EXACT structure:

[HOOK] — 5 seconds. One explosive opening stat. Start mid-action. No preamble.
[WHAT HAPPENED] — 40 seconds. Who, what, when, and which catalyst. Two+ data points.
[WHY IT MATTERS] — 50 seconds. Cause → ripple effects. Source every claim.
[NUMBERS DEEP DIVE] — 40 seconds. Three+ specific figures. Historical context.
[WHAT HAPPENS NEXT] — 25 seconds. Catalysts to watch. No price targets.
[CTA] — 15 seconds. Subscribe prompt + comment question.

Compliance rules:
- Include at least one of: "according to", "data shows", "reported", "as of today",
  "markets indicate", "analysts note", "figures show", "sources indicate", "market data suggests"
- Do NOT use: "you should buy", "guaranteed return", "this will go up", "best investment",
  "I recommend buying", "go all in", "this is a sure thing"
- Final line: "This content is for informational and entertainment purposes only and does not
  constitute financial advice. Always consult a licensed financial advisor before making any
  investment decisions. Narration is AI-generated."

Return plain text only.\
""")

SUNDAY_SCRIPT_PROMPT = Template("""\
You are writing the complete script for DriftWire326's Sunday educational video.

TOPIC: $topic
SUBTOPICS: $subtopics
KEY CONCEPTS: $key_concepts
CURRENT RELEVANCE: $current_relevance

Write a 420-500 word educational script:

[HOOK] — 10 seconds. A surprising fact or counter-intuitive truth.
[WHAT IS IT] — 60 seconds. Clear definition + one strong analogy.
[HOW IT WORKS] — 80 seconds. Step-by-step with real 2026 examples and data.
[WHY IT MATTERS TO YOU] — 50 seconds. Connection to everyday investing.
[KEY TAKEAWAY] — 20 seconds. One memorable sentence.
[CTA] — 15 seconds. Subscribe + comment question.

Rules:
- Credibility anchors required ("according to", "data shows", "as of today", etc.)
- No advisory language
- Final line: "This content is for informational and entertainment purposes only and does not
  constitute financial advice. Always consult a licensed financial advisor before making any
  investment decisions. Narration is AI-generated."

Return plain text script only.\
""")

TITLE_GENERATION_PROMPT = Template("""\
Generate 10 YouTube title options for a DriftWire326 finance video.

Topic: $topic
Key stat: $key_stat
Video type: $video_type
Ticker: $specific_ticker

Rules: 40-65 chars, include numbers, curiosity or urgency, no misleading claims.
Power words: Crashed, Surged, Revealed, Warning, Breaking, Record, Shocked.

Return exactly: ["title1", "title2", ..., "title10"]\
""")

COMPLIANCE_REVIEW_PROMPT = Template("""\
Review this YouTube finance script for compliance issues.

SCRIPT:
$script

Check for:
1. Financial advice language (buy/sell recommendations)
2. Return guarantees or price targets stated as fact
3. Unsubstantiated claims without a cited source
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
