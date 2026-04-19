# Quilvar — North Star Architecture

> Reference: "$0 AI Architecture Stack — 2026 Edition" by Brij Kishore Pandey
> Adapted for Quilvar's stock signal agent use case.
>
> **Framing:** Trust-first, not stack-first. State integrity and validation
> truth come before orchestration frameworks and tooling layers.

---

## Target Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FRONTEND LAYER                              │
│   Streamlit dashboard (calibration charts, signal history,          │
│   alert feed, precision metrics)                                    │
│   Deployed on: HuggingFace Spaces (free)                            │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────────┐
│                      AGENT ORCHESTRATOR                             │
│   LangGraph — models alert lifecycle as an explicit state machine   │
│   Only worth adopting after the underlying state model is correct.  │
│   Swapping the loop before fixing state = architecture theater.     │
└──────┬───────────────────────────────────────────┬──────────────────┘
       │                                           │
┌──────▼──────────┐                    ┌───────────▼──────────────────┐
│  OBSERVABILITY  │                    │         LLM LAYER            │
│  Phase 1:       │                    │  Claude Haiku — classifier   │
│  Structured job │                    │  Claude Sonnet — reasoning   │
│  logs in        │                    │  GPT-4o mini — ensemble      │
│  Supabase       │                    │  Gemini Flash — ensemble     │
│  (feed health,  │                    │                              │
│  delivery rate, │                    │  ⚠️ Reasoning engine blocked: │
│  error counts)  │                    │  _market_implied_prob()      │
│                 │                    │  hardcoded to 50.0 —         │
│  Phase 2:       │                    │  gap/gap_strength/           │
│  Phoenix        │                    │  should_alert are not        │
│  (self-hosted)  │                    │  meaningful until wired to   │
│  after state    │                    │  live Polymarket prices.     │
│  transitions    │                    │  (claude_reasoning.py:343)   │
│  are stable     │                    └───────────┬──────────────────┘
└─────────────────┘                               │
                                                   │
┌──────────────────────────────────────────────────▼──────────────────┐
│                         DATA LAYER                                  │
│                                                                     │
│   Supabase (Postgres) — primary store            ✅ EXISTS          │
│   ├── alerts          (score ≥ 4 signals)        ✅ EXISTS          │
│   ├── weak_signals    (score 1-3, patterns)      ✅ EXISTS          │
│   ├── seen_urls       (dedup, 7-day TTL)         ✅ EXISTS          │
│   └── signal_config   (key-value config)         ✅ EXISTS          │
│                                                                     │
│   DuckDB (local) — future OLAP queries on weak_signals              │
└──────────────────────────────────────────────────┬──────────────────┘
                                                   │
┌──────────────────────────────────────────────────▼──────────────────┐
│                   TOOL USE VIA MCP  (deferred)                      │
│   Good leverage layer once data model and auth boundaries are        │
│   stable. Premature before core operational trust is established.   │
└──────────────────────────────────────────────────┬──────────────────┘
                                                   │
┌──────────────────────────────────────────────────▼──────────────────┐
│                      DEPLOYMENT LAYER                               │
│   GitHub Actions — scheduling (scan, validate, report)  ✅ EXISTS   │
│   Docker / Cloudflare Workers — revisit at month 6+                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## What We Have vs What We're Building Toward

| Component | North Star | Today | When |
|---|---|---|---|
| Observability | Structured job logs → Phoenix | `print()` statements | Month 2 |
| Frontend | Streamlit on HuggingFace Spaces | Telegram push only | Month 3 |
| Live market prices | Polymarket yes-price at scan time | Hardcoded 50% placeholder | Prerequisite for reasoning |
| Reasoning engine | `claude_reasoning.py` with real gap | Built, not activatable | After: calibration > 40% + live prices + trustworthy history |
| Orchestrator | LangGraph (after state model is correct) | Hand-rolled loop | After: state transitions are stable |
| Tool use | MCP server | Direct function calls | After: data model and auth are stable |
| LLM ensemble | Haiku + Sonnet + GPT-4o + Gemini | Haiku only | After: reasoning engine activated |
| Data — analytics | DuckDB for weak_signal pattern queries | Supabase only | Month 5+ |
| RAG Pipeline | **Not needed for Quilvar** | N/A | Skip |
| Local LLMs | Ollama (cost optimization only) | API | Month 6+ |
| Deployment | GitHub Actions + Docker | GitHub Actions | Month 6+ |
| Code Agent | Claude Code | Claude Code | ✅ Done |
| App State | Supabase | Supabase | ✅ Done |

---

## What We Are Skipping From the Reference Stack

**RAG Pipeline (LlamaIndex + ChromaDB/Qdrant):**
Not applicable. Quilvar ingests structured RSS feeds, not unstructured document corpora.
Our "retrieval" is Supabase queries and live feed fetching. No vector database needed.

**Local LLMs (Ollama, Gemma, Llama, Mistral):**
Haiku classification costs ~$1-2/month. Local LLMs become relevant only if API costs
grow meaningfully or data sovereignty is required. Revisit at month 6+.

---

## Build Sequence — Trust-First

```
Now        → Complete pipeline hardening
             - Alert lifecycle: stored → eligible → delivered → validated
             - Dedup semantics correct (done: Phase 1 write-before-seen)
             - Validation truth: 30 days of clean 1d/7d/30d outcomes

Month 2    → Structured observability (no Phoenix yet)
             - Write job metrics to Supabase: feed_name, entries_parsed,
               alerts_stored, delivery_success, errors, duration_ms
             - Distinguish "quiet market" from "broken pipeline"

Month 3    → Streamlit dashboard
             - Calibration precision over time
             - Signal history by ticker/theme
             - Feed health from job metrics table

Gated      → Wire _market_implied_prob() to live Polymarket yes-price
             - Store market price at scan time in alerts table
             - Until done: reasoning engine outputs are illustrative only

Gated      → Activate claude_reasoning.py
             Prerequisites (all must be true):
             1. calibration precision > 40% at 7d or 30d
             2. _market_implied_prob() wired to real prices
             3. 30+ days of trustworthy validated history
             4. Posture remains advisory-only

Month 5+   → LangGraph orchestration
             Only after state transitions (store → deliver → validate)
             are correct and stable. Replacing the loop is the last step,
             not a step that fixes the loop.

Month 5+   → MCP server
             After data model, alert lifecycle, and auth boundaries
             are stable and auditable.

Month 6+   → Docker, Cloudflare Workers, local LLM evaluation
```

---

## The Feedback Loop — Honest State

The reference diagram shows a complete sense → act → observe cycle.
Quilvar's version exists but is not yet closed:

```
RSS feeds → Haiku classify → Supabase store → Telegram alert
                                    ↓
              signal_validator.py (1d / 7d / 30d price outcomes)   ← running
                                    ↓
              calibration_report() → precision metrics              ← running, no data yet
                                    ↓
              reasoning engine uses history to improve scoring      ← BLOCKED
```

**What's blocking closure:**
- `_market_implied_prob()` returns `50.0` (hardcoded) — `gap`, `gap_strength`,
  `should_alert` in DB are computed against this and are not decision-grade.
  *(claude_reasoning.py:343)*
- No validated history yet — pipeline is ~days old, not 30 days.
- Until both are resolved, the loop exists structurally but does not close
  in any analytically meaningful sense.

The loop is the right architecture. It is not yet the right output.

---

*Last updated: April 2026*
*Reference: "$0 AI Architecture Stack — 2026 Edition" by Brij Kishore Pandey*
