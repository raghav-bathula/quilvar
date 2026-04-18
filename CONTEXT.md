# Prediction Market Agent — Session Context
> Drop this file in your project root as CONTEXT.md and load it at the start of each session.

---

## What We Are Building

A **prediction market AI agent** that:
- Monitors RSS news feeds (equities) and crypto-specific feeds for market-moving signals
- Semantically classifies signals using Claude Haiku (with dynamic fallback — no fixed ticker list)
- Cross-references signals against Polymarket and Kalshi prediction markets
- Detects probability gaps between news-implied outcomes and market-implied odds
- Reasons through signals using a multi-model ensemble + 7 expert investment lenses
- Delivers enriched alerts via Telegram
- Validates signal quality over time via price + options outcome tracking
- Builds audience via post-mortem content (never predictions — only verified outcomes)

**Core principle:** Advisory only. No auto-execution. Ever.
**Shadow period:** 30 days of signal logging before trusting any output.

---

## Files

| File | Purpose | Status |
|---|---|---|
| `news_watcher.py` | Equity RSS ingestion, Haiku semantic scoring, dynamic ticker detection, Polymarket/Kalshi scan, Supabase, Telegram | ✅ Done |
| `crypto_watcher.py` | Crypto RSS ingestion (CoinDesk, CoinTelegraph, Decrypt, The Block + 4 more), Haiku crypto classifier, asset extraction, Polymarket crypto scan | ✅ Done |
| `signal_validator.py` | Price outcome tracking (24h/7d/30d, date-based), options data, calibration report — writes outcome for wins AND losses | ✅ Done |
| `claude_reasoning.py` | Multi-model ensemble (Claude + GPT + Gemini), 7 expert lenses, gap detection, correct model labeling | ✅ Done |
| `.github/workflows/agent.yml` | GitHub Actions: scan + crypto-scan every 15min, validate daily, report weekly | ✅ Done |

---

## Architecture — 6 Phases

### Phase 1 — Data Ingestion
**Files:** `news_watcher.py`, `crypto_watcher.py`

#### Equity feeds — 3 tiers, 12 sources:
- Tier 1: SEC EDGAR 8-K (requires `User-Agent` header), GlobeNewswire M&A, Yahoo Finance
- Tier 2: TechCrunch, HackerNews, The Verge, Ars Technica, Wired
- Tier 3: MarketWatch, CNBC, Bloomberg Markets, WSJ Markets

#### Crypto feeds — 3 tiers, 8 sources:
- Tier 1: CoinDesk, CoinTelegraph, Decrypt
- Tier 2: The Block, Bitcoin Magazine, Blockworks
- Tier 3: Bloomberg Markets, CNBC (macro spillover)

#### Signal scoring pipeline (both watchers):
1. **Pre-filter** — broad vocabulary check (free, instant). Equity: 40+ terms including company names (anthropic, openai, claude), market events. Crypto: 30+ crypto-specific terms. Items matching nothing are dropped before any API call.
2. **Roundup filter** (crypto only) — drops aggregator articles ("Price predictions 4/17: BTC, ETH...") that inflate scores without being signals.
3. **Haiku classification** — title + description sent to `claude-haiku-4-5-20251001`. Returns structured JSON: `{score, themes, tickers/assets, market_question, surprise, rationale}`. Themes are free-form (not from a fixed list) so new signal types are caught automatically.
4. **Fallback** (no API key or call failure) — three-layer ticker detection: (a) cashtag extraction (`$TICKER` patterns from article text), (b) Yahoo Finance trending tickers checked against article words (refreshed every 30min, crypto/indices/foreign filtered out), (c) small name→symbol map for well-known companies without cashtags.

#### Equity themes (Haiku decides, examples):
`ai_product_launch`, `competitor_threat`, `macro_signal`, `geopolitical`, `ipo_filing`, `merger_acquisition`, `regulatory`, `earnings_surprise`, `executive_change`, `company_pivot`

#### Crypto themes (Haiku decides, examples):
`price_milestone`, `protocol_upgrade`, `regulatory_action`, `etf_approval`, `exchange_event`, `institutional_adoption`, `defi_exploit`, `stablecoin_event`, `whale_movement`, `listing_delisting`, `macro_impact`, `layer2_news`, `legal_action`

