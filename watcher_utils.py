#!/usr/bin/env python3
"""
watcher_utils.py — Shared utilities for news_watcher.py and crypto_watcher.py.

Functions here are stream-agnostic. Agent-specific logic (prompts, feeds,
scoring, alert formatting, storage) stays in each watcher.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL       = os.getenv("SUPABASE_URL")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


# ── Deduplication ─────────────────────────────────────────────────────────────

def load_seen(stream: str) -> set[str]:
    """Load seen URL hashes from the last 7 days for this stream."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        rows = (
            sb.table("seen_urls")
            .select("url_hash")
            .eq("stream", stream)
            .gte("seen_at", cutoff)
            .execute()
        )
        return {r["url_hash"] for r in rows.data}
    except Exception as e:
        print(f"  [warn] load_seen failed: {e}", file=sys.stderr)
        return set()


def save_seen(new_hashes: list[str], stream: str) -> None:
    """Upsert new URL hashes into seen_urls. Only inserts hashes added this run."""
    if not new_hashes or not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        now = datetime.now(timezone.utc).isoformat()
        rows = [{"url_hash": h, "stream": stream, "seen_at": now} for h in new_hashes]
        sb.table("seen_urls").upsert(rows, on_conflict="url_hash").execute()
    except Exception as e:
        print(f"  [warn] save_seen failed: {e}", file=sys.stderr)


def item_id(source: str, link: str) -> str:
    """Return a SHA1 hash uniquely identifying a source+URL pair."""
    return hashlib.sha1(f"{source}|{link}".encode()).hexdigest()


# ── Cross-source story deduplication ─────────────────────────────────────────

def load_recent_alerted(alert_threshold: int, hours: int = 4) -> list[dict]:
    """Load alerts from the last N hours that triggered a Telegram notification."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = (
            sb.table("alerts")
            .select("tickers,themes")
            .gte("scan_time", cutoff)
            .gte("score", alert_threshold)
            .execute()
        )
        return rows.data
    except Exception as e:
        print(f"  [warn] load_recent_alerted failed: {e}", file=sys.stderr)
        return []


def is_duplicate_story(tickers: list[str], themes: list[str], recent: list[dict]) -> bool:
    """Return True if this article overlaps with a recently alerted story."""
    ticker_set = set(tickers)
    theme_set  = set(themes)
    for alert in recent:
        existing_tickers = set(alert.get("tickers") or [])
        existing_themes  = set(alert.get("themes")  or [])
        if ticker_set & existing_tickers and theme_set & existing_themes:
            return True
    return False


# ── JSON extraction ───────────────────────────────────────────────────────────

def extract_json(text: str) -> dict | None:
    """Extract a JSON object from model output, tolerating minor wrapping."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def fetch_json(url: str, headers: dict | None = None) -> dict | list | None:
    """GET a URL and return parsed JSON, or None on failure."""
    try:
        r = httpx.get(url, headers=headers or {}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [warn] fetch_json failed: {e}", file=sys.stderr)
        return None


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
            "parse_mode": "HTML",
        }, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"  [warn] Telegram send failed: {e}", file=sys.stderr)


# ── Weak signal storage ───────────────────────────────────────────────────────

def store_weak_signal(item: dict) -> bool:
    """Insert a score 1-3 article into weak_signals for future pattern analysis.
    Returns True on success, False on failure (so caller decides whether to mark seen)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        sb.table("weak_signals").insert({
            "scan_time": item["scan_time"],
            "source":    item["source"],
            "tier":      item["tier"],
            "title":     item["title"],
            "link":      item["link"],
            "score":     item["score"],
            "themes":    item["themes"],
            "tickers":   item["tickers"],
            "rationale": item.get("rationale"),
        }).execute()
        return True
    except Exception as e:
        print(f"  [warn] weak_signal insert failed: {e}", file=sys.stderr)
        return False


# ── Local logging ─────────────────────────────────────────────────────────────

def log_alert(item: dict, log_file: Path) -> None:
    with log_file.open("a") as f:
        f.write(json.dumps(item) + "\n")
