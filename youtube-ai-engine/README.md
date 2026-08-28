# 🤖 YouTube AI Engine

> A **self-evolving, self-healing** autonomous video production system that produces and publishes YouTube videos daily — and gets measurably better every single day without human intervention.

---

## What it does

Every day, fully autonomously, the engine:

1. **Hunts trends** across 8 sources (YouTube, Reddit, Google Trends, NewsAPI, Wikipedia, Hacker News, Exploding Topics)
2. **Decides** the best topic, style, and length using its memory of what worked before
3. **Writes** a viral 7-section script with Claude
4. **Produces** voiceover, AI imagery, music, and a fully edited video
5. **Generates** 3 CTR-optimised thumbnail variants
6. **Publishes** to YouTube with SEO-optimised metadata at the best time to post
7. **Learns** from the analytics of every video and rewrites its own config to improve

Once a week it **reviews and rewrites its own code** with Claude, runs its test suite, and commits the improvements to git.

When anything crashes, the **self-healer** captures the traceback, asks Claude for a fix, validates it with AST parsing, applies it, and retries — all on its own.

---

## Architecture

```
youtube-ai-engine/
├── core/                      The brain & survival systems
│   ├── memory.py              SQLite performance DB (the long-term memory)
│   ├── brain.py               Central decision engine
│   ├── self_healer.py         Autonomous bug fixer (Claude + AST patching)
│   └── scheduler.py           Daily / weekly job orchestrator
│
├── intelligence/             Knows what works
│   ├── trend_hunter.py        8-source trend aggregator
│   ├── competitor_spy.py      Channel analysis → winning formula DB
│   ├── audience_analyzer.py   Comment mining → audience desire map
│   ├── algorithm_decoder.py   Upload-pattern → performance correlations
│   └── performance_scorer.py  Universal 1-100 output scorer
│
├── production/               Makes the video
│   ├── script_writer.py       7-section viral scripts (Claude)
│   ├── voice_engine.py        ElevenLabs→OpenAI→edge-tts→espeak chain
│   ├── visual_engine.py       Replicate→Stability→Pexels→PIL chain
│   ├── editor.py              FFmpeg assembly + Ken Burns + colour grade
│   ├── thumbnail_ai.py        3-variant CTR-optimised thumbnails
│   └── caption_engine.py      Whisper + script-approximation captions
│
├── publishing/              Ships it
│   ├── youtube_publisher.py   Resumable YouTube Data API v3 upload
│   ├── seo_optimizer.py       Title / description / tags optimiser
│   └── scheduler_optimizer.py Best-time-to-post calculator
│
├── evolution/              Gets better over time
│   ├── code_auditor.py        Weekly Claude code review → git commit
│   ├── feedback_loop.py       Analytics → lesson → config auto-update
│   ├── ab_tester.py           Thumbnail A/B test with auto-winner
│   └── version_manager.py     Git snapshots + milestone tagging
│
├── config/                Tunable knobs (Brain auto-updates these)
│   ├── master_config.yaml
│   ├── style_profiles.yaml    4 visual style DNA profiles
│   ├── channel_persona.yaml   Channel identity & voice
│   └── api_keys.env.example
│
├── tests/auto_test.py     28-test self-verification suite
├── main.py                Single CLI entry point
├── setup.sh               One-command install
└── requirements.txt
```

---

## The compounding improvement loop

```
   ┌──────────────────────────────────────────────────────┐
   │                                                        │
   ▼                                                        │
Trend → Topic → Script → Video → Thumbnail → Publish        │
                                                 │          │
                                                 ▼          │
                                          YouTube Analytics │
                                                 │          │
                                                 ▼          │
                                       Lessons extracted    │
                                                 │          │
                                                 ▼          │
                                    Config auto-updated ────┘
                                  (better topic/style/length
                                   next time → repeat)
```

Plus a slower weekly loop where the engine **rewrites its own source code**.

---

## Quick start

```bash
# 1. Install everything
bash setup.sh

# 2. Add your API keys
nano config/api_keys.env       # ANTHROPIC_API_KEY is the only hard requirement

# 3. Authenticate YouTube (uses OAuth token, never a password)
cd ../youtube_automation && python first_time_setup.py && cd -

# 4. Verify the install
python main.py --test          # → 28 tests should pass

# 5. Make a video right now
python main.py --all

# 6. Or run forever on a schedule
python main.py --schedule
```

> **Graceful degradation:** every external service has a fallback chain. With *only* an Anthropic key + ffmpeg + espeak-ng, the engine still produces complete videos end-to-end. Premium keys (ElevenLabs, Replicate, Pexels…) just make the output nicer.

---

## CLI reference

| Command | What it does |
|---|---|
| `python main.py --all` | Run the full daily pipeline now |
| `python main.py --dry-run` | Pick a topic only, no media production |
| `python main.py --schedule` | Start the continuous daemon |
| `python main.py --status` | Print channel performance summary |
| `python main.py --test` | Run the test suite |
| `python main.py --phase trend` | Trend hunting only |
| `python main.py --phase script --topic "..."` | Script for a specific topic |
| `python main.py --phase voice` | Voice generation only |
| `python main.py --phase visual` | Image generation only |
| `python main.py --phase assemble` | Full video build |
| `python main.py --phase thumbnail` | Thumbnails only |
| `python main.py --phase audit` | Run the weekly code self-improvement now |
| `python main.py --phase feedback` | Run the analytics feedback loop now |
| `python main.py --phase competitor` | Run competitor analysis now |
| `--style tech-dark\|vlog-warm\|news-clean\|motivation-epic` | Override visual style |
| `--length <seconds>` | Override video length |

---

## Daily schedule (UTC)

| Time | Job |
|---|---|
| 02:00 | Full daily production pipeline |
| 10:00 | Competitor analysis snapshot |
| 22:00 | Analytics feedback checkpoints (24h/72h/7d/28d) |
| Sun 01:00 | Weekly code self-improvement audit |
| every 6h | Git version snapshot |

---

## Security

- Authentication is **OAuth token-based only**. The engine never stores or uses an account password.
- `config/api_keys.env` is gitignored. Only `api_keys.env.example` is committed.
- All generated content is advertiser-safe by design (see `channel_persona.yaml` → `voice.avoid`).

---

## Requirements

- Python 3.11+
- ffmpeg
- espeak-ng (auto-installed by `setup.sh`)
- An `ANTHROPIC_API_KEY` (everything else is optional)
