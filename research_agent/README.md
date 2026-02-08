# research_agent

Read-only research agent that collaborates with Codex.

## What it does
- Runs research tasks with safe, auditable tooling
- Returns structured JSON outputs
- Avoids file writes during analysis

## Setup (from repo root)
```bash
# Ensure uv is available
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  source "$HOME/.local/bin/env"
fi

# Create project-local venv and install deps
uv venv
uv pip install -r research_agent/requirements.txt
```

## Run
```bash
OLLAMA_MAX_TOKENS=1600 uv run uvicorn research_agent.server:APP --host 127.0.0.1 --port 8000
```
