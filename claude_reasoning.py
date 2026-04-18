#!/usr/bin/env python3
"""
claude_reasoning.py — Multi-model ensemble (Claude + GPT + Gemini),
7 expert thesis lenses, gap detection, enriched Telegram alerts.

Activate only after 30 days of validation data in Supabase.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL       = os.getenv("SUPABASE_URL")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY     = os.getenv("GOOGLE_API_KEY")

# Gap thresholds
GAP_STRONG   = 25  # percentage points
GAP_MODERATE = 15

# Ensemble weights
ENSEMBLE_WEIGHT = 0.60
EXPERT_WEIGHT   = 0.40

# ── Expert lenses ─────────────────────────────────────────────────────────────

EXPERTS = [
    {
        "name":        "Warren Buffett",
        "philosophy":  "Moat durability, 10-year earnings lens, margin of safety",
        "focus":       "Is the company's competitive moat permanently damaged? "
                       "What are the 10-year earnings implications?",
    },
    {
        "name":        "Charlie Munger",
        "philosophy":  "Inversion, second-order consequences, mental models",
        "focus":       "Invert: what would have to be true for the market to be right? "
                       "What are the second-order consequences nobody is pricing?",
    },
    {
        "name":        "George Soros",
        "philosophy":  "Reflexivity, self-fulfilling feedback loops",
        "focus":       "Is this news creating a reflexive feedback loop? "
                       "Will the initial move reinforce itself or reverse?",
    },
    {
        "name":        "Michael Burry",
        "philosophy":  "Contrarian, what the market is missing",
        "focus":       "What is the market systematically ignoring or mispricing? "
                       "Where is the crowd wrong?",
    },
    {
        "name":        "Stan Druckenmiller",
        "philosophy":  "Asymmetry, timing and position sizing",
        "focus":       "What is the asymmetric bet here? Is the timing right? "
                       "How large should the position be?",
    },
    {
        "name":        "Cathie Wood",
        "philosophy":  "Disruption TAM, adoption curve speed",
        "focus":       "What is the total addressable market for this disruption? "
                       "How fast will adoption accelerate?",
    },
    {
        "name":        "Peter Lynch",
        "philosophy":  "Common sense, observable ground truth",
        "focus":       "What does common sense say? What can you observe on the ground "
                       "that the models are missing?",
    },
]


# ── Supabase ──────────────────────────────────────────────────────────────────

def _sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_pending_alerts() -> list[dict]:
    sb = _sb()
    resp = (
        sb.table("alerts")
        .select("*")
        .gte("score", 6)
        .is_("reasoned_at", "null")
        .order("scan_time", desc=True)
        .limit(20)
        .execute()
    )
    return resp.data or []


def fetch_precedents(themes: list[str], limit: int = 5) -> list[dict]:
    """Fetch historically validated signals with matching themes."""
    sb = _sb()
    resp = (
        sb.table("alerts")
        .select("title,themes,tickers,score,gap,outcome,validated_1d,validated_7d")
        .not_.is_("outcome", "null")
        .execute()
    )
    rows = resp.data or []
    # filter by theme overlap
    relevant = [
        r for r in rows
        if set(r.get("themes") or []) & set(themes)
    ]
    return relevant[:limit]


def store_reasoning(row_id: int, result: dict) -> None:
    sb = _sb()
    sb.table("alerts").update({
        "reasoned_at":     datetime.now(timezone.utc).isoformat(),
        "ensemble_prob":   result["ensemble_prob"],
        "expert_avg_prob": result["expert_avg_prob"],
        "gap":             result["gap"],
        "gap_strength":    result["gap_strength"],
        "should_alert":    result["should_alert"],
        "reasoning_json":  result,
    }).eq("id", row_id).execute()


# ── Model calls ───────────────────────────────────────────────────────────────

def _ask_claude(prompt: str, model: str = "claude-haiku-4-5-20251001") -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _ask_gpt(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=256,
    )
    return resp.choices[0].message.content.strip()


def _ask_gemini(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
    resp = model.generate_content(prompt)
    return resp.text.strip()


def _extract_probability(text: str) -> float | None:
    """Parse a 0–100 probability from model output."""
    import re
    # look for patterns like "65%", "65", "probability: 65"
    matches = re.findall(r"\b(\d{1,3})(?:\s*%|\s*percent|\s*probability)?\b", text)
    for m in matches:
        val = int(m)
        if 0 <= val <= 100:
            return float(val)
    return None


PROBABILITY_PROMPT = """You are a quantitative analyst estimating probability for a prediction market.

Signal: {title}
Source: {source} (Tier {tier})
Themes: {themes}
Tickers: {tickers}
Market question: {market}

Relevant historical precedents:
{precedents}

Based on this signal, what is the probability (0–100) that the event described in the
market question resolves YES within 30 days?

Respond with a single integer between 0 and 100, followed by a one-sentence rationale.
"""

EXPERT_PROMPT = """You are {name}, legendary investor. Your philosophy: {philosophy}.

Signal headline: {title}
Market question: {market}
Current market-implied probability: {market_prob}%

{focus}

