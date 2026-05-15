# Roadmap

This roadmap outlines future improvements beyond the current release.

## Trading & Strategy

- Advanced market making (inventory-aware quoting, dynamic spreads, adverse selection filters).
- Cross-market and cross-event arbitrage with tighter execution constraints.
- On-chain analytics (funding flows, wallet clustering, MEV-aware execution heuristics).
- Strategy ensembles with capital allocation and correlation-aware risk budgets.

## Agents & LLMs

- Additional LLM providers (Anthropic/Groq) with strict structured output enforcement.
- Tool-calling for controlled external research with allowlists and caching.
- Human-in-the-loop approval workflows and replayable decision traces.

## Risk & Safety

- Scenario stress testing and regime detection.
- Hardening of balance and withdrawal controls with multi-approval workflows.
- External audit log shipping (append-only object storage, SIEM integration).

## Infrastructure

- Postgres-backed high-scale telemetry (optional in compose profile).
- Redis caching for market data and agent results.
- Observability stack templates (Prometheus/Grafana, Loki, OpenTelemetry).

## Developer Experience

- CI pipeline (lint + pytest + container build).
- Pre-commit hooks and deterministic formatting.
