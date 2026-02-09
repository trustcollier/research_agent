from __future__ import annotations

import json
import os
from typing import Dict, List


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


def list_agents(path: str = AGENTS_PATH) -> List[Dict[str, str]]:
    return load_agents(path)
