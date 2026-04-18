#!/usr/bin/env python3
"""
news_watcher.py — RSS ingestion, signal scoring, Polymarket/Kalshi scan,
Supabase storage, Telegram + StockTwits notifications.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
import re
from dotenv import load_dotenv

from watcher_utils import (
    extract_json,
    fetch_json,
    is_duplicate_story,
    item_id,
    load_recent_alerted,
    load_seen,
    log_alert,
    save_seen,
    send_telegram,
)

load_dotenv()

# ── Secrets ───────────────────────────────────────────────────────────────────
SUPABASE_URL            = os.getenv("SUPABASE_URL")
SUPABASE_KEY            = os.getenv("SUPABASE_KEY")
STOCKTWITS_ACCESS_TOKEN = os.getenv("STOCKTWITS_ACCESS_TOKEN")
ANTHROPIC_API_KEY       = os.getenv("ANTHROPIC_API_KEY")

ALERTS_LOG_FILE      = Path("alerts_log.jsonl")
MAX_ENTRIES_PER_FEED = 20   # cap per feed — runs are every 30 min
_STREAM              = "equity"

ALERT_THRESHOLD = 7   # send Telegram + StockTwits
DB_THRESHOLD    = 4   # store in Supabase

# ── RSS Feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Tier 1 — primary / authoritative sources
    # EDGAR requires a declared User-Agent per their fair-access policy
    {"url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=40&search_text=&output=atom",
     "source": "SEC EDGAR 8-K", "tier": 1,
     "agent": "quilvar-prediction-agent contact@quilvar.com"},
    {"url": "https://www.globenewswire.com/RssFeed/subjectcode/12-Mergers%20Acquisitions",
     "source": "GlobeNewswire M&A", "tier": 1},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC",
     "source": "Yahoo Finance", "tier": 1},
    # Tier 2 — AI / tech
    {"url": "https://techcrunch.com/feed/",          "source": "TechCrunch",   "tier": 2},
    {"url": "https://news.ycombinator.com/rss",      "source": "HackerNews",   "tier": 2},
    {"url": "https://www.theverge.com/rss/index.xml", "source": "The Verge",   "tier": 2},
    {"url": "https://feeds.arstechnica.com/arstechnica/index",
     "source": "Ars Technica", "tier": 2},
    {"url": "https://www.wired.com/feed/rss",         "source": "Wired",       "tier": 2},
    # Tier 3 — markets / macro
    {"url": "https://feeds.marketwatch.com/marketwatch/topstories/",
     "source": "MarketWatch", "tier": 3},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
     "source": "CNBC", "tier": 3},
    {"url": "https://feeds.bloomberg.com/markets/news.rss",
     "source": "Bloomberg Markets", "tier": 3},
    {"url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
     "source": "WSJ Markets", "tier": 3},
]

# ── Pre-filter vocabulary (broad — just to drop truly irrelevant items cheaply) ─
_PREFILTER_TERMS = [
    # AI / model companies and products
    "ai", "ml", "model", "llm", "launch", "claude", "anthropic", "openai", "chatgpt",
    "gemini", "gpt", "copilot", "nvidia", "llama", "mistral", "groq", "perplexity",
    # Corporate events
    "acqui", "merger", "ipo", "sec", "8-k", "earnings", "revenue", "guidance",
    "layoff", "hire", "ceo", "cfo", "fund", "invest", "valuation", "funding",
    # Market signals
    "market", "stock", "share", "rate", "fed", "tariff", "inflation", "gdp",
    "startup", "partner", "deal", "pivot", "rebrand", "compet", "threat", "disrupt",
    # Macro
    "war", "sanction", "strait", "opec", "oil", "ceasefire", "iran", "china",
]

# ── Haiku classification ───────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """\
You are a signal detector for a prediction market trading system. Your job is to identify \
news that would move prediction market odds by 10+ percentage points on Polymarket or Kalshi.

Given a headline and description, return a JSON object with exactly these fields:

- score: integer 0-10
  0 = noise (no market impact)
  3 = weak signal (notable but unlikely to move markets)
  5 = moderate signal (worth storing, may affect odds)
  7 = strong signal (likely moves prediction market odds 10-20pts)
  9 = major signal (moves markets, creates new prediction markets)