#### Prediction market scanning:
- Polymarket: top 50–100 markets by volume
- Kalshi: top 50–100 open markets
- Matching uses: (1) extracted tickers/assets directly, (2) Haiku's `market_question` keywords, (3) theme-based vocabulary. No fixed watchlist in the matching logic.

**Storage:** Supabase `alerts` table. Crypto alerts tagged `asset_class: "crypto"` in `reasoning_json`.
**Thresholds:** Alert threshold ≥ 6, DB threshold ≥ 4.
**Schedule:** GitHub Actions every 15 minutes, `scan` and `crypto-scan` jobs run in parallel.

---

### Phase 2 — Signal Validation
**File:** `signal_validator.py`

- Price tracking via Yahoo Finance (free, no key)
- 24h ≥2%, 7d ≥4%, 30d ≥8% move = validated
- Date-based price lookup (not row-index) — measures actual calendar days
- `outcome` written for both wins (`validated_1d`) and losses (`not_validated_1d`) — unbiased precedent set
- Options data: IV, put/call ratio, total options volume (nearest expiry)
- Unusual Whales RSS for flow confirmation
- Weekly calibration report via Telegram: precision by source, theme, score bucket
- Target: precision > 40% before activating reasoning engine

---

### Phase 3 — Claude Reasoning Engine
**File:** `claude_reasoning.py`
**Activate:** Only after 30 days of validation data in Supabase

- Claude Haiku + GPT-4o mini + Gemini 1.5 Flash ensemble
- Model labels tracked per-call — a Claude failure + GPT success is correctly labeled GPT, not mislabeled
- 7 expert thesis lenses: Buffett, Munger, Soros, Burry, Druckenmiller, Wood, Lynch
- `--pending` flag properly gates execution (bug fixed — previously always ran)
- Gap detection: combined prob − market implied prob
- ⚠️ `_market_implied_prob()` hardcoded to 50% — must wire real Polymarket/Kalshi price data before gap detection is meaningful. See `claude_reasoning.py:_market_implied_prob()`.
- Precedent memory: queries both validated and not-validated signals by theme

---

### Phase 4 — Missing Layers (Month 2)
**To build next:**
- `sentiment_watcher.py` — Reddit RSS, StockTwits stream
- `calendar_watcher.py` — earnings dates, FOMC, CPI/GDP
- Deduplication engine — same story across 3+ feeds = amplified signal, not 3 alerts
- Wire real Polymarket/Kalshi price data into `_market_implied_prob()`
- `sizing_engine.py` — gap strength → Kelly position size (advisory only, needs calibration data)

---

### Phase 5 — Content Pipeline (Month 2–3)
**To build:**
- `chart_generator.py` — mplfinance price chart with signal timestamp
- `card_generator.py` — Pillow infographic card per validated signal
- `content_pipeline.py` — Claude writes 60s post-mortem script
- `poster.py` — distributes to Instagram, TikTok, YouTube Shorts

Content strategy: post-mortems only, never predictions. "We flagged this on [date]. Here's what happened." Timestamps are the credibility.

---

### Phase 6 — Repo & Secrets (Month 2–3)
**Now:** Single public repo — free Actions, nothing valuable to protect yet.

**Month 2 split:**
- `prediction-agent-public/` — infrastructure skeleton (open source)
- `prediction-agent-private/` — calibrated weights, validated data

Valuable assets stored in Supabase `signal_config` table (not committed):
`signal_weights.json`, `expert_weights.json`, `keyword_model.pkl`, `threshold_config.json`

---

## Infrastructure Stack

| Component | Tool | Cost |
|---|---|---|
| Scheduling | GitHub Actions (public repo) | $0 |
| Database | Supabase | $0 |
| Signal classifier | Claude Haiku | ~$1–3/mo (pre-filter limits calls) |
| Reasoning LLM | Claude Haiku → Sonnet 4.6 | $0 → ~$4/mo |
| Ensemble LLM 1 | GPT-4o mini | ~$0.25/mo |
| Ensemble LLM 2 | Gemini 1.5 Flash | ~$0.12/mo |
| Alerts | Telegram Bot API | $0 |
| Validation | Yahoo Finance API | $0 |
| Options flow | Unusual Whales RSS | $0 |
| **Total** | | **~$1–3 shadow / ~$7–9 month 2+** |