What probability (0–100) would YOU assign to this market resolving YES?
Respond with a single integer between 0 and 100, then a one-sentence rationale in your voice.
"""


# ── Ensemble reasoning ────────────────────────────────────────────────────────

def run_ensemble(alert: dict, precedents: list[dict]) -> dict:
    title   = alert.get("title", "")
    source  = alert.get("source", "")
    tier    = alert.get("tier", 0)
    themes  = ", ".join(alert.get("themes") or [])
    tickers = ", ".join(alert.get("tickers") or [])
    market  = alert.get("market", "—")

    prec_text = "\n".join(
        f"- {p['title']} | themes={p.get('themes')} | outcome={p.get('outcome')}"
        for p in precedents
    ) or "No precedents found yet."

    base_prompt = PROBABILITY_PROMPT.format(
        title=title, source=source, tier=tier,
        themes=themes, tickers=tickers, market=market,
        precedents=prec_text,
    )

    probs: list[float] = []
    model_labels: list[str] = []
    model_outputs: dict[str, str] = {}

    # Claude
    if ANTHROPIC_API_KEY:
        try:
            text = _ask_claude(base_prompt)
            p    = _extract_probability(text)
            if p is not None:
                probs.append(p)
                model_labels.append("claude")
                model_outputs["claude"] = text
        except Exception as e:
            print(f"  [warn] Claude: {e}", file=sys.stderr)

    # GPT-4o mini
    if OPENAI_API_KEY:
        try:
            text = _ask_gpt(base_prompt)
            p    = _extract_probability(text)
            if p is not None:
                probs.append(p)
                model_labels.append("gpt4o_mini")
                model_outputs["gpt4o_mini"] = text
        except Exception as e:
            print(f"  [warn] GPT: {e}", file=sys.stderr)

    # Gemini Flash
    if GOOGLE_API_KEY:
        try:
            text = _ask_gemini(base_prompt)
            p    = _extract_probability(text)
            if p is not None:
                probs.append(p)
                model_labels.append("gemini_flash")
                model_outputs["gemini_flash"] = text
        except Exception as e:
            print(f"  [warn] Gemini: {e}", file=sys.stderr)

    ensemble_prob = round(sum(probs) / len(probs), 1) if probs else 50.0
    disagreement  = (max(probs) - min(probs)) if len(probs) > 1 else 0
    uncertain     = disagreement > 20

    return {
        "ensemble_prob": ensemble_prob,
        "model_probs":   dict(zip(model_labels, probs)),
        "model_outputs": model_outputs,
        "disagreement":  round(disagreement, 1),
        "uncertain":     uncertain,
    }


def run_expert_lenses(alert: dict, market_prob: float) -> dict:
    title  = alert.get("title", "")
    market = alert.get("market", "—")

    expert_probs: dict[str, float] = {}
    expert_rationales: dict[str, str] = {}

    if not ANTHROPIC_API_KEY:
        return {"expert_probs": {}, "expert_avg": market_prob}

    for expert in EXPERTS:
        prompt = EXPERT_PROMPT.format(
            name=expert["name"],
            philosophy=expert["philosophy"],
            focus=expert["focus"],
            title=title,
            market=market,
            market_prob=market_prob,
        )
        try:
            text = _ask_claude(prompt)
            p    = _extract_probability(text)
            if p is not None:
                expert_probs[expert["name"]] = p
                expert_rationales[expert["name"]] = text
        except Exception as e:
            print(f"  [warn] Expert {expert['name']}: {e}", file=sys.stderr)

    avg = round(sum(expert_probs.values()) / len(expert_probs), 1) if expert_probs else market_prob
    spread = (max(expert_probs.values()) - min(expert_probs.values())) if len(expert_probs) > 1 else 0

    return {
        "expert_probs":      expert_probs,
        "expert_rationales": expert_rationales,
        "expert_avg":        avg,
        "expert_spread":     round(spread, 1),
    }


def compute_gap(ensemble_prob: float, expert_avg: float, market_prob: float) -> dict:
    combined = round(ensemble_prob * ENSEMBLE_WEIGHT + expert_avg * EXPERT_WEIGHT, 1)
    gap      = round(combined - market_prob, 1)

    if abs(gap) >= GAP_STRONG:
        strength = "strong"
    elif abs(gap) >= GAP_MODERATE:
        strength = "moderate"
    else:
        strength = "weak"

    return {
        "combined_prob": combined,
        "market_prob":   market_prob,
        "gap":           gap,
        "gap_strength":  strength,
        "should_alert":  abs(gap) >= GAP_MODERATE,
    }


def _market_implied_prob(alert: dict) -> float:
    """Return market-implied probability for the alert's matched market.

    TODO (activate before enabling reasoning engine):
      Wire real Polymarket/Kalshi price data here.
      Polymarket: GET /markets/{conditionId} → outcomePrices[0] (yes price, 0–1)
      Kalshi:     GET /markets/{ticker} → yes_ask or last_price
      Store the fetched price in alerts.market_prob at scan time, then read it here.
      Until this is done, gap/gap_strength/should_alert are computed against 50%
      and are not meaningful for actual trading decisions.
    """
    return 50.0


# ── Notification ──────────────────────────────────────────────────────────────

def _format_alert(alert: dict, result: dict) -> str:
    tickers   = " ".join(f"${t}" for t in (alert.get("tickers") or []))
    gap_emoji = "▲" if result["gap"] > 0 else "▼"
    lines = [
        f"*REASONING ALERT [{alert['score']}/10]* {gap_emoji}",
        f"{alert['title']}",
        f"",
        f"Market: {alert.get('market', '—')}",
        f"",
        f"Ensemble: {result['ensemble_prob']}%  |  Expert avg: {result['expert_avg_prob']}%",
        f"Market implied: {result.get('market_prob', 50)}%",
        f"*Gap: {result['gap']:+.0f}pts ({result['gap_strength']})*",
        f"",
        f"Tickers: {tickers or '—'}",
        f"Uncertain: {'yes — disagreement >' + str(result.get('disagreement', 0)) + 'pts' if result.get('uncertain') else 'no'}",
        f"{alert.get('link', '')}",
    ]
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
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
        print(f"  [warn] Telegram: {e}", file=sys.stderr)


# ── Core runner ───────────────────────────────────────────────────────────────

def reason_alert(alert: dict, dry_run: bool = False) -> dict:
    themes      = alert.get("themes") or []
    precedents  = fetch_precedents(themes) if not dry_run else []
    market_prob = _market_implied_prob(alert)

    ensemble = run_ensemble(alert, precedents)
    experts  = run_expert_lenses(alert, market_prob)
    gap_info = compute_gap(ensemble["ensemble_prob"], experts["expert_avg"], market_prob)

    result = {
        **ensemble,
        "expert_avg_prob": experts["expert_avg"],
        "expert_spread":   experts.get("expert_spread", 0),
        "expert_probs":    experts.get("expert_probs", {}),
        "market_prob":     market_prob,
        **gap_info,
        "precedents_used": len(precedents),
    }

    print(
        f"  gap={result['gap']:+.0f}pts ({result['gap_strength']}) "
        f"ensemble={result['ensemble_prob']}% expert={result['expert_avg_prob']}% "
        f"market={market_prob}%"
    )

    if result["should_alert"]:
        msg = _format_alert(alert, result)
        if not dry_run:
            send_telegram(msg)
        else:
            print(f"  [dry-run] would send:\n{msg}\n")

    if not dry_run:
        store_reasoning(alert["id"], result)

    return result


def run_pending(dry_run: bool = False) -> None:
    alerts = fetch_pending_alerts()
    print(f"{len(alerts)} alerts pending reasoning.")
    for alert in alerts:
        print(f"[{alert['id']}] {alert['title'][:80]}")
        try:
            reason_alert(alert, dry_run=dry_run)
        except Exception as e:
            print(f"  [error] {e}", file=sys.stderr)


# ── Test mode ─────────────────────────────────────────────────────────────────

def run_test() -> None:
    print("=== TEST MODE ===\n")
    fake = {
        "id":      0,
        "title":   "Anthropic launches Claude design tool — Adobe Figma threatened",
        "source":  "TechCrunch",
        "tier":    2,
        "score":   8,
        "themes":  ["ai_product_launch", "competitor_threat"],
        "tickers": ["ADBE", "FIGMA", "WIX"],
        "market":  "Will Adobe stock fall >5% by end of month?",
        "link":    "https://techcrunch.com/example",
    }
    print(f"Alert: {fake['title']}")
    print(f"Themes: {fake['themes']}")
    print(f"Tickers: {fake['tickers']}")

    market_prob = 50.0
    # simulate ensemble & expert with dummy probs (no API calls)
    ensemble_prob = 72.0
    expert_avg    = 65.0
    gap_info = compute_gap(ensemble_prob, expert_avg, market_prob)

    print(f"\nSimulated ensemble prob: {ensemble_prob}%")
    print(f"Simulated expert avg:    {expert_avg}%")
    print(f"Market implied:          {market_prob}%")
    print(f"Combined:                {gap_info['combined_prob']}%")
    print(f"Gap:                     {gap_info['gap']:+.0f}pts ({gap_info['gap_strength']})")
    print(f"Should alert:            {gap_info['should_alert']}")

    fake_result = {
        **gap_info,
        "ensemble_prob":   ensemble_prob,
        "expert_avg_prob": expert_avg,
        "market_prob":     market_prob,
        "uncertain":       False,
        "disagreement":    7.0,
    }
    print("\n--- Formatted alert ---")
    print(_format_alert(fake, fake_result))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Claude reasoning engine")
    parser.add_argument("--test",    action="store_true", help="Dry run with fake data")
    parser.add_argument("--pending", action="store_true", help="Reason on pending signals")
    parser.add_argument("--dry-run", action="store_true", help="Process without writing to DB")
    args = parser.parse_args()

    if args.test:
        run_test()
        return

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_KEY required.", file=sys.stderr)
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY required.", file=sys.stderr)
        sys.exit(1)

    if args.pending:
        run_pending(dry_run=args.dry_run)
    else:
        print("No action specified. Use --pending to process alerts or --test for a dry run.")


if __name__ == "__main__":
    main()
