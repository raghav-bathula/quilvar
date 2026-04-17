#!/usr/bin/env python3
"""
news_watcher.py — RSS ingestion, signal scoring, Polymarket/Kalshi scan,
Supabase storage, Telegram + StockTwits notifications.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Secrets ───────────────────────────────────────────────────────────────────
SUPABASE_URL            = os.getenv("SUPABASE_URL")
SUPABASE_KEY            = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID        = os.getenv("TELEGRAM_CHAT_ID")
STOCKTWITS_ACCESS_TOKEN = os.getenv("STOCKTWITS_ACCESS_TOKEN")

SEEN_ITEMS_FILE = Path("seen_items.json")
ALERTS_LOG_FILE = Path("alerts_log.jsonl")

ALERT_THRESHOLD = 6   # send Telegram + StockTwits
DB_THRESHOLD    = 4   # store in Supabase

# ── RSS Feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Tier 1 — primary sources
    {"url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=40&search_text=&output=atom",
     "source": "SEC EDGAR 8-K", "tier": 1},
    {"url": "https://www.globenewswire.com/RssFeed/subjectcode/12-Mergers%20Acquisitions",
     "source": "GlobeNewswire M&A", "tier": 1},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC",
     "source": "Yahoo Finance", "tier": 1},
    # Tier 2 — AI / tech
    {"url": "https://techcrunch.com/feed/",         "source": "TechCrunch",   "tier": 2},
    {"url": "https://news.ycombinator.com/rss",     "source": "HackerNews",   "tier": 2},
    {"url": "https://www.theverge.com/rss/index.xml","source": "The Verge",   "tier": 2},
    {"url": "https://feeds.arstechnica.com/arstechnica/index",
     "source": "Ars Technica", "tier": 2},
    # Tier 3 — markets
    {"url": "https://feeds.reuters.com/reuters/businessNews",
     "source": "Reuters Business", "tier": 3},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
     "source": "CNBC", "tier": 3},
    {"url": "https://feeds.bloomberg.com/markets/news.rss",
     "source": "Bloomberg Markets", "tier": 3},
]

# ── Themes ────────────────────────────────────────────────────────────────────
THEMES: dict[str, list[str]] = {
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
}

# ── Watchlist: ticker → search terms ─────────────────────────────────────────
WATCHLIST: dict[str, list[str]] = {
    "ADBE": ["adobe"],
    "FIGMA":["figma"],
    "WIX":  ["wix"],
    "GDDY": ["godaddy"],
    "CRM":  ["salesforce", "slack"],
    "SNOW": ["snowflake"],
    "PLTR": ["palantir"],
    "BIRD": ["allbirds", "newbird"],
    "MSFT": ["microsoft"],
    "GOOG": ["google", "alphabet"],
    "AMZN": ["amazon", "aws"],
    "META": ["meta", "facebook"],
    "NVDA": ["nvidia"],
    "ORCL": ["oracle"],
    "ZM":   ["zoom"],
    "DOCU": ["docusign"],
    "SHOP": ["shopify"],
    "UBER": ["uber"],
    "LYFT": ["lyft"],
    "ABNB": ["airbnb"],
}

# ── Market APIs ───────────────────────────────────────────────────────────────
POLYMARKET_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&limit=50&order=volume&ascending=false"
)
KALSHI_URL = (
    "https://trading-api.kalshi.com/trade-api/v2/markets"
    "?limit=50&status=open"
)


# ── Deduplication ─────────────────────────────────────────────────────────────

def load_seen() -> set[str]:
    if SEEN_ITEMS_FILE.exists():
        return set(json.loads(SEEN_ITEMS_FILE.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    SEEN_ITEMS_FILE.write_text(json.dumps(list(seen)))


def item_id(source: str, link: str) -> str:
    return hashlib.sha1(f"{source}|{link}".encode()).hexdigest()


# ── Signal Scoring ────────────────────────────────────────────────────────────

def score_item(title: str, description: str) -> tuple[int, list[str], list[str]]:
    text = (title + " " + description).lower()

    matched_themes: list[str] = []
    for theme, keywords in THEMES.items():
        if any(kw in text for kw in keywords):
            matched_themes.append(theme)

    matched_tickers: list[str] = []
    for ticker, terms in WATCHLIST.items():
        if any(term in text for term in terms):
            matched_tickers.append(ticker)

    raw = (len(matched_themes) * 2) + len(matched_tickers)
    score = min(raw, 10)
    return score, matched_themes, matched_tickers


# ── Market Scanning ───────────────────────────────────────────────────────────

def _fetch_json(url: str, headers: dict | None = None) -> dict | list | None:
    try:
        r = httpx.get(url, headers=headers or {}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [warn] market fetch failed: {e}", file=sys.stderr)
        return None


def _market_relevance(market_question: str, tickers: list[str], themes: list[str]) -> bool:
    q = market_question.lower()
    for ticker in tickers:
        if ticker.lower() in q:
            return True
    for ticker, terms in WATCHLIST.items():
        if ticker in tickers:
            if any(t in q for t in terms):
                return True
    theme_words = {
        "ai_product_launch": ["ai", "launch", "product"],
        "ai_pivot":          ["ai", "rebrand", "pivot"],
        "competitor_threat": ["market share", "compete", "rival"],
        "macro_signal":      ["rate", "fed", "inflation", "recession"],
    }
    for theme in themes:
        if any(w in q for w in theme_words.get(theme, [])):
            return True
    return False


def scan_markets(tickers: list[str], themes: list[str]) -> list[dict]:
    hits: list[dict] = []

    poly_data = _fetch_json(POLYMARKET_URL)
    if isinstance(poly_data, list):
        for m in poly_data:
            q = m.get("question", "")
            if _market_relevance(q, tickers, themes):
                hits.append({
                    "platform":   "polymarket",
                    "market":     q,
                    "market_url": m.get("url", ""),
                })

    kalshi_data = _fetch_json(KALSHI_URL)
    if isinstance(kalshi_data, dict):
        for m in kalshi_data.get("markets", []):
            q = m.get("title", "")
            if _market_relevance(q, tickers, themes):
                hits.append({
                    "platform":   "kalshi",
                    "market":     q,
                    "market_url": f"https://kalshi.com/markets/{m.get('ticker_name', '')}",
                })

    return hits


# ── Notifications ─────────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [warn] Telegram not configured, skipping.", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = httpx.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": "Markdown",
        }, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"  [warn] Telegram send failed: {e}", file=sys.stderr)


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
    cashtags = " ".join(f"${t}" for t in item["tickers"]) if item["tickers"] else ""
    themes   = ", ".join(item["themes"]) if item["themes"] else "—"
    market_line = ""
    if item.get("market"):
        market_line = f"\nMarket: {item['market']} ({item['platform']})"

    return (
        f"*SIGNAL [{item['score']}/10]* — {item['source']} (Tier {item['tier']})\n"
        f"{item['title']}\n"
        f"Themes: {themes}\n"
        f"Tickers: {cashtags or '—'}"
        f"{market_line}\n"
        f"{item['link']}"
    )


# ── Supabase Storage ──────────────────────────────────────────────────────────

def store_alert(item: dict) -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  [warn] Supabase not configured, skipping DB write.", file=sys.stderr)
        return
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        sb.table("alerts").insert({
            "scan_time":   item["scan_time"],
            "source":      item["source"],
            "tier":        item["tier"],
            "title":       item["title"],
            "link":        item["link"],
            "themes":      item["themes"],
            "watchlist":   item["tickers"],
            "tickers":     item["tickers"],
            "score":       item["score"],
            "shadow_mode": True,
            "gap_flag":    len(item.get("markets", [])) > 0,
            "market":      item.get("market"),
            "platform":    item.get("platform"),
            "market_url":  item.get("market_url"),
        }).execute()
    except Exception as e:
        print(f"  [warn] Supabase insert failed: {e}", file=sys.stderr)


def log_alert(item: dict) -> None:
    with ALERTS_LOG_FILE.open("a") as f:
        f.write(json.dumps(item) + "\n")


# ── Core Scan Loop ────────────────────────────────────────────────────────────

def run_scan(dry_run: bool = False) -> int:
    seen    = load_seen()
    new_ids: set[str] = set()
    alerts  = 0

    for feed_def in RSS_FEEDS:
        url    = feed_def["url"]
        source = feed_def["source"]
        tier   = feed_def["tier"]

        print(f"[{source}] parsing…")
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  [error] parse failed: {e}", file=sys.stderr)
            continue

        for entry in feed.entries:
            link  = entry.get("link", "")
            title = entry.get("title", "")
            desc  = entry.get("summary", entry.get("description", ""))

            uid = item_id(source, link)
            if uid in seen:
                continue
            new_ids.add(uid)

            score, themes, tickers = score_item(title, desc)
            if score < DB_THRESHOLD:
                continue

            markets = scan_markets(tickers, themes) if not dry_run else []
            top_market = markets[0] if markets else {}

            record: dict = {
                "scan_time":  datetime.now(timezone.utc).isoformat(),
                "source":     source,
                "tier":       tier,
                "title":      title,
                "link":       link,
                "score":      score,
                "themes":     themes,
                "tickers":    tickers,
                "markets":    markets,
                "market":     top_market.get("market"),
                "platform":   top_market.get("platform"),
                "market_url": top_market.get("market_url"),
            }

            log_alert(record)

            if not dry_run:
                store_alert(record)

            if score >= ALERT_THRESHOLD:
                alerts += 1
                text = _build_alert_text(record)
                print(f"  >>> ALERT score={score} | {title[:80]}")
                if not dry_run:
                    send_telegram(text)
                    if tickers:
                        send_stocktwits(text.replace("*", ""))
                else:
                    print(f"  [dry-run] would send:\n{text}\n")
            else:
                print(f"  [db] score={score} | {title[:80]}")

    seen.update(new_ids)
    save_seen(seen)
    return alerts


# ── Mock data for --test ──────────────────────────────────────────────────────

def run_test() -> None:
    print("=== TEST MODE (no network calls) ===\n")
    samples = [
        ("TechCrunch", 2,
         "Anthropic launches Claude for design — Adobe and Figma shares fall",
         "https://techcrunch.com/example"),
        ("SEC EDGAR 8-K", 1,
         "Allbirds rebrands as NewBird AI, announces generative AI pivot",
         "https://www.sec.gov/example"),
        ("Reuters Business", 3,
         "Fed holds rates steady; inflation data in line with forecasts",
         "https://reuters.com/example"),
        ("HackerNews", 2,
         "Show HN: I built a todo app in Rust",
         "https://news.ycombinator.com/item?id=99999"),
    ]
    for source, tier, title, link in samples:
        score, themes, tickers = score_item(title, "")
        print(f"source={source} tier={tier} score={score}")
        print(f"  title:   {title}")
        print(f"  themes:  {themes}")
        print(f"  tickers: {tickers}")
        if score >= ALERT_THRESHOLD:
            print(f"  → WOULD ALERT")
        elif score >= DB_THRESHOLD:
            print(f"  → WOULD STORE IN DB")
        else:
            print(f"  → SKIP (score below threshold)")
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
