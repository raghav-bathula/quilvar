#!/usr/bin/env python3
"""
crypto_watcher.py — Crypto-specific RSS ingestion, signal scoring,
Polymarket/Kalshi crypto market scan, Supabase storage, Telegram alerts.

Mirrors news_watcher.py but crypto-native throughout:
- Crypto RSS feeds (CoinDesk, CoinTelegraph, Decrypt, The Block, Bitcoin Mag)
- Asset list instead of equity ticker watchlist
- Crypto-specific Haiku prompt (price milestones, protocol events, regulatory)
- Polymarket crypto market matching (BTC price targets, ETF approvals, etc.)
- Same Supabase alerts table, asset_class="crypto" column
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
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

SUPABASE_URL      = os.getenv("SUPABASE_URL")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

ALERTS_LOG_FILE      = Path("alerts_log_crypto.jsonl")
MAX_ENTRIES_PER_FEED = 20   # cap per feed — runs are every 30 min
_STREAM              = "crypto"

ALERT_THRESHOLD = 6
DB_THRESHOLD    = 4

# ── Crypto RSS feeds ──────────────────────────────────────────────────────────
CRYPTO_FEEDS = [
    # Tier 1 — primary crypto journalism
    {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
     "source": "CoinDesk", "tier": 1},
    {"url": "https://cointelegraph.com/rss",
     "source": "CoinTelegraph", "tier": 1},
    {"url": "https://decrypt.co/feed",
     "source": "Decrypt", "tier": 1},
    # Tier 2 — institutional / on-chain focus
    {"url": "https://www.theblock.co/rss.xml",
     "source": "The Block", "tier": 2},
    {"url": "https://bitcoinmagazine.com/.rss/full/",
     "source": "Bitcoin Magazine", "tier": 2},
    {"url": "https://blockworks.co/feed",
     "source": "Blockworks", "tier": 2},
    # Tier 3 — broad market context with crypto coverage
    {"url": "https://feeds.bloomberg.com/markets/news.rss",
     "source": "Bloomberg Markets", "tier": 3},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
     "source": "CNBC", "tier": 3},
]

# ── Crypto asset list: symbol → name variants ─────────────────────────────────
# Not a restriction — used for name→symbol resolution in fallback only.
# Haiku extracts assets freely without this list.
CRYPTO_ASSETS: dict[str, list[str]] = {
    "BTC":  ["bitcoin", "btc", "satoshi"],
    "ETH":  ["ethereum", "eth", "ether"],
    "SOL":  ["solana", "sol"],
    "BNB":  ["binance coin", "bnb", "binance smart chain", "bsc"],
    "XRP":  ["ripple", "xrp"],
    "DOGE": ["dogecoin", "doge"],
    "ADA":  ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink", "link"],
    "DOT":  ["polkadot", "dot"],
    "MATIC":["polygon", "matic", "pol"],
    "UNI":  ["uniswap", "uni"],
    "ATOM": ["cosmos", "atom"],
    "LTC":  ["litecoin", "ltc"],
    "NEAR": ["near protocol", "near"],
    "ARB":  ["arbitrum", "arb"],
    "OP":   ["optimism", "op"],
    "INJ":  ["injective", "inj"],
    "SUI":  ["sui network", "sui"],
    "APT":  ["aptos", "apt"],
    "USDT": ["tether", "usdt"],
    "USDC": ["usd coin", "usdc", "circle"],
}

# ── Pre-filter ────────────────────────────────────────────────────────────────
_CRYPTO_PREFILTER = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain", "defi",
    "nft", "token", "coin", "wallet", "exchange", "binance", "coinbase",
    "solana", "ripple", "stablecoin", "usdt", "usdc", "web3", "dao",
    "protocol", "on-chain", "mempool", "halving", "mining", "staking",
    "sec", "etf", "futures", "spot", "liquidat", "whale", "hack", "exploit",
    "rug", "fork", "upgrade", "mainnet", "testnet", "layer 2", "rollup",
]

# ── Haiku classification prompt ───────────────────────────────────────────────
_CRYPTO_CLASSIFY_PROMPT = """\
You are a signal detector for a crypto prediction market trading system. \
Your job is to identify news that would move prediction market odds on Polymarket or Kalshi \
by 10+ percentage points — e.g. "Will BTC hit $100k by Dec 2026?" or "Will a spot ETH ETF be approved?"

