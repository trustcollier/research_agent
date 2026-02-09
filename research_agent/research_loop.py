from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
DEFAULT_MAX_RETRIES = 3
MIN_RESULTS_PER_QUERY = 3
DEFAULT_TOKEN_BUDGET = 50000
DEFAULT_COMPACT_KEEP_RECENT = 10
DEFAULT_COMPACT_BEFORE_SYNTH = True

BASIC_SYSTEM_PROMPT = "You are a helpful research assistant. Return JSON only."


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _render_prompt(
    template: str,
    *,
    task: str,
    sources: str = "",
    failed_queries: str = "",
) -> str:
    return (
        template.replace("{{TASK}}", task)
        .replace("{{SOURCES}}", sources)
        .replace("{{FAILED_QUERIES}}", failed_queries)
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


def _estimate_tokens(text: str) -> int:
    # Rough heuristic: ~4 chars per token
    return max(1, len(text) // 4)


def _compact_sources(
    sources: List[AgentSource],
    *,
    keep_recent: int,
) -> Tuple[List[AgentSource], int]:
    if len(sources) <= keep_recent:
        return sources, 0
    omitted = len(sources) - keep_recent
    return sources[-keep_recent:], omitted


def _build_reflection_sources(
    sources: List[AgentSource],
    *,
    token_budget: int,
    keep_recent: int,
) -> Tuple[str, bool, int, int]:
    full_text = _format_sources_for_reflection(sources)
    if _estimate_tokens(full_text) <= token_budget:
        return full_text, False, 0, token_budget - _estimate_tokens(full_text)

    compacted, omitted = _compact_sources(sources, keep_recent=keep_recent)
    compact_text = _format_sources_for_reflection(compacted)
    header = f"(omitted {omitted} older sources due to context budget)\n"
    final_text = header + compact_text if compact_text else header + "(no sources)"
    remaining = max(0, token_budget - _estimate_tokens(final_text))
    return final_text, True, omitted, remaining


def _build_synthesis_sources(
    sources: List[AgentSource],
    *,
    token_budget: int,
    keep_recent: int,
    force_compact: bool,
) -> Tuple[List[AgentSource], str, bool, int, int]:
    full_text = _format_sources_for_synthesis(sources)
    if not force_compact and _estimate_tokens(full_text) <= token_budget:
        return sources, full_text, False, 0, token_budget - _estimate_tokens(full_text)

    compacted, omitted = _compact_sources(sources, keep_recent=keep_recent)
    compact_text = _format_sources_for_synthesis(compacted)
    header = f"(omitted {omitted} older sources due to context budget)\n"
    final_text = header + compact_text if compact_text else header + "(no sources)"
    remaining = max(0, token_budget - _estimate_tokens(final_text))
    return compacted, final_text, True, omitted, remaining


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


def _validate_citations(
    citations: List[Dict[str, str]],
    id_map: Dict[str, Dict[str, str]],
) -> Tuple[List[AgentSource], List[str]]:
    valid: List[AgentSource] = []
    invalid: List[str] = []
    seen_locations = set()
    for citation in citations:
        citation_id = citation.get("id")
        if not citation_id or citation_id not in id_map:
            if citation_id:
                invalid.append(citation_id)
            continue
        entry = id_map[citation_id]
        location = entry.get("location")
        if location and location in seen_locations:
            continue
        if location:
            seen_locations.add(location)
        valid.append(
            AgentSource(
                title=entry["title"],
                type=entry["type"],
                location=entry["location"],
            )
        )
    return valid, invalid


def _search_with_retry(query: str, *, max_sources: int, error_log: List[Dict[str, str]]) -> Tuple[List[AgentSource], bool]:
    for attempt in range(DEFAULT_MAX_RETRIES):
        try:
            results = search_web(query, limit=max_sources)
            if results:
                return results, False
        except Exception as exc:
            error_log.append({"phase": "search", "query": query, "error": str(exc)})
        if attempt < DEFAULT_MAX_RETRIES - 1:
            sleep_time = min((2 ** attempt) + random.uniform(0, 1), 10)
            time.sleep(sleep_time)
    return [], True


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
    token_budget = int(os.environ.get("RESEARCH_TOKEN_BUDGET", str(DEFAULT_TOKEN_BUDGET)))
    keep_recent = int(
        os.environ.get("RESEARCH_COMPACT_KEEP_RECENT", str(DEFAULT_COMPACT_KEEP_RECENT))
    )
    force_compact_before_synth = (
        os.environ.get("RESEARCH_COMPACT_BEFORE_SYNTH", str(DEFAULT_COMPACT_BEFORE_SYNTH)).lower()
        == "true"
    )

    agent_prompt = resolve_agent_prompt(agent_name, agent_id)
    user_task = build_user_task(agent_prompt, task)

    adapter = get_adapter()

    plan_template = _load_prompt("plan_prompt.txt")
    reflect_template = _load_prompt("reflect_prompt.txt")
    synth_template = _load_prompt("synthesize_prompt.txt")

    all_sources: List[AgentSource] = []
    queries: List[PlanQuery] = []
    pending_queries: Optional[List[PlanQuery]] = None
    failed_queries: List[str] = []
    error_log: List[Dict[str, str]] = []
    consecutive_failures = 0
    total_queries = 0
    failed_count = 0
    degraded_mode = False
    compacted_once = False

    for _ in range(max_iters):
        if degraded_mode:
            break
        if pending_queries:
            queries = pending_queries
            pending_queries = None
        else:
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
                return _to_agent_error(
                    "plan phase returned invalid JSON", plan_result.get("raw")
                )

            queries = plan.queries[:max_queries]

        # Search
        for query in queries:
            if degraded_mode:
                break
            total_queries += 1
            results, failed = _search_with_retry(
                query.query,
                max_sources=max_sources,
                error_log=error_log,
            )
            if failed or not results:
                consecutive_failures += 1
                failed_count += 1
            else:
                consecutive_failures = 0
            if results:
                all_sources.extend(results)
                if len(results) < MIN_RESULTS_PER_QUERY:
                    failed_queries.append(query.query)
            else:
                failed_queries.append(query.query)

            if consecutive_failures >= 3 or (total_queries >= 4 and failed_count / total_queries >= 0.5):
                degraded_mode = True
                break

        all_sources = _dedupe_sources(all_sources)

        # Reflect
        reflect_sources, compacted, omitted_reflect, _ = _build_reflection_sources(
            all_sources,
            token_budget=token_budget,
            keep_recent=keep_recent,
        )
        if compacted and omitted_reflect > 0:
            compacted_once = True
        failed_block = "\n".join(f"- {q}" for q in failed_queries) if failed_queries else "(none)"
        reflect_prompt = _render_prompt(
            reflect_template,
            task=user_task,
            sources=reflect_sources,
            failed_queries=failed_block,
        )
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

        if reflection.sufficient or degraded_mode:
            break

        if reflection.new_queries:
            pending_queries = reflection.new_queries[:max_queries]
        else:
            break

    # Synthesize
    synthesis_sources_list, synth_sources_text, synth_compacted, omitted_synth, _ = _build_synthesis_sources(
        all_sources,
        token_budget=token_budget,
        keep_recent=keep_recent,
        force_compact=force_compact_before_synth,
    )
    if synth_compacted and omitted_synth > 0:
        compacted_once = True
    synth_prompt = _render_prompt(synth_template, task=user_task, sources=synth_sources_text)
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

    id_map = {entry["id"]: entry for entry in _assign_source_ids(synthesis_sources_list)}
    raw_citations = [c.model_dump() for c in synthesis.citations]
    citations, invalid_ids = _validate_citations(raw_citations, id_map)

    risks: List[str] = []
    if degraded_mode:
        risks.append("Search degraded; answer may be based on partial information.")
    if compacted_once:
        risks.append("Some sources were omitted due to context budget limits.")
    if invalid_ids:
        risks.append("Some citations were invalid and were omitted.")

    return AgentResponse(
        summary=synthesis.answer,
        key_findings=[],
        recommendations=[],
        risks=risks,
        open_questions=[],
        sources=citations,
        raw=synth_result.get("raw"),
    )