---

## Secrets & Key Management

### Keys and when needed

| Secret | Needed now? | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | Haiku classifier in both watchers |
| `SUPABASE_URL` | **Yes** | All files |
| `SUPABASE_KEY` | **Yes** | All files |
| `TELEGRAM_BOT_TOKEN` | **Yes** | Both watchers, signal_validator |
| `TELEGRAM_CHAT_ID` | **Yes** | Both watchers, signal_validator |
| `STOCKTWITS_ACCESS_TOKEN` | Week 3 | news_watcher.py only |
| `OPENAI_API_KEY` | Week 5–6 | claude_reasoning.py |
| `GOOGLE_API_KEY` | Week 5–6 | claude_reasoning.py |

### Local setup

```bash
# Step 1 — confirm .env is gitignored BEFORE creating it
cat .gitignore    # .env must appear here

# Step 2 — create from example
cp .env.example .env
# Fill in the 5 required keys

# Step 3 — install dependencies
pip install feedparser httpx python-dotenv supabase anthropic   # both watchers
pip install yfinance pandas                                       # signal_validator
pip install openai google-generativeai                           # claude_reasoning (week 5+)

# Step 4 — verify nothing secret is staged
git status        # .env must NOT appear
```

### GitHub Actions
Add secrets at: **repo → Settings → Secrets and variables → Actions → New repository secret**
Use exact names from the table above. Already wired in `agent.yml` via `${{ secrets.NAME }}`.

### What is safe to commit

| File | Safe? |
|---|---|
| `.env.example` | ✅ Yes — blank values only |
| `.env` | ❌ Never |
| `seen_items.json`, `seen_items_crypto.json` | ❌ Never (gitignored) |
| `alerts_log.jsonl`, `alerts_log_crypto.jsonl` | ❌ Never (gitignored) |
| All `.py` source files | ✅ Yes — no hardcoded secrets |
| `agent.yml` | ✅ Yes — uses `${{ secrets.NAME }}` only |

---

## Supabase Schema

```sql
CREATE TABLE alerts (
  id              bigserial PRIMARY KEY,
  scan_time       timestamptz NOT NULL,
  source          text,
  tier            int,
  title           text,
  link            text,
  themes          text[],
  watchlist       text[],
  tickers         text[],      -- also used for crypto assets
  score           int,
  shadow_mode     boolean DEFAULT true,
  gap_flag        boolean,
  market          text,
  platform        text,
  market_url      text,
  market_question text,        -- Haiku-generated prediction market question
  surprise        int,         -- 0-10, how unexpected vs consensus
  rationale       text,        -- Haiku one-line explanation
  outcome         text,
  outcome_1d      text,
  outcome_7d      text,
  outcome_30d     text,
  validated_1d    boolean,
  validated_7d    boolean,
  validated_30d   boolean,
  price_data_1d   jsonb,
  price_data_7d   jsonb,
  price_data_30d  jsonb,
  reasoned_at     timestamptz,
  ensemble_prob   float,
  expert_avg_prob float,
  gap             float,
  gap_strength    text,
  should_alert    boolean,
  reasoning_json  jsonb,       -- also stores asset_class, direction for crypto
  created_at      timestamptz DEFAULT now()
);

CREATE TABLE signal_config (
  key        text PRIMARY KEY,
  value      jsonb,
  updated_at timestamptz DEFAULT now(),
  notes      text
);

CREATE INDEX idx_scan_time   ON alerts(scan_time);
CREATE INDEX idx_score       ON alerts(score);
CREATE INDEX idx_outcome_1d  ON alerts(outcome_1d);
CREATE INDEX idx_reasoned_at ON alerts(reasoned_at);
CREATE INDEX idx_gap         ON alerts(gap);
```

---

## Key Design Decisions

**Why Haiku as semantic classifier:**
Keyword lists need constant manual expansion and miss paraphrasing. Haiku reads context and extracts implied tickers/assets without explicit name matches. The pre-filter drops irrelevant items before any API call, keeping cost low (~$1–3/month).

