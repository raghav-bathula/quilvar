#!/usr/bin/env python3
"""
signal_validator.py — Price outcome tracking (24h/7d/30d), options data
(IV, put/call ratio), calibration report. Run daily at 4pm EST via GitHub Actions.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL       = os.getenv("SUPABASE_URL")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Validation thresholds (absolute % move)
THRESHOLDS = {"1d": 2.0, "7d": 4.0, "30d": 8.0}

UNUSUAL_WHALES_RSS = "https://unusualwhales.com/rss/options_flow.rss"


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_pending(horizon: str) -> list[dict]:
    """Fetch alerts that need validation for the given horizon."""
    col    = f"validated_{horizon}"
    sb     = _sb()
    days   = {"1d": 1, "7d": 7, "30d": 30}[horizon]
    now    = datetime.now(timezone.utc)
    cutoff_dt = (now - timedelta(days=days)).isoformat()

    # Only fetch rows old enough for this horizon, not yet validated, with tickers
    resp = (
        sb.table("alerts")
        .select("*")
        .is_(col, "null")
        .not_.is_("tickers", "null")
        .lte("scan_time", cutoff_dt)
        .order("scan_time", desc=False)
        .limit(200)
        .execute()
    )
    return resp.data or []


def update_row(row_id: int, patch: dict) -> None:
    sb = _sb()
    sb.table("alerts").update(patch).eq("id", row_id).execute()


# ── Price validation ──────────────────────────────────────────────────────────

def price_move_pct(ticker: str, from_dt: datetime, horizon_days: int) -> float | None:
    """Return % price move over horizon_days calendar days starting from from_dt.
    Uses the last available trading day on or before the target date."""
    target_dt = from_dt + timedelta(days=horizon_days)
    # Add a 5-day buffer so yfinance always returns enough data to find target date
    end_dt = target_dt + timedelta(days=5)
    try:
        hist = yf.download(
            ticker,
            start=from_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if hist.empty or len(hist) < 2:
            return None
        p_start = float(hist["Close"].iloc[0])
        # Find the last trading day on or before target_dt
        target_date = target_dt.date()
        hist_dates  = hist.index.date if hasattr(hist.index, "date") else [d.date() for d in hist.index]
        eligible    = [i for i, d in enumerate(hist_dates) if d <= target_date]
        if not eligible:
            return None
        p_end = float(hist["Close"].iloc[eligible[-1]])
        if p_start == 0:
            return None
        return round((p_end - p_start) / p_start * 100, 2)
    except Exception as e:
        print(f"  [warn] price fetch {ticker}: {e}", file=sys.stderr)
        return None


def validate_ticker(ticker: str, from_dt: datetime, horizon: str) -> dict:
    days = {"1d": 1, "7d": 7, "30d": 30}[horizon]
    move = price_move_pct(ticker, from_dt, days)
    threshold = THRESHOLDS[horizon]
    validated = abs(move) >= threshold if move is not None else False
    return {
        "ticker":    ticker,
        "move_pct":  move,
        "threshold": threshold,
        "validated": validated,
    }


# ── Options data ──────────────────────────────────────────────────────────────

def get_options_data(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            return {}
        # nearest expiration
        exp = exps[0]
        chain = t.option_chain(exp)
        calls = chain.calls
        puts  = chain.puts

        # ATM: strike closest to current price
        price = t.fast_info.get("lastPrice") or t.fast_info.get("regularMarketPrice")
        if price is None:
            info = t.info
            price = info.get("currentPrice") or info.get("regularMarketPrice", 0)

        if price and not calls.empty and not puts.empty:
            atm_call = calls.iloc[(calls["strike"] - price).abs().argsort()[:1]]
            atm_put  = puts.iloc[(puts["strike"]  - price).abs().argsort()[:1]]
            iv_call  = float(atm_call["impliedVolatility"].iloc[0]) if not atm_call.empty else None
            iv_put   = float(atm_put["impliedVolatility"].iloc[0])  if not atm_put.empty  else None
        else:
            iv_call, iv_put = None, None

        call_vol  = int(calls["volume"].sum()) if not calls.empty else 0
        put_vol   = int(puts["volume"].sum())  if not puts.empty  else 0
        pc_ratio  = round(put_vol / call_vol, 3) if call_vol > 0 else None
        total_vol = call_vol + put_vol

        return {
            "iv_atm_call":   iv_call,
            "iv_atm_put":    iv_put,
            "put_call_ratio": pc_ratio,
            "total_options_volume": total_vol,
            "expiration": exp,
        }
    except Exception as e:
        print(f"  [warn] options {ticker}: {e}", file=sys.stderr)
        return {}


# ── Unusual Whales confirmation ───────────────────────────────────────────────

def unusual_whales_tickers() -> set[str]:
    """Return set of tickers mentioned in recent Unusual Whales flow."""
    import feedparser
    tickers: set[str] = set()
    try:
        feed = feedparser.parse(UNUSUAL_WHALES_RSS)
        for entry in feed.entries[:50]:
            title = (entry.get("title", "") + " " + entry.get("summary", "")).upper()
            # extract $TICKER patterns
            import re
            tickers.update(re.findall(r"\$([A-Z]{1,5})\b", title))
    except Exception as e:
        print(f"  [warn] unusual whales: {e}", file=sys.stderr)
    return tickers


# ── Calibration report ────────────────────────────────────────────────────────

def calibration_report() -> str:
    sb   = _sb()
    resp = sb.table("alerts").select("*").order("scan_time", desc=False).limit(2000).execute()
    rows = resp.data or []

    if not rows:
        return "No alerts in database yet."

    df = pd.DataFrame(rows)

    lines = ["*Calibration Report*", f"Total alerts: {len(df)}", ""]

    for horizon in ("1d", "7d", "30d"):
        col = f"validated_{horizon}"
        if col not in df.columns:
            continue
        sub = df[df[col].notna()]
        if sub.empty:
            lines.append(f"{horizon}: no data yet")
            continue
        precision = sub[col].mean() * 100
        lines.append(f"{horizon} precision: {precision:.1f}% ({len(sub)} signals validated)")

    # by source
    lines.append("\nBy source (1d):")
    if "source" in df.columns and "validated_1d" in df.columns:
        by_source = df[df["validated_1d"].notna()].groupby("source")["validated_1d"].agg(["mean", "count"])
        for src, row in by_source.iterrows():
            lines.append(f"  {src}: {row['mean']*100:.0f}% ({int(row['count'])} signals)")

    # by score bucket
    lines.append("\nBy score (1d):")
    if "score" in df.columns and "validated_1d" in df.columns:
        df2 = df[df["validated_1d"].notna()].copy()
        df2["score_bucket"] = df2["score"].apply(lambda s: f"{(s//2)*2}-{(s//2)*2+1}")
        by_score = df2.groupby("score_bucket")["validated_1d"].agg(["mean", "count"])
        for bucket, row in by_score.iterrows():
            lines.append(f"  score {bucket}: {row['mean']*100:.0f}% ({int(row['count'])} signals)")

    # target check
    lines.append("\nTarget: precision > 40% before activating reasoning engine.")

    return "\n".join(lines)


def cleanup_weak_signals(days: int = 30) -> None:
    """Delete weak_signals rows older than `days`. Runs daily to enforce TTL."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sb = _sb()
        sb.table("weak_signals").delete().lt("scan_time", cutoff).execute()
        print(f"  [cleanup] pruned weak_signals older than {days} days")
    except Exception as e:
        print(f"  [warn] cleanup_weak_signals failed: {e}", file=sys.stderr)


