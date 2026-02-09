from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agents_store import list_agents


PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system_prompt.txt"


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def resolve_agent_prompt(agent_name: Optional[str], agent_id: Optional[str]) -> Optional[str]:
    if not agent_name and not agent_id:
        return None
    agents = list_agents()
    for agent in agents:
        if agent_id and agent.get("id") == agent_id:
            return agent.get("prompt")
        if agent_name and agent.get("name") == agent_name:
            return agent.get("prompt")
    return None


def build_user_task(agent_prompt: Optional[str], task: str) -> str:
    if agent_prompt:
        return f"{agent_prompt}\n\n{task}"
    return task
