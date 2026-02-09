# Structure Project Overview

## Purpose
This repo contains a read-only research agent service under `research_agent/`. It exposes a simple HTTP API for running research tasks with structured JSON output, and is designed to be safe, auditable, and deterministic.

## Key Capabilities
- **Research loop**: plan → search → reflect → synthesize.
- **Structured outputs**: JSON responses with citations.
- **Determinism**: fixed LLM parameters, cached search/LLM outputs, and persistent traces.
- **Safety**: read-only behavior; no file writes during analysis.

## Top-Level Layout
- `research_agent/` — core service implementation
- `AGENTS.md` — repo instructions
- `PROJECT_OVERVIEW.md` — this file

## How It Works (High-Level)
1. Accepts a task via `/run`.
2. Generates search queries.
3. Searches the web.
4. Reflects on sufficiency.
5. Synthesizes a final answer with citations.

## Reproducibility
- Every run is traceable with a stable `run_id` derived from the normalized task.
- Cached LLM and search results ensure repeated runs are consistent.

## Run (from repo root)
```bash
uv run uvicorn research_agent.server:APP --host 127.0.0.1 --port 8000
```

## API
- `POST /run` — run a research task and return structured results.
- `POST /search` — direct web search (optional tool call).

## Notes
- The research agent is intentionally minimal and read-only.
- External configuration is via environment variables in `research_agent/.env`.