- themes: list of 1-3 free-form labels describing what KIND of signal this is.
  Examples: "ai_product_launch", "competitor_threat", "macro_rate_decision",
  "geopolitical_escalation", "ipo_filing", "regulatory_action", "executive_departure",
  "earnings_surprise", "company_pivot", "supply_chain_disruption", "m&a_announcement"
  Use your own labels if none of these fit. Prefer specific over generic.

- tickers: list of US stock tickers that would likely move on this news (uppercase).
  Think broadly — include THREATENED incumbents, BENEFICIARIES, and COMPETITORS.
  Example: Anthropic design tool launch → ["ADBE", "FIGMA", "WIX", "GDDY", "MSFT"]

- market_question: a single string — the prediction market question this event would create
  or move. E.g. "Will Adobe stock fall >5% this month?" or
  "Will the Fed cut rates at the May 2026 FOMC meeting?"
  Write null if no clear prediction market angle exists.

- surprise: integer 0-10 — how unexpected is this vs. market consensus?
  0 = fully priced in / expected, 10 = complete surprise

- rationale: one sentence explaining the score and what market opportunity exists.

Return ONLY valid JSON. No markdown fences, no explanation outside the JSON object.

Headline: {title}
Description: {description}
"""


def _passes_prefilter(title: str, description: str) -> bool:
    text = (title + " " + description).lower()
    return any(term in text for term in _PREFILTER_TERMS)


def classify_with_haiku(title: str, description: str) -> tuple[int, list[str], list[str], dict]:
    """
    Use Claude Haiku to semantically score a news item.
    Returns (score, themes, tickers, extras) where extras contains
    market_question, surprise, and rationale. Falls back to keyword scoring on failure.
    """
    if not ANTHROPIC_API_KEY:
        score, themes, tickers = _keyword_score(title, description)
        return score, themes, tickers, {}

    prompt = _CLASSIFY_PROMPT.format(
        title=title,
        description=description[:500],
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        data = extract_json(raw)
        if data:
            score   = max(0, min(10, int(data.get("score", 0))))
            themes  = [t for t in data.get("themes", []) if isinstance(t, str)]
            tickers = [t for t in data.get("tickers", []) if isinstance(t, str)]
            extras  = {
                "market_question": data.get("market_question"),
                "surprise":        data.get("surprise"),
                "rationale":       data.get("rationale"),
            }
            return score, themes, tickers, extras
    except Exception as e:
        print(f"  [warn] Haiku classify failed: {e}", file=sys.stderr)

    score, themes, tickers = _keyword_score(title, description)
    return score, themes, tickers, {}


# ── Keyword fallback (used when no API key or Haiku call fails) ───────────────

_KEYWORD_THEMES: dict[str, list[str]] = {
    "ai_product_launch": [
        "ai product", "launches ai", "ai tool", "ai assistant", "announces ai",
        "releases ai", "ai platform", "generative ai", "large language model",
        "llm", "ai model", "ai agent", "claude", "chatgpt", "gemini", "gpt-4",
        "copilot", "ai feature", "ai powered", "ai-powered", "foundation model",
    ],
    "ai_pivot": [
        "pivots to ai", "ai pivot", "rebrands", "ai company", "ai-first",
        "ai strategy", "transforms to ai", "becomes ai", "ai rebrand",
        "new direction", "strategic shift", "ai transformation",
    ],
    "competitor_threat": [
        "threatens", "disrupts", "competes with", "market share", "undercuts",
        "replaces", "obsoletes", "disruption", "competitive threat", "rival",
        "alternative to", "beats", "outperforms", "displaces",
    ],
    "macro_signal": [
        "fed rate", "interest rate", "inflation", "recession", "gdp",
        "unemployment", "fomc", "cpi", "tariff", "trade war", "yield curve",
        "rate hike", "rate cut", "federal reserve", "treasury", "basis points",
    ],
    "geopolitical": [
        "strait of hormuz", "sanction", "ceasefire", "trade war", "opec",
        "oil supply", "trade ban", "export control", "embargo",
        "military conflict", "armed conflict", "iran war", "china tariff",
    ],
    "ipo_ma": [
        "ipo", "initial public offering", "files to go public", "merger", "acquires",
        "acquisition", "takeover", "buyout", "going public",
    ],
    "earnings": [
        "earnings beat", "earnings miss", "revenue beat", "revenue miss",
        "raised guidance", "lowered guidance", "quarterly results", "eps beat", "eps miss",
    ],
}

# Last-resort name→ticker map — only for companies unlikely to appear as $cashtags
# and not caught by trending tickers. Keep small.
_FALLBACK_WATCHLIST: dict[str, list[str]] = {
    "ADBE": ["adobe"],
    "FIGMA": ["figma"],
    "BIRD":  ["allbirds", "newbird"],
    "GDDY":  ["godaddy"],
    "DOCU":  ["docusign"],
}

# ── Trending ticker cache ─────────────────────────────────────────────────────
_trending_cache: list[str] = []
_trending_fetched_at: float = 0.0
_TRENDING_TTL = 1800  # refresh every 30 minutes


def _get_trending_tickers() -> list[str]:
    """
    Fetch currently trending US tickers from Yahoo Finance.
    No API key required. Cached for 30 minutes per process.
    Returns up to 50 symbols.
    """
    global _trending_cache, _trending_fetched_at
    now = time.time()
    if _trending_cache and (now - _trending_fetched_at) < _TRENDING_TTL:
        return _trending_cache
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/trending/US"
        r = httpx.get(
            url,
            params={"count": 50},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        r.raise_for_status()
        quotes = r.json()["finance"]["result"][0]["quotes"]
        _trending_cache = [
            q["symbol"] for q in quotes
            if q.get("symbol")
            and len(q["symbol"]) >= 2          # drop single-letter symbols
            and "-" not in q["symbol"]         # drop crypto (BTC-USD, ETH-USD)
            and "^" not in q["symbol"]         # drop indices (^DJI, ^GSPC)
            and "." not in q["symbol"]         # drop foreign listings (CNQ.TO)
        ]
        _trending_fetched_at = now
        print(f"  [trending] fetched {len(_trending_cache)} tickers", file=sys.stderr)
    except Exception as e:
        print(f"  [warn] trending tickers fetch failed: {e}", file=sys.stderr)
    return _trending_cache


def _extract_cashtags(text: str) -> list[str]:
    """Extract explicit $TICKER mentions from article text."""
    return list(dict.fromkeys(re.findall(r"\$([A-Z]{1,5})\b", text)))


def _keyword_score(title: str, description: str) -> tuple[int, list[str], list[str]]:
    """
    Fallback scorer — no API key required.
    Ticker detection priority:
      1. Explicit $TICKER cashtags in article text (unrestricted)
      2. Trending tickers whose symbol appears as a word in the text
      3. Small fallback name→ticker map for well-known companies without cashtags
    """
    raw_text = title + " " + description
    lower_text = raw_text.lower()

    matched_themes = [
        t for t, kws in _KEYWORD_THEMES.items()
        if any(kw in lower_text for kw in kws)
    ]

    # 1 — cashtags
    tickers: list[str] = _extract_cashtags(raw_text)

    # 2 — trending tickers: symbol appears as a standalone word in text
    #     Require >= 2 chars to avoid matching common abbreviations (I, A, S, P...)
    words = set(re.findall(r"\b[A-Z]{2,5}\b", raw_text))
    for symbol in _get_trending_tickers():
        if symbol in words and symbol not in tickers:
            tickers.append(symbol)

    # 3 — name-based fallback for a small set of important companies
    for ticker, names in _FALLBACK_WATCHLIST.items():
        if ticker not in tickers and any(n in lower_text for n in names):
            tickers.append(ticker)

    raw = (len(matched_themes) * 2) + len(tickers)
    return min(raw, 10), matched_themes, tickers

# ── Market APIs ───────────────────────────────────────────────────────────────
POLYMARKET_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&limit=50&order=volume&ascending=false"
)
KALSHI_URL = (
    "https://trading-api.kalshi.com/trade-api/v2/markets"
    "?limit=50&status=open"
)


# ── Article body fetcher ──────────────────────────────────────────────────────

_PAYWALL_MARKERS = [
    "subscribe to read", "subscribe to continue", "sign in to read",
    "create a free account", "already a subscriber", "subscription required",
    "to continue reading", "this content is for subscribers",
]

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return re.sub(r"\s{2,}", " ", text).strip()


def _fetch_article_body(url: str, source: str, rss_content: str, rss_description: str) -> str:
    """
    Return the best available text for Haiku to classify.
    Priority:
      1. RSS full content field (if >= 500 chars) — free, no network call
      2. Fetched article body (stripped HTML, first 2000 chars)
      3. RSS description fallback
    """
    # 1 — RSS already has full content
    if len(rss_content) >= 500:
        return rss_content[:2000]

    # SEC EDGAR: the RSS description IS the filing summary — fetch adds nothing
    if "SEC EDGAR" in source:
        return rss_description[:2000]

    # 2 — fetch the article
    try:
        r = httpx.get(
            url,
            timeout=5,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200:
            return rss_description[:2000]

        body = _strip_html(r.text)

        # paywall detection
        lower = body.lower()
        if any(marker in lower for marker in _PAYWALL_MARKERS) or len(body) < 200:
            return rss_description[:2000]

        return body[:2000]

    except Exception:
        return rss_description[:2000]


# ── Signal Scoring ────────────────────────────────────────────────────────────

def score_item(title: str, description: str) -> tuple[int, list[str], list[str], dict]:
    """
    Score a news item. Uses Claude Haiku for semantic classification when
    ANTHROPIC_API_KEY is set and the item passes the pre-filter; otherwise
    falls back to keyword matching.
    Returns (score, themes, tickers, extras).
    """
    if not _passes_prefilter(title, description):
        return 0, [], [], {}
    return classify_with_haiku(title, description)


# ── Market Scanning ───────────────────────────────────────────────────────────

def _market_relevance(
    market_question: str,
    tickers: list[str],
    themes: list[str],
    haiku_market_q: str | None = None,
) -> bool:
    """Check if a Polymarket/Kalshi question is relevant to the signal.

    Priority order:
    1. Direct ticker mention in the market question text
    2. Haiku-generated market_question keyword overlap (most precise)
    3. Theme keyword overlap (broad fallback)
    """
    q = market_question.lower()

    # 1 — any of Haiku's extracted tickers appear in the market question
    for ticker in tickers:
        if ticker.lower() in q:
            return True

    # 2 — words from Haiku's own market_question appear in the Polymarket/Kalshi question
    if haiku_market_q:
        # key nouns from haiku question (skip short stop-words)
        words = [w.lower() for w in haiku_market_q.split() if len(w) > 4]
        if sum(1 for w in words if w in q) >= 2:
            return True

    # 3 — theme keyword fallback (intentionally broad)
    theme_words = {
        "ai_product_launch":       ["ai", "artificial intelligence", "launch", "model"],
        "ai_pivot":                ["ai", "rebrand", "pivot"],
        "competitor_threat":       ["market share", "compete", "rival"],
        "macro_signal":            ["rate", "fed", "inflation", "recession"],
        "macro_rate_decision":     ["rate", "fed", "fomc", "basis points"],
        "geopolitical_escalation": ["war", "sanction", "strait", "iran", "china", "tariff"],
        "ipo_filing":              ["ipo", "public offering", "listing"],
        "merger_acquisition":      ["acqui", "merger", "deal", "takeover"],
        "regulatory":              ["regulation", "antitrust", "ban", "fine"],
        "earnings_surprise":       ["earnings", "revenue", "beat", "miss", "guidance"],
    }
    for theme in themes:
        if any(w in q for w in theme_words.get(theme, [])):
            return True

    return False


def scan_markets(tickers: list[str], themes: list[str], haiku_market_q: str | None = None) -> list[dict]:
    hits: list[dict] = []

    poly_data = fetch_json(POLYMARKET_URL)
    if isinstance(poly_data, list):
        for m in poly_data:
            q = m.get("question", "")
            if _market_relevance(q, tickers, themes, haiku_market_q):
                hits.append({
                    "platform":   "polymarket",
                    "market":     q,
                    "market_url": m.get("url", ""),
                })

    # Kalshi now requires authentication — disabled until API key is added
    # kalshi_data = _fetch_json(KALSHI_URL)

    return hits


# ── Notifications ─────────────────────────────────────────────────────────────

def send_stocktwits(body: str) -> None:
    if not STOCKTWITS_ACCESS_TOKEN:
        print("  [warn] StockTwits not configured, skipping.", file=sys.stderr)
        return
    url = "https://api.stocktwits.com/api/2/messages/create.json"
    try:
        r = httpx.post(
            url,
            data={"body": body[:1000]},
            headers={"Authorization": f"OAuth {STOCKTWITS_ACCESS_TOKEN}"},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"  [warn] StockTwits send failed: {e}", file=sys.stderr)


def _build_alert_text(item: dict) -> str:
    import html as _html
    cashtags = " ".join(f"${t}" for t in item["tickers"]) if item["tickers"] else "—"
    themes   = ", ".join(item["themes"]) if item["themes"] else "—"

    lines = [
        f"<b>SIGNAL [{item['score']}/10]</b> — {_html.escape(item['source'])} (Tier {item['tier']})",
        _html.escape(item["title"]),
        f"Themes: {_html.escape(themes)}",
        f"Tickers: {cashtags}",
    ]
    if item.get("market_question"):
        lines.append(f"Market Q: {_html.escape(item['market_question'])}")
    if item.get("surprise") is not None:
        lines.append(f"Surprise: {item['surprise']}/10")
    if item.get("rationale"):
        lines.append(f"<i>{_html.escape(item['rationale'])}</i>")
    if item.get("market"):
        lines.append(f"Matched market: {_html.escape(item['market'])} ({item['platform']})")
    lines.append(item["link"])
    return "\n".join(lines)


# ── Supabase Storage ──────────────────────────────────────────────────────────

def store_alert(item: dict) -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  [warn] Supabase not configured, skipping DB write.", file=sys.stderr)
        return
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        sb.table("alerts").insert({
            "scan_time":       item["scan_time"],
            "source":          item["source"],
            "tier":            item["tier"],
            "title":           item["title"],
            "link":            item["link"],
            "themes":          item["themes"],
            "watchlist":       item["tickers"],
            "tickers":         item["tickers"],
            "score":           item["score"],
            "shadow_mode":     True,
            "gap_flag":        len(item.get("markets", [])) > 0,
            "market":          item.get("market"),
            "platform":        item.get("platform"),
            "market_url":      item.get("market_url"),
            "market_question": item.get("market_question"),
            "surprise":        item.get("surprise"),
            "rationale":       item.get("rationale"),
        }).execute()
    except Exception as e:
        print(f"  [warn] Supabase insert failed: {e}", file=sys.stderr)


# ── Core Scan Loop ────────────────────────────────────────────────────────────

def run_scan(dry_run: bool = False) -> int:
    seen    = load_seen(_STREAM)
    new_ids: list[str] = []
    alerts  = 0
    recent_alerted: list[dict] = load_recent_alerted(ALERT_THRESHOLD)

    for feed_def in RSS_FEEDS:
        url    = feed_def["url"]
        source = feed_def["source"]
        tier   = feed_def["tier"]

        agent = feed_def.get("agent")
        print(f"[{source}] parsing…")
        try:
            feed = feedparser.parse(url, agent=agent) if agent else feedparser.parse(url)
        except Exception as e:
            print(f"  [error] parse failed: {e}", file=sys.stderr)
            continue
        if not feed.entries:
            print(f"  [warn] 0 entries returned (feed may be blocked or empty)", file=sys.stderr)

        for entry in feed.entries[:MAX_ENTRIES_PER_FEED]:
            link    = entry.get("link", "")
            title   = entry.get("title", "")
            desc    = entry.get("summary", entry.get("description", ""))
            content = entry.get("content", [{}])[0].get("value", "") if entry.get("content") else ""

            uid = item_id(source, link)
            if uid in seen:
                continue
            seen.add(uid)
            new_ids.append(uid)

            body  = _fetch_article_body(link, source, _strip_html(content), _strip_html(desc))
            score, themes, tickers, extras = score_item(title, body)
            if score < DB_THRESHOLD:
                continue

            markets = scan_markets(tickers, themes, extras.get("market_question")) if not dry_run else []
            top_market = markets[0] if markets else {}

            record: dict = {
                "scan_time":       datetime.now(timezone.utc).isoformat(),
                "source":          source,
                "tier":            tier,
                "title":           title,
                "link":            link,
                "score":           score,
                "themes":          themes,
                "tickers":         tickers,
                "markets":         markets,
                "market":          top_market.get("market"),
                "platform":        top_market.get("platform"),
                "market_url":      top_market.get("market_url"),
                "market_question": extras.get("market_question"),
                "surprise":        extras.get("surprise"),
                "rationale":       extras.get("rationale"),
            }

            log_alert(record, ALERTS_LOG_FILE)

            if not dry_run:
                store_alert(record)

            if score >= ALERT_THRESHOLD:
                if is_duplicate_story(tickers, themes, recent_alerted):
                    print(f"  [dedup] story already alerted, storing only | {title[:60]}")
                else:
                    alerts += 1
                    text = _build_alert_text(record)
                    print(f"  >>> ALERT score={score} | {title[:80]}")
                    if not dry_run:
                        send_telegram(text)
                        if tickers:
                            send_stocktwits(text.replace("*", ""))
                        recent_alerted.append({"tickers": tickers, "themes": themes})
                    else:
                        print(f"  [dry-run] would send:\n{text}\n")
            else:
                print(f"  [db] score={score} | {title[:80]}")

    if not dry_run:
        save_seen(new_ids, _STREAM)
    return alerts


# ── Mock data for --test ──────────────────────────────────────────────────────

def run_test() -> None:
    print("=== TEST MODE (no network calls) ===\n")
    samples = [
        ("TechCrunch", 2,
         "Anthropic launches Claude Design, a new product for creating quick visuals",
         "The company says Claude Design is intended to help people like founders and product managers without a design background share their ideas more easily",
         "https://techcrunch.com/example"),
        ("SEC EDGAR 8-K", 1,
         "Allbirds rebrands as NewBird AI, announces generative AI pivot",
         "Allbirds Inc filed an 8-K announcing a full corporate rebrand to NewBird AI effective immediately",
         "https://www.sec.gov/example"),
        ("CNBC", 3,
         "Iran declares Strait of Hormuz open to shipping but Trump says U.S. blockade still active",
         "Oil plunges 4% on news that Iran has unilaterally reopened the strait as ceasefire talks continue",
         "https://cnbc.com/example"),
        ("HackerNews", 2,
         "Show HN: I built a todo app in Rust",
         "",
         "https://news.ycombinator.com/item?id=99999"),
    ]
    for source, tier, title, desc, link in samples:
        score, themes, tickers, extras = score_item(title, desc)
        print(f"source={source} tier={tier} score={score}")
        print(f"  title:   {title}")
        print(f"  themes:  {themes}")
        print(f"  tickers: {tickers}")
        if extras.get("market_question"):
            print(f"  market_q: {extras['market_question']}")
        if extras.get("surprise") is not None:
            print(f"  surprise: {extras['surprise']}/10")
        if extras.get("rationale"):
            print(f"  rationale: {extras['rationale']}")
        if score >= ALERT_THRESHOLD:
            print(f"  → WOULD ALERT")
        elif score >= DB_THRESHOLD:
            print(f"  → WOULD STORE IN DB")
        else:
            print(f"  → SKIP")
        print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Prediction market news watcher")
    parser.add_argument("--test",     action="store_true", help="Dry run with sample data")
    parser.add_argument("--watch",    action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Watch interval in seconds")
    args = parser.parse_args()

    if args.test:
        run_test()
        return

    if args.watch:
        print(f"Watching every {args.interval}s. Ctrl-C to stop.")
        while True:
            n = run_scan()
            print(f"Scan complete — {n} alert(s) sent. Sleeping {args.interval}s…\n")
            time.sleep(args.interval)
    else:
        n = run_scan()
        print(f"\nScan complete — {n} alert(s) sent.")


if __name__ == "__main__":
    main()
