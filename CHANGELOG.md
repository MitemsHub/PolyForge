# Changelog

## 0.9.0

- Phase 9: Comprehensive pytest suite across config, risk, scanner, agents, execution, backtesting, and orchestrator.
- Added end-to-end dry-run system test runner and pre-launch validation checklist.
- Added strategy presets (conservative/balanced/aggressive) with explicit opt-in application.
- Hardened time handling to use timezone-aware UTC timestamps by default.

## 0.8.0

- Phase 8: Security hardening, audit logging, containerization, and production deployment scaffolding.
- Wallet modes (hot/proxy/cold), minimum balance gating, and client-side API rate limiting.
- Hash-chained audit log for decisions and execution events.
- Docker multi-stage build and compose setup for scheduler + dashboard.

## 0.7.0

- Phase 7: Advanced backtesting + optimization engine with reports.
- Walk-forward + Monte Carlo helpers, caching to DuckDB, and parameter search utilities.

## 0.6.0

- Phase 6: Read-only Streamlit dashboard for real-time visibility into cycles, signals, and portfolio.
- Telemetry persisted to DuckDB for dashboard queries.

## 0.5.0

- Phase 5: Orchestration layer (scheduler + orchestrator) tying scanner → agents → risk → executor.
- Improved alerting and durable telemetry primitives.

## 0.4.0

- Phase 4: Execution engine with dry-run previews and multiple live trading safety gates.
- Emergency cancel-all and execution documentation.

## 0.3.0

- Phase 3: LangGraph/LangChain agent workflow with mock LLM support and structured outputs.

## 0.2.0

- Phase 2: Risk engine, market scanner, DuckDB portfolio persistence, and basic backtesting.

## 0.1.0

- Phase 1: Project foundation, typed settings, API clients, logging, and main entrypoint.
