from __future__ import annotations

import os
from typing import List

import requests

from ..schemas import AgentSource


def search_web(query: str, limit: int = 5) -> List[AgentSource]:
    api_key = os.environ.get("SERPAI_KEY", "")
    if not api_key:
        return []
    params = {"q": query, "engine": "google", "api_key": api_key}
    resp = requests.get("https://serpapi.com/search.json", params=params, timeout=20)
    if not resp.ok:
        return []
    data = resp.json()
    results = data.get("organic_results", [])
    sources: List[AgentSource] = []
    for item in results[:limit]:
        title = item.get("title") or ""
        link = item.get("link") or ""
        if not link:
            continue
        sources.append(AgentSource(title=title, type="web", location=link))
    return sources
