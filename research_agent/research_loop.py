from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from .prompting import build_user_task, resolve_agent_prompt
from .router import get_adapter
from .schemas import (
    AgentResponse,
    AgentSource,
    PlanResponse,
    PlanQuery,
    ReflectionResponse,
    SynthesisResponse,
)
from .tools.web_search import search_web


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

DEFAULT_MAX_ITERS = 2
DEFAULT_MAX_QUERIES = 6
DEFAULT_MAX_SOURCES = 5

BASIC_SYSTEM_PROMPT = "You are a helpful research assistant. Return JSON only."


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _render_prompt(template: str, *, task: str, sources: str = "") -> str:
    return (
        template.replace("{{TASK}}", task)
        .replace("{{SOURCES}}", sources)
    )


def _parse_json(content: str) -> Dict:
    return json.loads(content)


def _to_agent_error(reason: str, raw: Optional[dict] = None) -> AgentResponse:
    return AgentResponse(
        summary=f"ERROR: {reason}",
        key_findings=[],
        recommendations=[],
        risks=[reason],
        open_questions=[],
        sources=[],
        raw=raw,
    )


def _format_sources_for_reflection(sources: List[AgentSource]) -> str:
    if not sources:
        return "(no sources)"
    lines = []
    for source in sources:
        lines.append(f"- {source.title} ({source.location})")
    return "\n".join(lines)


def _assign_source_ids(sources: List[AgentSource]) -> List[Dict[str, str]]:
    assigned = []
    for idx, source in enumerate(sources, start=1):
        assigned.append(
            {
                "id": f"[{idx}]",
                "title": source.title,
                "type": source.type,
                "location": source.location,
            }
        )
    return assigned


def _format_sources_for_synthesis(sources: List[AgentSource]) -> str:
    if not sources:
        return "(no sources)"
    assigned = _assign_source_ids(sources)
    lines = []
    for source in assigned:
        lines.append(
            f"{source['id']} {source['title']}\n{source['type']}\n{source['location']}\n"
        )
    return "\n".join(lines)


def _dedupe_sources(sources: List[AgentSource]) -> List[AgentSource]:
    seen = set()
    deduped: List[AgentSource] = []
    for source in sources:
        key = source.location
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped


def run_research(
    task: str,
    *,
    agent_name: Optional[str] = None,
    agent_id: Optional[str] = None,
    max_iters: Optional[int] = None,
    max_queries: Optional[int] = None,
    max_sources: Optional[int] = None,
) -> AgentResponse:
    if not task:
        return _to_agent_error("task is required")

    max_iters = max_iters or int(os.environ.get("RESEARCH_MAX_ITERS", str(DEFAULT_MAX_ITERS)))
    max_queries = max_queries or int(
        os.environ.get("RESEARCH_MAX_QUERIES", str(DEFAULT_MAX_QUERIES))
    )
    max_sources = max_sources or int(
        os.environ.get("RESEARCH_MAX_SOURCES", str(DEFAULT_MAX_SOURCES))
    )

    agent_prompt = resolve_agent_prompt(agent_name, agent_id)
    user_task = build_user_task(agent_prompt, task)

    adapter = get_adapter()

    plan_template = _load_prompt("plan_prompt.txt")
    reflect_template = _load_prompt("reflect_prompt.txt")
    synth_template = _load_prompt("synthesize_prompt.txt")

    all_sources: List[AgentSource] = []
    queries: List[PlanQuery] = []

    for _ in range(max_iters):
        # Plan
        plan_prompt = _render_prompt(plan_template, task=user_task)
        plan_result = adapter.chat(
            system=BASIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": plan_prompt}],
            temperature=0.2,
            force_json=True,
        )
        try:
            plan_payload = _parse_json(plan_result.get("content", ""))
            plan = PlanResponse(**plan_payload)
        except Exception:
            return _to_agent_error("plan phase returned invalid JSON", plan_result.get("raw"))

        queries = plan.queries[:max_queries]

        # Search
        for query in queries:
            results = search_web(query.query, limit=max_sources)
            all_sources.extend(results)

        all_sources = _dedupe_sources(all_sources)

        # Reflect
        reflect_sources = _format_sources_for_reflection(all_sources)
        reflect_prompt = _render_prompt(reflect_template, task=user_task, sources=reflect_sources)
        reflect_result = adapter.chat(
            system=BASIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": reflect_prompt}],
            temperature=0.2,
            force_json=True,
        )
        try:
            reflect_payload = _parse_json(reflect_result.get("content", ""))
            reflection = ReflectionResponse(**reflect_payload)
        except Exception:
            return _to_agent_error(
                "reflection phase returned invalid JSON", reflect_result.get("raw")
            )

        if reflection.sufficient:
            break

        if reflection.new_queries:
            queries = reflection.new_queries[:max_queries]
        else:
            break

    # Synthesize
    synth_sources = _format_sources_for_synthesis(all_sources)
    synth_prompt = _render_prompt(synth_template, task=user_task, sources=synth_sources)
    synth_result = adapter.chat(
        system=BASIC_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": synth_prompt}],
        temperature=0.2,
        force_json=True,
    )
    try:
        synth_payload = _parse_json(synth_result.get("content", ""))
        synthesis = SynthesisResponse(**synth_payload)
    except Exception:
        return _to_agent_error("synthesis phase returned invalid JSON", synth_result.get("raw"))

    id_map = {entry["id"]: entry for entry in _assign_source_ids(all_sources)}
    citations: List[AgentSource] = []
    for citation in synthesis.citations:
        if citation.id in id_map:
            entry = id_map[citation.id]
            citations.append(
                AgentSource(
                    title=entry["title"],
                    type=entry["type"],
                    location=entry["location"],
                )
            )

    return AgentResponse(
        summary=synthesis.answer,
        key_findings=[],
        recommendations=[],
        risks=[],
        open_questions=[],
        sources=citations,
        raw=synth_result.get("raw"),
    )
