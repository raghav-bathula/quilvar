# Prediction Market Agent — Session Context
> Use this file as starting context in Claude Code or Cowork.
> Drop it in your project root as CONTEXT.md

---

## What We Are Building

A **prediction market AI agent** that:
- Monitors RSS news feeds and SEC filings for market-moving signals
- Cross-references signals against Polymarket and Kalshi prediction markets
- Detects probability gaps between news-implied outcomes and market-implied odds
- Reasons through signals using multiple LLMs + expert investment thesis lenses
- Delivers enriched alerts via Telegram and StockTwits
- Validates signal quality over time via price + options outcome tracking
- Builds audience via post-mortem content (never predictions — only verified outcomes)

**Core principle:** Advisory only. No auto-execution. Ever.
**Shadow period:** 30 days of signal logging before trusting any output.

---

## Files Already Built

| File | Purpose | Status |
|---|---|---|
| `news_watcher.py` | RSS ingestion, signal scoring, Polymarket/Kalshi scan, Supabase storage, Telegram + StockTwits notifications | ✅ Done |
| `signal_validator.py` | Price outcome tracking (24h/7d/30d), options data (IV, put/call ratio), calibration report | ✅ Done |
| `claude_reasoning.py` | Multi-model ensemble (Claude + GPT + Gemini), 7 expert thesis lenses, gap detection, enriched alerts | ✅ Done |
| `.github/workflows/agent.yml` | GitHub Actions: scan every 15min, validate daily 4pm EST, report every Sunday | ✅ Done |

---

## Architecture — 6 Phases

### Phase 1 — Data Ingestion (Weeks 1–2)
**File:** `news_watcher.py`

**RSS Feeds — 3 Tiers:**
- Tier 1 (source): SEC EDGAR 8-K, GlobeNewswire M&A, Yahoo Finance
- Tier 2 (AI/tech): TechCrunch, HackerNews, The Verge, Ars Technica
- Tier 3 (market): Reuters Business, CNBC, Bloomberg Markets

**Signal Scoring (0–10):**
- 4 themes: `ai_product_launch`, `ai_pivot`, `competitor_threat`, `macro_signal`
- Watchlist: Adobe, Figma, Wix, GoDaddy, Salesforce, Snowflake, Palantir + others
- Score = (matched themes × 2) + watchlist hits, capped at 10
- Alert threshold: score ≥ 6
- DB threshold: score ≥ 4

**Prediction Markets:**
- Polymarket: `https://gamma-api.polymarket.com/markets?active=true&limit=50&order=volume&ascending=false`
- Kalshi: `https://trading-api.kalshi.com/trade-api/v2/markets?limit=50&status=open`

**Storage:** Supabase `alerts` table
**Schedule:** GitHub Actions, every 15 minutes, public repo (2000 free min/month)

---

### Phase 2 — Signal Validation (Weeks 3–5)
**File:** `signal_validator.py`

**Price tracking via Yahoo Finance (free, no key):**
- 24h: ≥2% move = validated
- 7d: ≥4% move = validated
- 30d: ≥8% move = validated

**Options data per ticker:**
- IV (ATM call + put)
- Put/call volume ratio
- Total options volume
- Unusual Whales RSS for flow confirmation

**Calibration report (Sundays via Telegram):**
- Precision by source, theme, score level
- Target: precision > 40% before acting on anything
- `outcome` column filled automatically, used for precedent matching

---

### Phase 3 — Claude Reasoning Engine (Weeks 5–8)
**File:** `claude_reasoning.py`
**Activate:** Only after 30 days of validation data in Supabase

**Multi-Model Ensemble:**
- Claude Haiku (free) → upgrade to Sonnet 4.6 at month 2
- GPT-4o mini (~$0.25/month)
- Gemini 1.5 Flash (~$0.12/month)
- Disagreement > 20pts = flag uncertain, reduce confidence

