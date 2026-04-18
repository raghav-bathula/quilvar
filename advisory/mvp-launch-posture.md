# MVP Launch Posture

## Recommendation

Launch Quilvar as an internal pilot in shadow mode, positioned as decision-support only.

Do not present it yet as a prediction engine, trading edge, or automated execution system. The credible MVP is an AI-assisted signal intelligence system that detects market-relevant events, structures them, alerts humans, and measures what actually mattered over time.

## MVP Scope

The MVP should do the following reliably:
- ingest trusted news and filing sources
- score and store structured signals
- send human-readable alerts for the highest-value events
- validate outcomes over 1d, 7d, and 30d horizons
- produce a weekly calibration report
- keep all outputs advisory, with no automated action

## Launch Posture

- Users: internal team only, or a very small design-partner group
- Promise: faster signal detection and structured monitoring
- Non-promise: no claims of predictive accuracy, alpha, or automated decisioning
- Success criterion: trustworthiness and workflow usefulness, not model sophistication

## Success Metrics

### Reliability

- feed/job success rate: `>99%`
- alert delivery success rate: `>98%`
- duplicate alert rate: `<5%`
- silent failure rate: `0` known data-loss paths
- median ingestion-to-alert latency: `<10 minutes`

### Signal Quality

- percent of stored signals judged relevant by human review: `>60%`
- percent of alerted signals judged worth reading: `>75%`
- precision of validated alerts at 7d or 30d: target `>40%` to start
- false-positive rate on top-tier alerts: trending down week over week

### Workflow Value

- alert volume stays in a usable daily band
- percent of alerts that lead to analyst follow-up
- time saved versus manual monitoring
- weekly examples where the system surfaced something the team would likely have missed or found later

## Readiness Gates Before Broader Launch

- data integrity issues closed
- schema and migrations unified
- dedupe behavior stable
- outcome labeling corrected
- alert lifecycle fully auditable
- real market-price integration before marketing the gap feature as decision support

## Executive Summary

The right first launch is an internal intelligence copilot. Measure reliability and trust first. Earn the right to make stronger claims later through validated outcomes.
