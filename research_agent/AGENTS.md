# AGENTS.md

## Project intent
- Build a read-only research agent that collaborates with Codex.
- Keep tooling safe, minimal, and auditable.

## Agent activation (Codex TUI)
- Manage and switch named agents via `/agents`.
- For a one-off override, use `base_instructions`.
- Use `/status` to confirm the active agent and full prompt in the "Agent" section.

## Agent storage
- Agents are stored in `agents.json` as `{ id, name, prompt }` records.
- Agent store: `Structure/research_agent/agents.json`.

## Operating constraints
- Read-only behavior: never modify, create, or delete files during analysis.
- Return structured JSON outputs per the system prompt.

## When uncertain
- Ask for clarification in `open_questions`.
- Document assumptions in `risks`.