**7 Expert Thesis Lenses (all run via Claude API):**
| Expert | Philosophy |
|---|---|
| Warren Buffett | Moat durability, 10-year earnings lens |
| Charlie Munger | Inversion, second-order consequences |
| George Soros | Reflexivity, self-fulfilling feedback loops |
| Michael Burry | Contrarian, what market is missing |
| Stan Druckenmiller | Asymmetry, timing + position sizing |
| Cathie Wood | Disruption TAM, adoption curve speed |
| Peter Lynch | Common sense, observable ground truth |

**Gap Detection:**
- Combined = Ensemble (60%) + Expert avg (40%)
- Gap = combined − market implied probability
- Alert if gap > 15 percentage points
- Strong > 25pts | Moderate 15–25pts

**Precedent Memory:**
- Queries Supabase for past validated signals by theme
- Feeds historical outcomes into each reasoning prompt
- Gets smarter as data accumulates

---

### Phase 4 — Missing Layers (Month 2)
**To build:**
- `sentiment_watcher.py` — Reddit RSS, StockTwits stream, X cashtags
- `calendar_watcher.py` — Yahoo Finance earnings, FOMC, CPI/GDP dates
- Deduplication engine — same story on 3+ sources = amplifier not 3 signals
- `sizing_engine.py` — gap strength → suggested position size (Kelly approximation)

---

### Phase 5 — Content Pipeline (Month 2–3)
**To build:**
- `chart_generator.py` — mplfinance price chart with signal timestamp marked
- `card_generator.py` — Pillow infographic card per validated signal
- `content_pipeline.py` — Claude writes 60s post-mortem script
- `poster.py` — distributes to Instagram, TikTok, YouTube Shorts

**Content strategy:** Post-mortems only, never predictions.
Format: "Our agent flagged this on [date]. Here's what happened."
Timestamps are the credibility. 30-day shadow data is the content backlog.

---

### Phase 6 — Repo & Secrets Strategy (Month 2–3)

**Now:** Single public repo — free Actions, nothing valuable to protect yet.

**Month 2 split:**
- `prediction-agent-public/` — infrastructure skeleton (open source)
- `prediction-agent-private/` — calibrated weights, validated data

**Valuable assets that emerge:**
- `signal_weights.json` — source + theme weights from calibration
- `expert_weights.json` — which expert lenses were most accurate
- `keyword_model.pkl` — trained signal scorer
- `threshold_config.json` — tuned gap thresholds

**Weights stored in Supabase `signal_config` table** — loaded at runtime, never committed to public repo.

---

## Infrastructure Stack

| Component | Tool | Cost |
|---|---|---|
| Scheduling | GitHub Actions (public repo) | $0 |
| Database | Supabase | $0 |
| Primary LLM | Claude Haiku → Sonnet 4.6 | $0 → ~$4/mo |
| Ensemble LLM 1 | GPT-4o mini | ~$0.25/mo |
| Ensemble LLM 2 | Gemini 1.5 Flash | ~$0.12/mo |
| Alerts | Telegram Bot API | $0 |
| Social | StockTwits API | $0 (200 posts/day) |
| Validation | Yahoo Finance API | $0 |
| Options flow | Unusual Whales RSS | $0 |
| Secret backup | Bitwarden | $0 |
| **Total** | | **$0 shadow / ~$4.50 month 2+** |

---

## Secrets Required

### Add to GitHub: repo → Settings → Secrets and variables → Actions

| Secret | Source | When |
|---|---|---|
| `ANTHROPIC_API_KEY` | anthropic.com → API Keys (free tier) | Now |
| `SUPABASE_URL` | Supabase → Project Settings → API | Now |
| `SUPABASE_KEY` | Supabase → Project Settings → API (anon key) | Now |
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram → /newbot | Now |
| `TELEGRAM_CHAT_ID` | api.telegram.org/bot{TOKEN}/getUpdates | Now |
| `STOCKTWITS_ACCESS_TOKEN` | stocktwits.com/developers | Week 3 |
| `OPENAI_API_KEY` | platform.openai.com | Week 6 |
| `GOOGLE_API_KEY` | console.cloud.google.com | Week 6 |

