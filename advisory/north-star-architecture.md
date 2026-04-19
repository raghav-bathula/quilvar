# Quilvar — North Star Architecture

> Reference: "$0 AI Architecture Stack — 2026 Edition" by Brij Kishore Pandey
> Adapted for Quilvar's stock signal agent use case.

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
│   LangGraph — models alert lifecycle as an explicit state machine:  │
│   fetch → classify → store → deduplicate → alert → validate         │
│   Enables conditional routing, retries, and parallel feed scanning  │
└──────┬───────────────────────────────────────────┬──────────────────┘
       │                                           │
┌──────▼──────────┐                    ┌───────────▼──────────────────┐
│  OBSERVABILITY  │                    │         LLM LAYER            │
│  Phoenix        │                    │  Claude Haiku — classifier   │
│  (self-hosted)  │                    │  Claude Sonnet — reasoning   │
│  Per-run metrics│                    │  GPT-4o mini — ensemble      │
│  Feed health    │                    │  Gemini Flash — ensemble     │
│  Alert delivery │                    │  (local Ollama: future       │
│  rates          │                    │   cost optimization only)    │
└─────────────────┘                    └───────────┬──────────────────┘
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
│   DuckDB (local) — future analytical queries                        │
│   "Which ticker clusters in weak_signals predicted score-7          │
│    alerts 2 hours later?" — OLAP query, not suited for Postgres     │
└──────────────────────────────────────────────────┬──────────────────┘
                                                   │
┌──────────────────────────────────────────────────▼──────────────────┐
│                      TOOL USE VIA MCP                               │
│   Expose Quilvar capabilities as MCP tools:                         │
│   - search_signals(ticker, date_range)                              │
│   - query_validation_history(horizon)                               │
│   - trigger_scan(stream)                                            │
│   - get_calibration_report()                                        │
│   Enables: Claude Code to query live signal data during dev,        │
│   future agents to consume Quilvar as a tool                        │
└──────────────────────────────────────────────────┬──────────────────┘
                                                   │
┌──────────────────────────────────────────────────▼──────────────────┐
│                      DEPLOYMENT LAYER                               │
│   GitHub Actions — scheduling (scan, validate, report)  ✅ EXISTS   │
│   Docker — future, if reasoning engine needs more compute           │
│   Cloudflare Workers — future, if webhook-driven ingest added       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## What We Have vs What We're Building Toward

| Component | North Star | Today | Priority |
|---|---|---|---|
| Frontend | Streamlit on HuggingFace Spaces | Telegram alerts only | Month 3 |
| Orchestrator | LangGraph state machine | Hand-rolled `run_scan()` loop | Month 4 |
| Observability | Phoenix / structured job metrics | `print()` statements | Month 2 |
| RAG Pipeline | **Not needed for Quilvar** | N/A | Skip |
| LLM Layer | Haiku + Sonnet + ensemble | Haiku only (classifier) | Month 5 |
| Local LLMs | Ollama (cost optimization) | API only | Month 6+ |
| Tool Use | MCP server | Direct function calls | Month 4 |
| Data Layer | Supabase + DuckDB | Supabase | Month 5 |
| App State | Supabase | Supabase | ✅ Done |
| Deployment | GitHub Actions + Docker | GitHub Actions | Month 6+ |
| Code Agent | Claude Code | Claude Code | ✅ Done |

---

## What We Are Skipping From the Reference Stack

**RAG Pipeline (LlamaIndex + ChromaDB/Qdrant):**
Not applicable. Quilvar ingests structured RSS feeds, not unstructured document corpora. Our "retrieval" is Supabase queries and live feed fetching. No vector database needed.

**Local LLMs (Ollama, Gemma, Llama, Mistral):**
Our Haiku classification cost is ~$1-2/month. Local LLMs become relevant only if API costs become meaningful at scale, or if data sovereignty is required. Revisit at month 6+.

---

## Build Sequence

```
Now          → Harden pipeline (data integrity, observability basics)
Month 2      → Structured job logging to Supabase (feed health, latency, error rates)
Month 3      → Streamlit dashboard — calibration charts, signal history
Month 4      → Activate claude_reasoning.py (needs: calibration > 40%, live market prices)
Month 4      → MCP server exposing Quilvar tools
Month 5      → LangGraph orchestration (if reasoning logic gets complex enough)
Month 5      → DuckDB for pattern analysis on weak_signals
Month 6+     → Docker, Cloudflare Workers, local LLM evaluation
```

---

## The One Thing the Reference Stack Gets Right for Us

> *"The strategic asset is the validation loop."*

The reference diagram shows a complete sense → act → observe cycle. Quilvar's version:

```
RSS feeds → Haiku classify → Supabase store → Telegram alert
                                    ↓
              signal_validator.py (1d / 7d / 30d price outcomes)
                                    ↓
              calibration_report() → precision metrics
                                    ↓
              (future) reasoning engine uses history to improve scoring
```

The feedback loop is built. The rest of the north star stack is infrastructure around it.

---

*Last updated: April 2026*
*Reference: "$0 AI Architecture Stack — 2026 Edition" by Brij Kishore Pandey*
