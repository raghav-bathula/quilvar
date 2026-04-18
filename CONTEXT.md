# Quilvar — Stock Signal Agent

> Load this file at the start of each session. Run `/cto` for a full technical review.

---

## What We Are Building

Quilvar finds **news that moves stocks**. It monitors RSS feeds, semantically classifies articles using Claude Haiku, stores signals in Supabase, validates outcomes via price tracking, and delivers alerts via Telegram.

**Strategic decisions locked:**
- Quilvar = stock signal agent. NOT a prediction market gap detector.
- A separate agent will handle mispriced prediction markets later.
- Crypto pipeline built but disabled from schedule until equity is tuned.
- Folder restructure deferred until a 3rd agent is scoped.

**Core principle:** Advisory only. No auto-execution. Ever.
**Shadow period:** 30 days of signal logging before trusting any output.

---

## Component Summary

### 1. `news_watcher.py` — Equity Signal Pipeline
**What it does:** Fetches 12 RSS feeds every 30 min, classifies articles with Haiku, stores signals in Supabase, sends Telegram alerts.

**Feeds — 3 tiers:**
- Tier 1: SEC EDGAR 8-K, GlobeNewswire M&A, Yahoo Finance
- Tier 2: TechCrunch, HackerNews, The Verge, Ars Technica, Wired
- Tier 3: MarketWatch, CNBC, Bloomberg Markets, WSJ Markets

**Scoring pipeline per article:**
1. **Pre-filter** — 40+ broad terms (free, instant). Drops irrelevant items before any API call.
2. **Article body fetch** — tries RSS content field → fetches URL (5s timeout, 2000 chars) → falls back to RSS description. Paywall detection via marker strings.
3. **Haiku classification** — returns `{score 0-10, themes[], tickers[], market_question, surprise, rationale}`.
4. **Keyword fallback** — if no API key: cashtag extraction → Yahoo Finance trending tickers → name→symbol map.
5. **Cross-source dedup** — before alerting, checks last 4h of alerts for ticker+theme overlap. Same story from Bloomberg + CNBC = one Telegram.

**Thresholds:** `ALERT_THRESHOLD = 7` (Telegram), `DB_THRESHOLD = 4` (Supabase store)
**Schedule:** Every 30 min via GitHub Actions

---

### 2. `crypto_watcher.py` — Crypto Signal Pipeline
**What it does:** Same architecture as news_watcher but crypto-native. Currently **disabled from schedule** (manual dispatch only).

**Feeds — 8 sources:** CoinDesk, CoinTelegraph, Decrypt, The Block, Bitcoin Magazine, Blockworks, Bloomberg Markets, CNBC

**Key differences from equity:**
- Crypto-specific Haiku prompt returns `direction` (bullish/bearish/neutral)
- Asset detection via `CRYPTO_ASSETS` name→symbol map (22 symbols)
- Roundup filter drops "Price predictions: BTC, ETH, SOL..." aggregator articles
- `ALERT_THRESHOLD = 6`, `asset_class: "crypto"` stored in `reasoning_json`

**Why disabled:** Was flooding DB (46% of 530 rows were crypto). Re-enable after equity pipeline is tuned and `watcher_utils.py` is extracted.

---

### 3. `signal_validator.py` — Outcome Tracking
**What it does:** Daily job that checks if alerted stocks actually moved. Writes validated/not_validated for 1d/7d/30d horizons.

**Validation logic:**
- 24h ≥ 2% move = validated_1d
- 7d ≥ 4% move = validated_7d
- 30d ≥ 8% move = validated_30d
- Price lookup is date-based (not row-index) — measures actual calendar days
- Both wins AND losses written — unbiased calibration data

**Also does:**
- Options data: IV, put/call ratio, total volume (nearest expiry)
- Unusual Whales RSS for flow confirmation
- Weekly calibration report via Telegram
- `cleanup_seen_urls()` — prunes seen_urls rows older than 7 days (runs on --horizon 1d)

**Target:** Precision > 40% before activating reasoning engine.

**Current state:** First rows not yet 24h old. First real calibration data expected tonight (4pm EST validate job).

---

### 4. `claude_reasoning.py` — Reasoning Engine
**What it does:** Multi-model ensemble + 7 expert lenses per signal. **Not yet enabled on schedule.**

**Architecture:**
- Claude Haiku + GPT-4o mini + Gemini 1.5 Flash ensemble
- 7 expert thesis lenses: Buffett, Munger, Soros, Burry, Druckenmiller, Wood, Lynch
- Model labels tracked per-call to prevent mislabeling on API failures
- `--pending` flag gates execution correctly

**⚠️ Known blocker:** `_market_implied_prob()` hardcoded to 50%. All `gap`, `gap_strength`, `should_alert` values in DB are meaningless until wired to real market prices. Do not activate on schedule until this is fixed.

**Activate:** Only after calibration precision > 40% AND `_market_implied_prob()` is wired.

---

### 5. `schema.sql` — Supabase Schema

