"""
Daily video content builder — 18-40 US audience, optimised for YouTube monetisation.

Strategy
--------
• High-CPM categories first: Finance, Career, Tech (avg $15-40 CPM for US viewers)
• 7 rotating categories, one per day of the week
• Titles use proven YouTube hook patterns (numbers, questions, "you're doing X wrong")
• Tags blend broad search + specific long-tail for discoverability
• US-specific context throughout (401k, IRS, FICO score, US cities, USD)
"""

import datetime

# ---------------------------------------------------------------------------
# Topic library — US-focused, 7 categories
# ---------------------------------------------------------------------------

TOPIC_LIBRARY = {
    # ── Monday — MONEY (CPM $20-40 for US finance audience) ─────────────────
    "Money": [
        {
            "title": "5 Money Mistakes Americans in Their 20s & 30s Regret Most",
            "hook": "The average American carries $90k in debt. These 5 habits put them there.",
            "bullets": [
                "Skipping employer 401(k) match — that's free money you're leaving behind",
                "Lifestyle creep: every raise gets spent before it's saved",
                "Only paying minimums on credit cards costing 22-29% APR",
                "No emergency fund — one car repair wipes out months of progress",
                "Treating investments as something to start 'later' (the $300k mistake)",
            ],
            "takeaway": "Contribute at least enough to get your full 401(k) match today — it's an instant 50-100% return.",
            "tags": ["personalfinance", "money", "401k", "savingmoney", "financetips",
                     "debtfree", "investing101", "americanfinance"],
        },
        {
            "title": "How to Build a $1,000/Month Side Hustle in 90 Days",
            "hook": "The gig economy pays Americans $1.2 trillion a year. Here's your cut.",
            "bullets": [
                "Service businesses beat products — cash comes in week one, not month six",
                "Top earners on Fiverr, Upwork, and Toptal bill $50-$200/hr",
                "Local services (pressure washing, lawn care, cleaning) require zero online presence",
                "Content creation: YouTube AdSense + affiliates can clear $1k/month in 90 days",
                "Flip items on eBay, Facebook Marketplace, and Amazon — median profit $600/month",
            ],
            "takeaway": "Pick one. Start today. Your first $100 outside a paycheck changes your psychology forever.",
            "tags": ["sidehustle", "makemoneyonline", "extraincome", "freelance",
                     "entrepreneurship", "passiveincome", "gig", "workfromhome"],
        },
        {
            "title": "Index Funds Explained: How Average Americans Build Real Wealth",
            "hook": "Warren Buffett bet $1M that index funds beat hedge funds. He won.",
            "bullets": [
                "S&P 500 index funds have averaged 10.5% annually over 50 years",
                "The average actively managed fund underperforms its index after fees",
                "Total market ETFs (VTI, FSKAX) own every US company for 0.03% per year",
                "$300/month from age 25 = $1.1M by retirement at historical returns",
                "Tax-advantaged: max your Roth IRA ($7,000/yr) before taxable accounts",
            ],
            "takeaway": "Open a Fidelity or Vanguard account this week. Set a $50/month auto-invest. Done.",
            "tags": ["indexfunds", "investing", "sp500", "stockmarket", "rothira",
                     "wealthbuilding", "vanguard", "financialfreedom"],
        },
        {
            "title": "Your Credit Score Is Costing You Thousands — Fix It Now",
            "hook": "A 100-point difference in your FICO score can cost $50,000 on a mortgage.",
            "bullets": [
                "Payment history is 35% of your score — one missed payment tanks it for 7 years",
                "Credit utilisation: keep all cards below 10% of their limit for top scores",
                "Average American with 800+ score has cards they barely use — by design",
                "Disputing errors (found in 1 in 5 reports) can raise your score 25+ points",
                "Becoming an authorised user on a long-standing account boosts score fast",
            ],
            "takeaway": "Pull your free report at AnnualCreditReport.com — dispute one error this week.",
            "tags": ["creditscore", "fico", "creditrepair", "personalfinance",
                     "homebuying", "creditcard", "money", "debt"],
        },
    ],

    # ── Tuesday — MINDSET (CPM $8-18) ───────────────────────────────────────
    "Mindset": [
        {
            "title": "The Habit Loop: Why You Can't Stop Doing Things That Are Bad for You",
            "hook": "You're not weak. You're just using willpower where a system should go.",
            "bullets": [
                "Cue → Routine → Reward: your brain automates behaviour to save energy",
                "Habits live in the basal ganglia — willpower lives in the prefrontal cortex (depletes)",
                "Habit stacking: attach the new habit to an existing one you already do",
                "Identity change beats goal setting — 'I am someone who…' rewires faster",
                "The two-minute rule: any new habit must start small enough to do in 2 minutes",
            ],
            "takeaway": "Design your environment, not your willpower. Move the bad stuff out of sight.",
            "tags": ["habits", "selfimprovement", "psychology", "mindset", "motivation",
                     "atomichabits", "personaldevelopment", "discipline"],
        },
        {
            "title": "Stoicism: 5 Ancient Hacks for Modern American Stress",
            "hook": "Marcus Aurelius ran the Roman Empire and still had a morning journaling practice.",
            "bullets": [
                "The dichotomy of control: sort every problem into 'mine' vs 'not mine'",
                "Negative visualisation makes you more grateful and less anxious about loss",
                "Amor fati — love your fate, including the setbacks",
                "Premeditatio malorum: pre-plan the worst case so it doesn't paralyse you",
                "Daily journaling: 5 minutes in the morning, 5 minutes at night — measurable results",
            ],
            "takeaway": "Ask yourself every morning: 'What is in my control today?' Start there.",
            "tags": ["stoicism", "philosophy", "mindset", "mentalhealth", "stress",
                     "journaling", "selfimprovement", "anxiety"],
        },
        {
            "title": "Why Most Americans Give Up on Their Goals by February",
            "hook": "80% of New Year's resolutions fail by Valentine's Day. Here's the neuroscience.",
            "bullets": [
                "Goals without systems are just wishes — systems survive bad days",
                "The 'what the hell' effect: one slip triggers total abandonment",
                "Implementation intention: 'I will do X at Y time in Z place' triples success rate",
                "Accountability partners increase goal achievement by 65%",
                "Reward the process, not just the outcome — dopamine has to stay in the loop",
            ],
            "takeaway": "Never miss twice. One bad day is an accident. Two is the start of a new habit.",
            "tags": ["goals", "mindset", "habits", "motivation", "selfimprovement",
                     "discipline", "success", "newyear"],
        },
    ],

    # ── Wednesday — HEALTH (CPM $10-20) ─────────────────────────────────────
    "Health": [
        {
            "title": "Why 78% of Americans Are Overweight — And It's Not Willpower",
            "hook": "The food industry spent $14 billion lobbying in 20 years. Your cravings aren't random.",
            "bullets": [
                "Ultra-processed food is engineered to bypass your fullness signals",
                "The 'bliss point' — perfect ratio of salt, sugar, fat — overrides natural appetite",
                "Seed oils (in nearly everything processed) alter fat cell structure",
                "GLP-1 hormones from whole food naturally signal fullness — UPF blocks this",
                "A simple swap: replace one ultra-processed meal per day with whole food",
            ],
            "takeaway": "Read ingredients, not calories. If you can't pronounce it, eat less of it.",
            "tags": ["health", "nutrition", "weightloss", "diet", "americanhealth",
                     "food", "fitness", "obesity"],
        },
        {
            "title": "Zone 2 Training: The Workout That Changes Everything",
            "hook": "The world's top performers exercise at an intensity most Americans think is 'too easy'.",
            "bullets": [
                "Zone 2 = 60-70% max heart rate — you can hold a full conversation",
                "Builds mitochondrial density: more energy at the cellular level",
                "Burns fat as primary fuel and preserves muscle mass — unlike HIIT",
                "Reduces all-cause mortality risk by 35% with just 150 min/week",
                "Works as walking, cycling, swimming, or a light jog — free to start",
            ],
            "takeaway": "Walk briskly for 45 minutes four times a week. That's all. Track your resting HR drop.",
            "tags": ["fitness", "zone2", "cardio", "health", "workout",
                     "longevity", "exercise", "training"],
        },
        {
            "title": "Sleep Deprivation: America's Most Ignored Health Crisis",
            "hook": "One in three Americans gets less than 7 hours of sleep. The cost? Staggering.",
            "bullets": [
                "Sleep deprivation is economically equivalent to $411 billion in lost US productivity annually",
                "Under 6 hours of sleep impairs cognition as severely as being legally drunk",
                "Chronic poor sleep raises type 2 diabetes risk by 48% and heart disease risk by 67%",
                "Blue light from screens delays melatonin release by up to 3 hours",
                "Consistent wake time — even on weekends — is the single highest-leverage sleep fix",
            ],
            "takeaway": "Set a 'phone down' alarm 60 minutes before bed. Dim your lights. Same time every morning.",
            "tags": ["sleep", "health", "wellness", "biohacking", "mentalhealth",
                     "productivity", "recovery", "circadianrhythm"],
        },
    ],

    # ── Thursday — TECH (CPM $15-35) ────────────────────────────────────────
    "Tech": [
        {
            "title": "AI Is Replacing Jobs — Here's Which Ones Are Safe",
            "hook": "ChatGPT passed the bar exam. What does that mean for your career?",
            "bullets": [
                "Roles most at risk: data entry, basic writing, paralegal research, customer service",
                "Roles safest: physical trades, creative direction, complex human judgment",
                "AI as amplifier: a plumber with AI scheduling earns 3x a plumber without it",
                "Prompt engineering is the most valuable skill no university teaches yet",
                "The 'AI-native' generation entering the workforce in 2026 will outperform peers",
            ],
            "takeaway": "Learn to use 3 AI tools deeply. You won't be replaced by AI — you'll be replaced by someone using it.",
            "tags": ["AI", "artificialintelligence", "chatgpt", "jobs", "technology",
                     "futureofwork", "career", "automation"],
        },
        {
            "title": "Your Phone Is Designed to Addict You — Here's the Proof",
            "hook": "The ex-president of Facebook said: 'We exploited a vulnerability in human psychology.'",
            "bullets": [
                "Variable reward schedules (pull-to-refresh) mimic slot machine mechanics",
                "Social validation loops (likes) trigger the same dopamine pathways as gambling",
                "Average American checks their phone 96 times per day — once every 10 minutes",
                "Notification suppression apps show 40% reduction in screen time within a week",
                "The attention economy: your focus is the product. Advertisers are the customer.",
            ],
            "takeaway": "Turn off every non-essential notification today. It takes 3 minutes and changes everything.",
            "tags": ["smartphone", "socialmedia", "digitalwellness", "screentime",
                     "focus", "tech", "addiction", "productivity"],
        },
        {
            "title": "5 Tech Skills That Will Pay $100k+ in the US in 2026",
            "hook": "You don't need a CS degree. You need the right skill and six months.",
            "bullets": [
                "Prompt engineering / AI workflow design — companies paying $90-150k, no degree required",
                "Cybersecurity — 3.5 million unfilled roles globally, median US salary $120k",
                "Data analytics (SQL + Python + Tableau) — entry-level $75k, no CS degree needed",
                "Cloud infrastructure (AWS/Azure certs) — cert + 6 months experience = $95k+ roles",
                "UX research — the human side of tech, $80-130k, background in any field accepted",
            ],
            "takeaway": "Pick one skill. Google 'free [skill] course'. Start the first module today.",
            "tags": ["tech", "programming", "career", "highpayingjobs", "remotework",
                     "AI", "cybersecurity", "datascience"],
        },
    ],

    # ── Friday — CAREER (CPM $12-25) ────────────────────────────────────────
    "Career": [
        {
            "title": "How to Get Promoted in 12 Months (Without Kissing Up)",
            "hook": "Promotions don't go to the hardest workers. They go to the most strategically visible ones.",
            "bullets": [
                "Do your job flawlessly + solve one problem your boss didn't ask you to fix",
                "Manage up: send a weekly 5-bullet email — what's done, what's next, what's blocked",
                "Build internal brand in ONE domain — the go-to expert gets promoted before the generalist",
                "Request the promotion conversation 6 months early: 'What does promotion-ready look like?'",
                "Document every win. At review time, your boss needs the ammunition to fight for you.",
            ],
            "takeaway": "Ask your manager this week: 'What problem, if I solved it, would most impress you?'",
            "tags": ["career", "promotion", "work", "success", "corporatelife",
                     "leadership", "jobadvice", "careeradvice"],
        },
        {
            "title": "Salary Negotiation: The Script That Gets Americans More Money",
            "hook": "Not negotiating your starting salary costs the average American $650,000 over a career.",
            "bullets": [
                "85% of hiring managers have flexibility in the first offer — most people never ask",
                "Never name a number first. Ask: 'What's the budgeted range for this role?'",
                "Counter 15-20% above their offer — you'll meet in the middle at what you actually want",
                "Negotiate the full package: bonus, equity, remote days, signing bonus, PTO",
                "The silence rule: state your number, then stop talking. Let them respond.",
            ],
            "takeaway": "Say exactly this: 'I'm excited about this role. Based on my research and experience, I was thinking $[X]. Is there flexibility?'",
            "tags": ["salarynegotiation", "salary", "career", "money", "jobsearch",
                     "HR", "negotiation", "careeradvice"],
        },
        {
            "title": "LinkedIn Is a Goldmine — Most Americans Use It Wrong",
            "hook": "Recruiters send 11,000+ InMails per minute on LinkedIn. Most get ignored. Here's why yours will get read.",
            "bullets": [
                "Your headline should describe outcomes you deliver, not your job title",
                "Posts with personal stories get 3-5x more reach than company news shares",
                "The 'give first' method: comment genuinely on 5 posts before posting your own",
                "Open to Work banner increases recruiter contact by 40% — use it without shame",
                "First 3 lines of your About section get cut off — put your best pitch there",
            ],
            "takeaway": "Rewrite your LinkedIn headline today. Lead with value, not your title.",
            "tags": ["linkedin", "jobsearch", "networking", "career", "personalbranding",
                     "recruitment", "jobhunting", "careeradvice"],
        },
    ],

    # ── Saturday — LIFESTYLE (CPM $7-15) ────────────────────────────────────
    "Lifestyle": [
        {
            "title": "How Americans Travel to Europe for Under $500 Round-Trip",
            "hook": "Business class travellers pay $5,000. Smart travellers pay $400. Same plane.",
            "bullets": [
                "Google Flights 'Explore' map shows cheapest destinations from your airport",
                "Error fares and flash sales: Scott's Cheap Flights free tier alerts save 40-90%",
                "Flying into secondary airports (e.g. Gatwick not Heathrow) saves $100-200 each way",
                "Travel credit card sign-up bonuses: 60,000-100,000 miles = 1-2 free round trips",
                "Shoulder season (Apr-May, Sep-Oct) is 25-40% cheaper than summer — and less crowded",
            ],
            "takeaway": "Download Google Flights. Set a price alert for your top 3 destinations right now.",
            "tags": ["travel", "budgettravel", "europe", "cheapflights", "travelhacks",
                     "vacation", "traveltips", "creditcardrewards"],
        },
        {
            "title": "The Morning Routine Backed by Science (Takes 20 Minutes)",
            "hook": "You don't need a 5am wake-up. You need a consistent first hour.",
            "bullets": [
                "Sunlight within 30 min of waking sets your cortisol curve for optimal energy all day",
                "Delay caffeine 90 minutes — drink it after adenosine clears, not while it's still high",
                "Move your body: 10 min walk or stretch changes your brain chemistry for hours",
                "Write 3 priorities the night before — wake up with a mission, not a blank slate",
                "Avoid email and social until your first important task is done",
            ],
            "takeaway": "Own your first 30 minutes before the world does. Design it once. Run it daily.",
            "tags": ["morningroutine", "productivity", "habits", "wellness", "lifestyle",
                     "selfimprovement", "routine", "motivation"],
        },
        {
            "title": "Minimalism: The Americans Who Own Less, Stress Less, and Spend Less",
            "hook": "The US has 3% of the world's children but buys 40% of the world's toys. Something's off.",
            "bullets": [
                "Average American household owns 300,000 items — most unused",
                "Clutter raises cortisol and reduces ability to focus and make decisions",
                "The one-in one-out rule: every new item means one old item leaves",
                "Unsubscribing from retail emails saves the average American $1,800/year",
                "Experiences over things: memories from experiences outlast satisfaction from products",
            ],
            "takeaway": "Do a 10-minute declutter today. Bag five things you haven't touched in a year.",
            "tags": ["minimalism", "declutter", "lifestyle", "mentalhealth", "simplyliving",
                     "consumerism", "stress", "money"],
        },
    ],

    # ── Sunday — SCIENCE & PSYCHOLOGY (CPM $10-20) ──────────────────────────
    "Science": [
        {
            "title": "The Science of Dopamine — Why You Can't Stop Scrolling",
            "hook": "Dopamine isn't the pleasure chemical. It's the wanting chemical. And social media exploits it perfectly.",
            "bullets": [
                "Dopamine spikes on anticipation, not arrival — that's why the next scroll always promises more",
                "Variable reward (sometimes a good post) creates stronger compulsion than consistent reward",
                "Dopamine fasting (24hrs off screens + novelty) resets baseline sensitivity",
                "Cold showers, exercise, and sunlight are proven dopamine-raising behaviours with no crash",
                "Deep work creates intrinsic dopamine — harder, but longer lasting than any notification",
            ],
            "takeaway": "Hard things give you clean dopamine. Easy things borrow from tomorrow's supply.",
            "tags": ["dopamine", "neuroscience", "brain", "science", "socialmedia",
                     "psychology", "focus", "mentalhealth"],
        },
        {
            "title": "Why You Remember Some Things Forever and Forget Others in Hours",
            "hook": "You've forgotten 90% of what you learned in school. Here's why — and how to stop it.",
            "bullets": [
                "The forgetting curve: without review, 50% of new info is lost within an hour",
                "Spaced repetition (Anki, RemNote) is the most evidence-based learning method known",
                "Emotional arousal tags memories for long-term storage — boredom doesn't",
                "Teaching what you learn (the Feynman technique) reveals gaps and locks it in",
                "Sleep after learning consolidates memory — pulling an all-nighter erases it",
            ],
            "takeaway": "Review new information at 1 hour, 1 day, 1 week, and 1 month. That's it.",
            "tags": ["memory", "learning", "science", "brain", "studytips",
                     "psychology", "education", "productivity"],
        },
        {
            "title": "Cognitive Biases Making Your Decisions Worse Every Day",
            "hook": "Your brain shortcuts your thinking 95% of the time. Here's what it's getting wrong.",
            "bullets": [
                "Confirmation bias: you notice information that agrees with you and dismiss what doesn't",
                "Sunk cost fallacy: staying in bad jobs, relationships, investments because you've 'already invested'",
                "The availability heuristic: plane crashes feel common; car crashes (far deadlier) feel normal",
                "Dunning-Kruger: the less we know, the more confident we feel — expertise breeds doubt",
                "IKEA effect: we value things more when we built them — watch for this in your own ideas",
            ],
            "takeaway": "Before a big decision, ask: 'What would I think if the opposite were true?'",
            "tags": ["psychology", "cognitivebias", "decisionmaking", "brain", "science",
                     "selfimprovement", "thinking", "mindset"],
        },
    ],
}