**Why free-form themes:**
Fixed theme lists constrain what the system can discover. Haiku labels themes descriptively — a new signal type (e.g., `ai_agent_autonomy`) appears naturally without code changes.

**Why separate crypto_watcher.py:**
Crypto has fundamentally different signal types (protocol upgrades, defi exploits, stablecoin events), different asset identifiers (no exchange tickers, use symbols), different Polymarket question patterns, and a `direction` field (bullish/bearish) that equities don't need. Shared infrastructure (Supabase, Telegram, Haiku) but separate feeds, prompts, and matching logic.

**Why dynamic trending tickers in fallback:**
The fallback runs without an API key. Yahoo Finance trending (refreshed every 30min) means the fallback automatically knows what the market is watching right now — no manual list maintenance.

**Why roundup filter in crypto:**
Daily "price predictions: BTC, ETH, XRP, SOL..." articles mention every major asset and would score 10/10 from pure asset count. They're noise, not signals.

**Why 30-day shadow period:**
Builds the precedent database and content backlog simultaneously. Without validated outcomes, the reasoning engine has no ground truth.

**Gap detection known limitation:**
`_market_implied_prob()` returns 50% hardcoded. Gap/gap_strength/should_alert are not meaningful until real Polymarket/Kalshi price data is wired in (Phase 4).

---

## Immediate Action List

```
Now — Deploy and start shadow period
  □ Confirm .env is gitignored (git status — .env must not appear)
  □ cp .env.example .env && fill in 5 keys
  □ Run Supabase schema SQL (add market_question, surprise, rationale columns if not present)
  □ Add gitignore entries: seen_items_crypto.json, alerts_log_crypto.jsonl
  □ Add 5 GitHub Secrets
  □ Push all files, watch first Actions run (both scan and crypto-scan jobs)
  □ Confirm alerts table has rows from both watchers
  □ Confirm Telegram is receiving alerts

Week 2 — Confirm data quality
  □ Check that themes are varied (not all macro_signal — that means prefilter too broad)
  □ Check that tickers/assets are non-empty on relevant signals
  □ Check market_question field is populated (requires API key)
  □ Run: python news_watcher.py --test && python crypto_watcher.py --test

Week 3 — Notifications
  □ Add STOCKTWITS_ACCESS_TOKEN to GitHub Secrets
  □ Confirm cashtag posts on StockTwits

Week 3–4 — Validation running
  □ signal_validator.py running daily
  □ outcome_1d filling for both wins and losses

Week 5 — First calibration checkpoint
  □ python signal_validator.py --report
  □ Precision > 40%? If yes → activate reasoning engine
  □ If no → tune Haiku prompt or scoring thresholds, run 2 more weeks

Week 5–6 — Reasoning engine
  □ Add OPENAI_API_KEY + GOOGLE_API_KEY to GitHub Secrets
  □ Wire real Polymarket/Kalshi prices into _market_implied_prob()
  □ Enable reasoning job schedule in agent.yml
  □ Start on Haiku, upgrade to Sonnet 4.6 when ready to pay

Month 2 — Phase 4
  □ sentiment_watcher.py
  □ calendar_watcher.py
  □ Deduplication engine
  □ sizing_engine.py
```

---

## Running Locally

```bash
# Equity signals
python news_watcher.py --test          # keyword fallback, no network
python news_watcher.py                 # live scan, writes to Supabase + Telegram
python news_watcher.py --watch --interval 300

# Crypto signals
python crypto_watcher.py --test        # keyword fallback, no network
python crypto_watcher.py --dry-run     # live feeds, no DB/Telegram writes
python crypto_watcher.py               # live scan, writes to Supabase + Telegram

# Validation
python signal_validator.py --horizon 1d
python signal_validator.py --report

# Reasoning (week 5+ only)
python claude_reasoning.py --pending
python claude_reasoning.py --test
```

---

*Last updated: Session 3 — April 2026*
*Status: Phase 1 complete (equity + crypto). Phases 2–3 built, awaiting data. Ready to deploy.*
*Next session: Start with `/cto` for a technical review, then confirm data is flowing into Supabase.*