**Tables:**
- `alerts` — all signals, scores, outcomes, validation results
- `signal_config` — key-value config store
- `seen_urls` — article deduplication (url_hash, stream, seen_at) with 7-day TTL

**Key indexes:** `scan_time`, `score`, `outcome_1d`, `reasoned_at`, `gap`, `(stream, seen_at DESC)`

---

### 6. `.github/workflows/agent.yml` — Automation

| Job | Schedule | Status |
|---|---|---|
| `scan` (news_watcher.py) | Every 30 min | ✅ Running |
| `crypto-scan` | Manual only | ⏸ Disabled |
| `validate` | Daily 4pm EST | ✅ Running |
| `report` | Sunday noon UTC | ✅ Running |
| `reason` | Manual only | ⏸ Disabled |

---

## Deduplication — 3 Layers

| Layer | What | Where |
|---|---|---|
| URL dedup | SHA1 hash of source+link, 7-day TTL in `seen_urls` table | `load_seen()` / `save_seen()` |
| Story dedup | Ticker+theme overlap in last 4h → store but don't alert | `_is_duplicate_story()` |
| Daily cleanup | Prune seen_urls rows older than 7 days | `cleanup_seen_urls()` in signal_validator |

---

## Infrastructure Stack

| Component | Tool | Cost |
|---|---|---|
| Scheduling | GitHub Actions (public repo) | $0 |
| Database | Supabase | $0 |
| Signal classifier | Claude Haiku | ~$1–2/mo |
| Reasoning LLM | Claude Haiku (→ Sonnet later) | $0 now |
| Ensemble LLMs | GPT-4o mini + Gemini Flash | ~$0.37/mo when active |
| Alerts | Telegram Bot API | $0 |
| Validation | Yahoo Finance | $0 |
| **Total** | | **~$1–2 now / ~$7–9 month 2+** |

---

## Secrets

| Secret | Status | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ Set | Both watchers |
| `SUPABASE_URL` | ✅ Set | All files |
| `SUPABASE_KEY` | ✅ Set | All files |
| `TELEGRAM_BOT_TOKEN` | ✅ Set | Both watchers, validator |
| `TELEGRAM_CHAT_ID` | ✅ Set | Both watchers, validator |
| `STOCKTWITS_ACCESS_TOKEN` | ⏳ Week 3 | news_watcher only |
| `OPENAI_API_KEY` | ⏳ Week 5–6 | claude_reasoning |
| `GOOGLE_API_KEY` | ⏳ Week 5–6 | claude_reasoning |

---

## Current DB State (as of Session 4)

- **530 total rows** in alerts table
- **Score distribution:** 9→2, 8→17, 7→77, 6→209, 5→63, 4→162
- **162 equity rows** at score 6+ with non-empty tickers (validatable)
- **143 crypto rows** at score 6+ (tickers are BTC/ETH/SOL — yfinance may not validate correctly)
- **validated_1d:** 0 rows yet — rows not yet 24h old. First calibration data tonight.

---

## What's Done / What's Next

### Done ✅
- RSS ingestion (12 equity + 8 crypto feeds)
- Haiku semantic classifier with keyword fallback
- Article body fetching (RSS content field → URL fetch → RSS description)
- 3-layer deduplication (URL, story, daily cleanup)
- Supabase storage + seen_urls TTL table
- Telegram alerts (HTML mode)
- GitHub Actions automation (30-min scan, daily validate, weekly report)
- Cross-source story dedup (same event = one alert)
- Outcome validation (1d/7d/30d price tracking)
- ALERT_THRESHOLD raised to 7 (was 6)
- Crypto disabled from schedule

### Next (priority order) 🔲
1. **`watcher_utils.py`** — extract shared functions from both watchers. Every bug fix currently requires 2 edits. (Effort: S)
2. **`requirements.txt`** — pin package versions. One bad upstream release silently breaks scheduled runs. (Effort: S)
3. **Wait for `validated_1d` data** — first calibration data expected tonight. Run `--report` after.
4. **Fix `_market_implied_prob()`** — wire real Polymarket yes-price. Blocks reasoning engine activation. (Effort: M)
5. **`calendar_watcher.py`** — earnings + FOMC dates. Score-7 signal before NVDA earnings ≠ random Wednesday. (Effort: M)

### Deferred 🔮
- Re-enable crypto_watcher after equity tuned
- Folder restructure (agents/ + shared/) when 3rd agent is scoped
- Reasoning engine schedule activation (needs calibration data + real market prices)
- Sentiment watcher (Reddit RSS, StockTwits)
- Content pipeline (post-mortems, charts, social distribution)

---

## Running Locally

```bash
# Equity scan
python news_watcher.py --test        # sample data, no network
python news_watcher.py               # live scan

# Crypto scan (manual)
python crypto_watcher.py --test
python crypto_watcher.py

# Validation
python signal_validator.py --horizon 1d
python signal_validator.py --report

# Reasoning (week 5+ only)
python claude_reasoning.py --pending
```

---

*Last updated: Session 4 — April 2026*
*Status: Equity pipeline running. Shadow period in progress. Waiting for first validated_1d data tonight.*