def cleanup_seen_urls(days: int = 7) -> None:
    """Delete seen_urls rows older than `days`. Runs daily to enforce TTL."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sb = _sb()
        sb.table("seen_urls").delete().lt("seen_at", cutoff).execute()
        print(f"  [cleanup] pruned seen_urls older than {days} days")
    except Exception as e:
        print(f"  [warn] cleanup_seen_urls failed: {e}", file=sys.stderr)


def send_telegram(text: str) -> None:
    import httpx
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
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
        print(f"  [warn] Telegram: {e}", file=sys.stderr)


# ── Validation runner ─────────────────────────────────────────────────────────

def run_validation(horizon: str) -> None:
    print(f"Validating horizon={horizon}…")
    pending = fetch_pending(horizon)
    print(f"  {len(pending)} alerts pending.")

    uw_tickers = unusual_whales_tickers()

    for row in pending:
        row_id  = row["id"]
        tickers = row.get("tickers") or []
        scan_dt = datetime.fromisoformat(row["scan_time"].replace("Z", "+00:00"))

        results   = []
        validated = False

        for ticker in tickers:
            r = validate_ticker(ticker, scan_dt, horizon)
            results.append(r)
            if r["validated"]:
                validated = True

        opts = {}
        if tickers:
            opts = get_options_data(tickers[0])

        flow_confirmed = bool(uw_tickers & set(tickers))

        outcome_label = "validated" if validated else "not_validated"

        patch = {
            f"validated_{horizon}":  validated,
            f"outcome_{horizon}":    outcome_label,
            f"price_data_{horizon}": {
                "results":          results,
                "options":          opts,
                "flow_confirmed":   flow_confirmed,
            },
        }
        # fill top-level outcome on first horizon processed, win or loss
        if not row.get("outcome"):
            patch["outcome"] = f"validated_{horizon}" if validated else f"not_validated_{horizon}"

        update_row(row_id, patch)
        status = "✓" if validated else "✗"
        print(f"  [{status}] id={row_id} tickers={tickers} | {results}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Signal outcome validator")
    parser.add_argument("--horizon", choices=["1d", "7d", "30d"],
                        help="Validate a specific horizon")
    parser.add_argument("--report",  action="store_true",
                        help="Print calibration report")
    parser.add_argument("--schema",  action="store_true",
                        help="Print Supabase SQL schema and exit")
    args = parser.parse_args()

    if args.schema:
        print(SCHEMA_SQL)
        return

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_KEY required.", file=sys.stderr)
        sys.exit(1)

    if args.report:
        report = calibration_report()
        print(report)
        send_telegram(report)
        return

    if args.horizon:
        if args.horizon == "1d":
            cleanup_seen_urls()
            cleanup_weak_signals()
        run_validation(args.horizon)
    else:
        # run all horizons
        for h in ("1d", "7d", "30d"):
            run_validation(h)


SCHEMA_SQL = """
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
  reasoning_json  jsonb,
  created_at      timestamptz DEFAULT now()
);

CREATE TABLE signal_config (
  key         text PRIMARY KEY,
  value       jsonb,
  updated_at  timestamptz DEFAULT now(),
  notes       text
);

CREATE INDEX idx_scan_time   ON alerts(scan_time);
CREATE INDEX idx_score       ON alerts(score);
CREATE INDEX idx_outcome_1d  ON alerts(outcome_1d);
CREATE INDEX idx_reasoned_at ON alerts(reasoned_at);
CREATE INDEX idx_gap         ON alerts(gap);
"""


if __name__ == "__main__":
    main()