Given a headline and description, return a JSON object with exactly these fields:

- score: integer 0-10
  0 = noise  3 = weak  5 = moderate  7 = strong  9 = major

- assets: list of crypto asset symbols affected (e.g. ["BTC", "ETH", "SOL"])
  Include both directly mentioned AND indirectly affected assets.
  Example: stablecoin depeg → ["USDT", "BTC", "ETH"] (systemic risk)
  Example: Solana exploit → ["SOL", "ETH"] (ETH benefits as alternative)

- themes: list of 1-3 labels from:
  ["price_milestone", "protocol_upgrade", "regulatory_action", "etf_approval",
   "exchange_event", "institutional_adoption", "defi_exploit", "stablecoin_event",
   "whale_movement", "listing_delisting", "macro_impact", "mining_event",
   "layer2_news", "legal_action"]
  Use your own label if none fit.

- direction: "bullish", "bearish", or "neutral" — expected market impact

- market_question: the prediction market question this would create or move.
  E.g. "Will Bitcoin exceed $120,000 by December 2026?"
  Write null if no clear prediction market angle.

- surprise: integer 0-10 — how unexpected vs. current market consensus

- rationale: one sentence explaining the score and the opportunity.

Return ONLY valid JSON. No markdown, no explanation outside the JSON.

