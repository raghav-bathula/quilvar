# Hardening Plan

## Objective

Make Quilvar reliable enough for internal pilot use and trustworthy enough to evaluate signal quality without silent failures or misleading history.

## Phase 1: Data Integrity

Focus:
- ensure alerts are only marked as seen after durable persistence
- make inserts and retries idempotent
- remove silent data-loss paths

Expected outcome:
- a failed write or delivery attempt does not permanently drop a signal

## Phase 2: Schema and State Model

Focus:
- establish one authoritative schema and migration path
- align runtime code with database columns and tables
- define a clear alert lifecycle: stored, eligible, attempted, delivered, validated

Expected outcome:
- no ambiguity between repo schema, deployed schema, and runtime assumptions

## Phase 3: Deduplication

Focus:
- separate URL dedupe from “same story” alert suppression
- make stream boundaries explicit between equity and crypto
- ensure recent-alert suppression only applies to alerts actually delivered

Expected outcome:
- fewer duplicate alerts without suppressing valid new signals

## Phase 4: Validation Semantics

Focus:
- correct top-level outcome modeling across 1d, 7d, and 30d
- ensure historical precedents reflect real outcomes rather than first-horizon artifacts
- make validation outputs auditable

Expected outcome:
- precedent analysis and weekly reports use trustworthy ground truth

## Phase 5: Observability

Focus:
- structured logging for feed failures, DB failures, and notification failures
- job-level success and latency tracking
- anomaly detection for missing feeds or unusual drops in alert volume

Expected outcome:
- the team can distinguish “quiet market” from “broken pipeline”

## Phase 6: Real Market Data

Focus:
- replace placeholder market-implied probability in the reasoning layer
- store market prices at scan time
- prevent gap analysis from being treated as decision-grade until live prices are integrated

Expected outcome:
- reasoning outputs become analytically meaningful rather than illustrative only

## Testing Priorities

Add targeted tests for:
- dedupe behavior
- retry/idempotency behavior
- outcome progression across multiple horizons
- schema compatibility
- alert lifecycle transitions
- fallback behavior when external providers fail

## Suggested Sequence

1. freeze feature expansion
2. fix persistence ordering and retry semantics
3. unify schema and alert state
4. correct dedupe logic
5. fix validation outcome model
6. add observability and targeted tests
7. integrate live market prices

## Management View

This is likely one focused hardening sprint for a strong engineer, or one to two weeks depending on migration discipline and test coverage expectations.