# Day-of-week → category (highest CPM days get top-earning categories)
WEEKDAY_CATEGORY = {
    0: "Money",      # Monday   — CPM $20-40
    1: "Tech",       # Tuesday  — CPM $15-35
    2: "Career",     # Wednesday — CPM $12-25
    3: "Health",     # Thursday  — CPM $10-20
    4: "Money",      # Friday   — second Money day (peak US browsing)
    5: "Lifestyle",  # Saturday
    6: "Science",    # Sunday
}

# Mindset rotates in every other week on Tuesday
MINDSET_SWAP_WEEKS = {1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23,
                      25, 27, 29, 31, 33, 35, 37, 39, 41, 43, 45, 47, 49, 51}


def build_daily_content() -> dict:
    today = datetime.date.today()
    weekday = today.weekday()
    week_num = today.isocalendar()[1]

    # Category selection
    category = WEEKDAY_CATEGORY[weekday]
    if weekday == 1 and week_num in MINDSET_SWAP_WEEKS:
        category = "Mindset"

    topics = TOPIC_LIBRARY[category]
    topic_data = topics[week_num % len(topics)]

    title = topic_data["title"]
    bullets_text = "\n".join(f"✅ {b}" for b in topic_data["bullets"])
    hashtags = (f"#DailyDrop #{''.join(category.split())} "
                + " ".join(f"#{t}" for t in topic_data["tags"][:8]))
    description = (
        f"{topic_data['hook']}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱ CHAPTERS\n"
        "00:00 Hook\n"
        "00:10 Today's category\n"
        "00:15 Key points breakdown\n"
        "01:45 Key takeaway + action step\n"
        "02:15 Subscribe & notification bell\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "In this video:\n"
        + bullets_text
        + f"\n\n💡 Takeaway: {topic_data['takeaway']}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📺 Daily Drop with Prasad — Learn Something New Every Day\n"
        "🔔 New video every day at 3 PM EST — subscribe & hit the bell\n"
        "👍 Like if this made you think differently\n"
        "💬 Drop your biggest takeaway in the comments\n"
        "📌 Share with someone who needs to hear this today\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "© Daily Drop with Prasad | prasad2t@gmail.com\n\n"
        + hashtags
    )

    base_tags = [
        "dailydrop", "learneveryday", "selfimprovement", "education",
        "millennials", "genz", "lifehacks", "knowledge", "usa",
    ]
    all_tags = list(dict.fromkeys(base_tags + topic_data["tags"]))  # deduplicated

    return {
        "date": today,
        "category": category,
        "title": title,
        "description": description,
        "tags": all_tags[:30],          # YouTube max = 30 tags
        "hook": topic_data["hook"],
        "bullets": topic_data["bullets"],
        "takeaway": topic_data["takeaway"],
        "topic": title,
    }