Headline: {title}
Description: {description}
"""

# ── Polymarket crypto markets ─────────────────────────────────────────────────
POLYMARKET_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&limit=100&order=volume&ascending=false"
)
KALSHI_URL = (
    "https://trading-api.kalshi.com/trade-api/v2/markets"
    "?limit=100&status=open"
)

# Keywords to match crypto Polymarket/Kalshi questions
_CRYPTO_MARKET_TERMS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
    "ripple", "xrp", "coinbase", "binance", "stablecoin",
    "halving", "etf", "spot etf", "sec crypto",
]


# ── Pre-filter ────────────────────────────────────────────────────────────────

# Titles matching these patterns are aggregator/roundup articles, not signals
_ROUNDUP_PATTERNS = [
    r"price predictions? \d",     # "Price predictions 4/17: BTC, ETH..."
    r"top \d+ crypto",            # "Top 10 crypto gainers"
    r"weekly recap",
    r"market (wrap|roundup|overview)",
    r"\d+ coins? to watch",
]


def _passes_prefilter(title: str, description: str) -> bool:
    text = (title + " " + description).lower()
    if not any(term in text for term in _CRYPTO_PREFILTER):
        return False
    # Drop known aggregator/roundup formats — they inflate scores without being signals
    for pattern in _ROUNDUP_PATTERNS:
        if re.search(pattern, title.lower()):
            return False
    return True


# ── Haiku classification ──────────────────────────────────────────────────────

def classify_with_haiku(title: str, description: str) -> tuple[int, list[str], list[str], dict]:
    """
    Returns (score, themes, assets, extras).
    Falls back to keyword scoring if no API key or call fails.
    """
    if not ANTHROPIC_API_KEY:
        return _keyword_score(title, description)

    prompt = _CRYPTO_CLASSIFY_PROMPT.format(
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
            score  = max(0, min(10, int(data.get("score", 0))))
            themes = [t for t in data.get("themes", []) if isinstance(t, str)]
            assets = [a.upper() for a in data.get("assets", []) if isinstance(a, str)]
            extras = {
                "direction":       data.get("direction"),
                "market_question": data.get("market_question"),
                "surprise":        data.get("surprise"),
                "rationale":       data.get("rationale"),
            }
            return score, themes, assets, extras
    except Exception as e:
        print(f"  [warn] Haiku classify failed: {e}", file=sys.stderr)

    return _keyword_score(title, description)


# ── Keyword fallback ──────────────────────────────────────────────────────────

_KEYWORD_THEMES: dict[str, list[str]] = {
    "price_milestone": [
        "all-time high", "ath", "all time high", "breaks", "surges past",
        "record high", "crashes below", "falls below", "drops to", "plunges",
        "rallies to", "support", "resistance",
    ],
    "regulatory_action": [
        "sec", "cftc", "regulation", "ban", "lawsuit", "crackdown",
        "approved", "rejected", "enforcement", "subpoena", "charges",
        "illegal", "compliance", "legislation",
    ],
    "etf_approval": [
        "etf", "exchange-traded fund", "spot etf", "etf approval",
        "etf rejection", "bitcoin etf", "ethereum etf",
    ],
    "exchange_event": [
        "coinbase", "binance", "kraken", "ftx", "exchange", "hack",
        "outage", "listing", "delisted", "suspended", "bankrupt", "collapsed",
    ],
    "institutional_adoption": [
        "microstrategy", "blackrock", "fidelity", "institutional", "treasury",
        "reserve", "corporate", "nation", "legal tender", "adopts bitcoin",
    ],
    "defi_exploit": [
        "exploit", "hack", "rug pull", "drained", "vulnerability",
        "attack", "stolen", "bridge hack", "protocol hack", "million drained",
    ],
    "stablecoin_event": [
        "depeg", "de-peg", "tether", "usdt", "usdc", "stablecoin",
        "circle", "peg broken", "bank run",
    ],
    "protocol_upgrade": [
        "upgrade", "hard fork", "soft fork", "mainnet", "merge",
        "halving", "eip", "bip", "testnet", "launch", "v2", "v3",
    ],
    "macro_impact": [
        "fed", "interest rate", "inflation", "recession", "risk-off",
        "risk assets", "dollar", "treasury", "macro",
    ],
}


def _extract_cashtags(text: str) -> list[str]:
    """Extract $SYMBOL patterns — works for both stocks and crypto."""
    return list(dict.fromkeys(re.findall(r"\$([A-Z]{2,6})\b", text)))


def _keyword_score(title: str, description: str) -> tuple[int, list[str], list[str], dict]:
    raw_text = title + " " + description
    lower    = raw_text.lower()

    themes = [t for t, kws in _KEYWORD_THEMES.items() if any(kw in lower for kw in kws)]

    # 1 — cashtags in text (e.g. $BTC $ETH)
    assets: list[str] = _extract_cashtags(raw_text)

    # 2 — name→symbol from CRYPTO_ASSETS
    for symbol, names in CRYPTO_ASSETS.items():
        if symbol not in assets and any(n in lower for n in names):
            assets.append(symbol)

    raw = (len(themes) * 2) + len(assets)
    return min(raw, 10), themes, assets, {}


def score_item(title: str, description: str) -> tuple[int, list[str], list[str], dict]:
    if not _passes_prefilter(title, description):
        return 0, [], [], {}
    return classify_with_haiku(title, description)


# ── Market scanning ───────────────────────────────────────────────────────────

def _crypto_market_relevance(
    market_question: str,
    assets: list[str],
    themes: list[str],
    haiku_market_q: str | None = None,
) -> bool:
    q = market_question.lower()

    # 1 — asset symbol or name appears in question
    for asset in assets:
        if asset.lower() in q:
            return True
        for name in CRYPTO_ASSETS.get(asset, []):
            if name in q:
                return True

    # 2 — haiku market_question keyword overlap
    if haiku_market_q:
        words = [w.lower() for w in haiku_market_q.split() if len(w) > 4]
        if sum(1 for w in words if w in q) >= 2:
            return True

    # 3 — broad crypto term in question
    if any(term in q for term in _CRYPTO_MARKET_TERMS):
        # only match if themes are also crypto-relevant
        crypto_themes = {
            "price_milestone", "regulatory_action", "etf_approval",
            "exchange_event", "institutional_adoption", "stablecoin_event",
            "protocol_upgrade", "macro_impact",
        }
        if set(themes) & crypto_themes:
            return True

    return False


def scan_markets(
    assets: list[str],
    themes: list[str],
    haiku_market_q: str | None = None,
) -> list[dict]:
    hits: list[dict] = []

    poly_data = fetch_json(POLYMARKET_URL)
    if isinstance(poly_data, list):
        for m in poly_data:
            q = m.get("question", "")
            if _crypto_market_relevance(q, assets, themes, haiku_market_q):
                hits.append({
                    "platform":   "polymarket",
                    "market":     q,
                    "market_url": m.get("url", ""),
                })

    # Kalshi now requires authentication — disabled until API key is added
    # kalshi_data = _fetch_json(KALSHI_URL)

    return hits


# ── Notifications ─────────────────────────────────────────────────────────────

def _build_alert_text(item: dict) -> str:
    import html as _html
    assets    = " ".join(item["assets"]) if item["assets"] else "—"
    themes    = ", ".join(item["themes"]) if item["themes"] else "—"
    direction = item.get("direction", "")
    dir_icon  = {"bullish": "▲", "bearish": "▼", "neutral": "◆"}.get(direction, "")

    lines = [
        f"<b>CRYPTO SIGNAL [{item['score']}/10]</b> {dir_icon} — {_html.escape(item['source'])} (Tier {item['tier']})",
        _html.escape(item["title"]),
        f"Assets: {_html.escape(assets)}",
        f"Themes: {_html.escape(themes)}",
    ]
    if item.get("market_question"):
        lines.append(f"Market Q: {_html.escape(item['market_question'])}")
    if item.get("surprise") is not None:
        lines.append(f"Surprise: {item['surprise']}/10")
    if item.get("rationale"):
        lines.append(f"<i>{_html.escape(item['rationale'])}</i>")
    if item.get("market"):
        lines.append(f"Matched: {_html.escape(item['market'])} ({item['platform']})")
    lines.append(item["link"])
    return "\n".join(lines)


# ── Supabase storage ──────────────────────────────────────────────────────────

def store_alert(item: dict) -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  [warn] Supabase not configured.", file=sys.stderr)
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
            "tickers":         item["assets"],   # reuse tickers column for assets
            "score":           item["score"],
            "shadow_mode":     True,
            "gap_flag":        len(item.get("markets", [])) > 0,
            "market":          item.get("market"),
            "platform":        item.get("platform"),
            "market_url":      item.get("market_url"),
            "market_question": item.get("market_question"),
            "surprise":        item.get("surprise"),
            "rationale":       item.get("rationale"),
            # crypto-specific extras stored in reasoning_json
            "reasoning_json":  {"asset_class": "crypto", "direction": item.get("direction")},
        }).execute()
    except Exception as e:
        print(f"  [warn] Supabase insert failed: {e}", file=sys.stderr)


# ── Core scan loop ────────────────────────────────────────────────────────────

def run_scan(dry_run: bool = False) -> int:
    seen    = load_seen(_STREAM)
    new_ids: list[str] = []
    alerts  = 0
    recent_alerted: list[dict] = load_recent_alerted(ALERT_THRESHOLD)

    for feed_def in CRYPTO_FEEDS:
        url    = feed_def["url"]
        source = feed_def["source"]
        tier   = feed_def["tier"]

        print(f"[{source}] parsing…")
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  [error] {e}", file=sys.stderr)
            continue
        if not feed.entries:
            print(f"  [warn] 0 entries", file=sys.stderr)
            continue

        for entry in feed.entries[:MAX_ENTRIES_PER_FEED]:
            link  = entry.get("link", "")
            title = entry.get("title", "")
            desc  = entry.get("summary", entry.get("description", ""))

            uid = item_id(source, link)
            if uid in seen:
                continue
            seen.add(uid)
            new_ids.append(uid)

            score, themes, assets, extras = score_item(title, desc)
            if score < DB_THRESHOLD:
                continue

            markets    = scan_markets(assets, themes, extras.get("market_question")) if not dry_run else []
            top_market = markets[0] if markets else {}

            record: dict = {
                "scan_time":       datetime.now(timezone.utc).isoformat(),
                "source":          source,
                "tier":            tier,
                "title":           title,
                "link":            link,
                "score":           score,
                "themes":          themes,
                "assets":          assets,
                "markets":         markets,
                "market":          top_market.get("market"),
                "platform":        top_market.get("platform"),
                "market_url":      top_market.get("market_url"),
                "direction":       extras.get("direction"),
                "market_question": extras.get("market_question"),
                "surprise":        extras.get("surprise"),
                "rationale":       extras.get("rationale"),
            }

            log_alert(record, ALERTS_LOG_FILE)
            if not dry_run:
                store_alert(record)

            if score >= ALERT_THRESHOLD:
                if is_duplicate_story(assets, themes, recent_alerted):
                    print(f"  [dedup] story already alerted, storing only | {title[:60]}")
                else:
                    alerts += 1
                    text = _build_alert_text(record)
                    print(f"  >>> ALERT score={score} {record.get('direction','')} | {title[:70]}")
                    if not dry_run:
                        send_telegram(text)
                        recent_alerted.append({"tickers": assets, "themes": themes})
                    else:
                        print(f"  [dry-run]\n{text}\n")
            else:
                print(f"  [db] score={score} | {title[:70]}")

    if not dry_run:
        save_seen(new_ids, _STREAM)
    return alerts


# ── Test mode ─────────────────────────────────────────────────────────────────

def run_test() -> None:
    print("=== CRYPTO TEST MODE (no network calls) ===\n")
    samples = [
        ("CoinDesk", 1,
         "Bitcoin hits new all-time high of $112,000 as institutional demand surges",
         "MicroStrategy and BlackRock spot ETF flows drove $BTC past previous record"),
        ("CoinTelegraph", 1,
         "SEC approves spot Ethereum ETF — trading begins Monday",
         "The long-awaited approval marks a turning point for $ETH institutional access"),
        ("The Block", 2,
         "Solana DeFi protocol drained of $180M in bridge exploit",
         "$SOL drops 12% as hackers drain the Wormhole bridge for the second time"),
        ("CNBC", 3,
         "Fed holds rates steady; crypto markets rally on risk-on sentiment",
         "$BTC $ETH both up 5% as investors rotate into risk assets"),
        ("Decrypt", 1,
         "Tether USDT briefly depegs to $0.97 amid Binance withdrawal concerns",
         "Stablecoin contagion fears spread to $BTC and $ETH as markets panic"),
    ]
    for source, tier, title, desc in samples:
        score, themes, assets, extras = score_item(title, desc)
        print(f"[{score}/10] {source} T{tier} | {title[:70]}")
        print(f"  assets={assets}  themes={themes}")
        if extras.get("direction"):
            print(f"  direction={extras['direction']}")
        if extras.get("market_question"):
            print(f"  market_q={extras['market_question']}")
        if extras.get("rationale"):
            print(f"  rationale={extras['rationale']}")
        if score >= ALERT_THRESHOLD:
            print(f"  → WOULD ALERT")
        elif score >= DB_THRESHOLD:
            print(f"  → WOULD STORE IN DB")
        else:
            print(f"  → SKIP")
        print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto prediction market signal watcher")
    parser.add_argument("--test",     action="store_true", help="Run with sample data, no network")
    parser.add_argument("--watch",    action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Watch interval seconds")
    parser.add_argument("--dry-run",  action="store_true", help="Fetch feeds but skip DB/Telegram")
    args = parser.parse_args()

    if args.test:
        run_test()
        return

    if args.watch:
        print(f"Watching every {args.interval}s. Ctrl-C to stop.")
        while True:
            n = run_scan(dry_run=args.dry_run)
            print(f"Scan complete — {n} alert(s). Sleeping {args.interval}s…\n")
            time.sleep(args.interval)
    else:
        n = run_scan(dry_run=args.dry_run)
        print(f"\nScan complete — {n} alert(s).")


if __name__ == "__main__":
    main()