### Local setup (order matters):
```bash
echo ".env" >> .gitignore          # Step 1 — before creating .env
echo "seen_items.json" >> .gitignore
echo "alerts_log.jsonl" >> .gitignore
git add .gitignore && git commit -m "gitignore secrets"
touch .env                          # Step 2 — now safe to create
pip install python-dotenv           # Step 3
```

### Secret backup: 3 layers
1. `.env` on laptop — local development
2. Bitwarden (free) — survives crash, syncs everywhere
3. GitHub Secrets — production runs regardless of machine state

---

## Supabase Schema

```sql
-- Run in Supabase SQL editor

CREATE TABLE alerts (
  id              bigserial PRIMARY KEY,
  scan_time       timestamptz NOT NULL,
  source          text,
  tier            int,
  title           text,
  link            text,
  themes          text[],
  watchlist       text[],
  tickers         text[],
  score           int,
  shadow_mode     boolean DEFAULT true,
  gap_flag        boolean,
  market          text,
  platform        text,
  market_url      text,
  outcome         text,
  -- Validation columns
  outcome_1d      text,
  outcome_7d      text,
  outcome_30d     text,
  validated_1d    boolean,
  validated_7d    boolean,
  validated_30d   boolean,
  price_data_1d   jsonb,
  price_data_7d   jsonb,
  price_data_30d  jsonb,
  -- Reasoning columns
  reasoned_at     timestamptz,
  ensemble_prob   float,
  expert_avg_prob float,
  gap             float,
  gap_strength    text,
  should_alert    boolean,
  reasoning_json  jsonb,
  created_at      timestamptz DEFAULT now()
);

CREATE TABLE signal_config (
  key         text PRIMARY KEY,
  value       jsonb,
  updated_at  timestamptz DEFAULT now(),
  notes       text
);

-- Indexes
CREATE INDEX idx_scan_time   ON alerts(scan_time);
CREATE INDEX idx_score       ON alerts(score);
CREATE INDEX idx_outcome_1d  ON alerts(outcome_1d);
CREATE INDEX idx_reasoned_at ON alerts(reasoned_at);
CREATE INDEX idx_gap         ON alerts(gap);
```

---

## Key Design Decisions Made

**Why SEC EDGAR as Tier 1:**
The Allbirds/NewBird AI pivot was catchable via 8-K before any media coverage.
Press releases are machine-readable and hit EDGAR before CNBC.

**Why keyword scoring before Claude reasoning:**
Claude reasoning costs money and needs precedent data to be useful.
Keyword scoring is free and filters noise before reasoning runs.
Don't activate claude_reasoning.py until week 5+ when validation data exists.

**Why 30-day shadow period:**
Not optional caution — it literally builds the precedent database.
Without validated outcomes, reasoning engine has no ground truth.
Also builds content backlog of verifiable post-mortems.

**Why multi-model ensemble:**
Different models have different priors. Disagreement between them is itself a signal.
GPT-4o mini + Gemini Flash cost almost nothing (~$0.37/month combined).
Model consensus = higher confidence. Disagreement = reduce position size.

**Why expert lenses:**
Same signal, different priors.
Buffett asks: is the moat permanently broken?
Burry asks: what is the market missing?
Soros asks: will the selloff become self-fulfilling?
Spread between expert estimates = your uncertainty band.

**Why post-mortems not predictions for content:**
Verifiable timestamps build credibility.
30-day shadow data = content backlog with proof.
"We flagged this on [date], here's what happened" is differentiated.

**Why StockTwits over Twitter/X:**
Twitter/X API now costs $100/month to post.
StockTwits is free, built for financial content, cashtag-native.

**Signal quality rating at current stage: 5/10**
Keyword matching has high false positive rate.
Score model is mechanical not intelligent.
No feedback loop yet — outcome column exists but nothing fills it.
Rating improves after 30 days of validation data.

---

## Two Real-World Case Studies Used to Design This

