from __future__ import annotations

import json
import os
import uuid
from typing import Dict, List, Optional


AGENTS_PATH = os.path.join(os.path.dirname(__file__), "agents.json")


def load_agents(path: str = AGENTS_PATH) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_agents(path: str, agents: List[Dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(agents, f, indent=2)


def create_agent(path: str, name: str, prompt: str) -> Dict[str, str]:
    agents = load_agents(path)
    agent = {"id": str(uuid.uuid4()), "name": name, "prompt": prompt}
    agents.append(agent)
    save_agents(path, agents)
    return agent


def get_agent(path: str, agent_id: str) -> Optional[Dict[str, str]]:
    return next((a for a in load_agents(path) if a.get("id") == agent_id), None)


def list_agents(path: str = AGENTS_PATH) -> List[Dict[str, str]]:
    return load_agents(path)


def update_agent(
    path: str,
    agent_id: str,
    *,
    name: Optional[str] = None,
    prompt: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    agents = load_agents(path)
    updated = None
    for agent in agents:
        if agent.get("id") == agent_id:
            if name is not None:
                agent["name"] = name
            if prompt is not None:
                agent["prompt"] = prompt
            updated = agent
            break
    if updated is None:
        return None
    save_agents(path, agents)
    return updated


def delete_agent(path: str, agent_id: str) -> bool:
    agents = load_agents(path)
    filtered = [a for a in agents if a.get("id") != agent_id]
    if len(filtered) == len(agents):
        return False
    save_agents(path, filtered)
    return True