### Case 1: Anthropic Design Tool → Adobe/Figma Down
- **Signal chain:** The Information scoop (April 14) → RSS Tier 2 → themes: ai_product_launch + competitor_threat → tickers: ADBE, FIGMA, WIX → Polymarket opened release date contract after news
- **What agent would have caught:** News at T+0, Polymarket contract underpriced at 60% vs base rate 80%+ (Anthropic 2-week release cadence)
- **Outcome:** Adobe/Figma/Wix down 2-4% same day
- **Signal type:** Real fundamental threat → underpriced downside on incumbents

### Case 2: Allbirds → NewBird AI +582%
- **Signal chain:** SEC 8-K press release → Tier 1 → themes: ai_pivot → ticker: BIRD → Kalshi/Polymarket gap on "BIRD above $10 in 30 days"
- **What agent would have caught:** 8-K at source before media, Kalshi likely implied 60-70% when base rate from Long Blockchain precedent suggests 15-25%
- **Outcome:** +582% day 1, classic euphoria pivot pattern
- **Signal type:** Euphoria shell pivot → overpriced continuation contracts → mean reversion short thesis
- **Precedent:** Long Island Iced Tea → Long Blockchain 2017 (+380% then delisted)

### Pattern encoded in agent:
| Signal Type | Example | Action |
|---|---|---|
| Real fundamental threat | Anthropic tool → Adobe down | Flag underpriced downside on incumbents |
| Euphoria shell pivot | Allbirds → NewBird AI | Flag overpriced continuation contracts |

---

## Immediate Action List

```
Week 1 — Infrastructure
  □ Create GitHub repo (public)
  □ Add .gitignore (secrets first)
  □ Create Supabase project, run schema SQL above
  □ Get Anthropic free API key (anthropic.com)
  □ Set up Telegram bot (@BotFather → /newbot)
  □ Add 5 GitHub Secrets
  □ Push all 4 files
  □ Watch first Actions run — confirm Supabase rows appear

Week 2 — Confirm data flowing
  □ Check alerts table has rows
  □ Check Telegram is receiving alerts
  □ Verify deduplication working (no repeat alerts)

Week 3 — Notifications live
  □ Add STOCKTWITS_ACCESS_TOKEN secret
  □ Confirm cashtag posts appearing on StockTwits

Week 3–4 — Validation running
  □ signal_validator.py running in GitHub Actions daily
  □ outcome_1d column starting to fill

Week 5 — First calibration checkpoint
  □ Run: python signal_validator.py --report
  □ Is precision > 40%? If yes, proceed to reasoning.
  □ If no, adjust keyword weights, re-run for 2 more weeks.

Week 5–6 — Reasoning live
  □ Add OPENAI_API_KEY + GOOGLE_API_KEY secrets
  □ Activate claude_reasoning.py in agent.yml
  □ Start on Claude Haiku free tier
  □ Upgrade to Sonnet 4.6 when ready to pay
```

---

## Python Dependencies

```bash
pip install feedparser httpx python-dotenv
pip install yfinance pandas                    # for signal_validator.py
pip install anthropic openai google-generativeai  # for claude_reasoning.py
```

---

## Running Locally

```bash
# Test without network calls
python news_watcher.py --test
python signal_validator.py --schema    # prints Supabase SQL
python claude_reasoning.py --test

# Single real scan
python news_watcher.py

# Validate outcomes
python signal_validator.py --horizon 1d
python signal_validator.py --report

# Reason on pending signals
python claude_reasoning.py --pending

# Continuous watch (local dev)
python news_watcher.py --watch --interval 300
```

---

## What to Build Next (in order)

1. **sentiment_watcher.py** — Reddit RSS + StockTwits stream
2. **calendar_watcher.py** — earnings dates, FOMC, CPI
3. **Deduplication** — same story on 3+ sources = amplify not duplicate
4. **chart_generator.py** — mplfinance chart from validated signals
5. **card_generator.py** — Pillow signal card for social
6. **content_pipeline.py** — Claude post-mortem script writer
7. **sizing_engine.py** — gap → position size (Kelly, advisory only)

---

*Last updated: Session 1 — April 2026*
*Status: Phase 1 ready to deploy. Phases 2–3 built, awaiting data.*
*Next session: Start with `python news_watcher.py --test` to confirm setup.*
