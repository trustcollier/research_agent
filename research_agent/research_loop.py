from __future__ import annotations

import hashlib
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
DEFAULT_MAX_QUERIES = 10
DEFAULT_MAX_SOURCES = 15
DEFAULT_MAX_RETRIES = 3
MIN_RESULTS_PER_QUERY = 3
DEFAULT_TOKEN_BUDGET = 120000
DEFAULT_COMPACT_KEEP_RECENT = 20
DEFAULT_COMPACT_BEFORE_SYNTH = False
DEFAULT_LLM_MAX_TOKENS = 800
TRACE_DIR = Path(__file__).resolve().parent.parent / "temporary" / "traces"
CACHE_DIR = Path(__file__).resolve().parent.parent / "temporary" / "cache"
LOW_QUALITY_DOMAINS = (
    "piechartmaker.com",
    "sqmagazine.co.uk",
    "aag-it.com",
)

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


def _ensure_dirs() -> None:
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_id(task: str) -> str:
    normalized = " ".join(task.strip().lower().split())
    return _hash_text(normalized)[:16]


def _cache_path(prefix: str, key: str) -> Path:
    return CACHE_DIR / f"{prefix}_{key}.json"


def _load_cache(prefix: str, key: str) -> Optional[dict]:
    path = _cache_path(prefix, key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(prefix: str, key: str, payload: dict) -> None:
    _cache_path(prefix, key).write_text(json.dumps(payload), encoding="utf-8")


def _llm_cache_key(*, system: str, prompt: str, model: str, temperature: float, max_tokens: int) -> str:
    key_payload = json.dumps(
        {
            "system": system,
            "prompt": prompt,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        sort_keys=True,
    )
    return _hash_text(key_payload)


def _search_cache_key(query: str, limit: int) -> str:
    key_payload = json.dumps({"query": query, "limit": limit}, sort_keys=True)
    return _hash_text(key_payload)


def _to_agent_error(reason: str, raw: Optional[dict] = None) -> AgentResponse:
    return AgentResponse(
        summary=f"ERROR: {reason}",
        key_findings=[],
        recommendations=[],
        risks=[reason],
        open_questions=[],
        sources=[],
        raw=raw,
        metadata=None,
    )


def _write_trace(trace: Dict[str, object]) -> None:
    if not trace.get("run_id"):
        return
    trace_path = TRACE_DIR / f"{trace['run_id']}.json"
    trace_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")


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


def _filter_sources(sources: List[AgentSource]) -> List[AgentSource]:
    filtered: List[AgentSource] = []
    for source in sources:
        location = (source.location or "").lower()
        if any(domain in location for domain in LOW_QUALITY_DOMAINS):
            continue
        filtered.append(source)
    return filtered


def _growth_required(task: str) -> bool:
    lowered = task.lower()
    return "growth" in lowered or "year-over-year" in lowered or "yoy" in lowered


def _growth_covered(sources: List[AgentSource]) -> bool:
    keywords = ("growth", "year-over-year", "yoy")
    for source in sources:
        title = (source.title or "").lower()
        location = (source.location or "").lower()
        if any(k in title for k in keywords) or any(k in location for k in keywords):
            return True
    return False


def _storage_focus_required(task: str) -> bool:
    lowered = task.lower()
    return "cloud storage" in lowered or "file sharing" in lowered or "storage providers" in lowered


def _infra_market_source(source: AgentSource) -> bool:
    text = f"{source.title} {source.location}".lower()
    infra_markers = (
        "cloud infrastructure",
        "iaas",
        "aws",
        "azure",
        "google cloud",
        "cloud platform",
    )
    return "market share" in text and any(marker in text for marker in infra_markers)


def _storage_focus_missing(sources: List[AgentSource]) -> bool:
    if not sources:
        return True
    return all(_infra_market_source(source) for source in sources)


def _growth_query_fallback(task: str) -> List[PlanQuery]:
    return [
        PlanQuery(
            query=f"{task} year-over-year growth rate 2024 2025 site:statista.com OR site:gartner.com OR site:idc.com",
            intent="find YoY growth rate data",
        ),
        PlanQuery(
            query="Dropbox Google Drive OneDrive growth rate 2024 YoY site:investors.dropbox.com OR site:microsoft.com OR site:alphabet.com",
            intent="find provider-specific growth figures",
        ),
        PlanQuery(
            query="cloud storage market growth rate by provider 2024 2025 site:statista.com OR site:gartner.com OR site:idc.com",
            intent="find market growth data by vendor",
        ),
        PlanQuery(
            query="file sharing software market share by vendor 2024 site:statista.com",
            intent="find vendor share data when storage share data is sparse",
        ),
        PlanQuery(
            query="Dropbox revenue growth 2024 year-over-year investor relations",
            intent="use IR data as a proxy if market-share YoY is unavailable",
        ),
    ]


def _storage_focus_query_fallback() -> List[PlanQuery]:
    return [
        PlanQuery(
            query="cloud storage market share consumer personal 2024 2025",
            intent="focus on consumer/personal cloud storage market share",
        ),
        PlanQuery(
            query="file sharing software market share by vendor 2024 site:statista.com",
            intent="use file-sharing vendor shares as proxy",
        ),
        PlanQuery(
            query="Dropbox Google Drive OneDrive market share personal cloud storage",
            intent="target provider-specific storage market share",
        ),
    ]


def _has_authoritative_sources(sources: List[AgentSource]) -> bool:
    domains = ("statista.com", "gartner.com", "idc.com", "techcrunch.com", "theverge.com", "bloomberg.com")
    for source in sources:
        location = (source.location or "").lower()
        if any(domain in location for domain in domains):
            return True
    return False


def _fallback_queries(task: str) -> List[PlanQuery]:
    return [
        PlanQuery(query=f"{task} market share 2024 2025", intent="core market share query"),
        PlanQuery(query=f"{task} year-over-year growth 2024 2025", intent="growth query"),
        PlanQuery(query=f"{task} statistics 2024 2025 site:statista.com", intent="authoritative source query"),
    ]


def _normalize_query(text: str) -> str:
    return " ".join(text.strip().split())


def _dedupe_sort_queries(queries: List[PlanQuery]) -> List[PlanQuery]:
    seen = set()
    unique: List[PlanQuery] = []
    for query in queries:
        normalized = _normalize_query(query.query)
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        unique.append(PlanQuery(query=normalized, intent=query.intent))
    return sorted(unique, key=lambda q: q.query.lower())


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

    _ensure_dirs()
    run_id = _run_id(task)
    trace: Dict[str, object] = {
        "run_id": run_id,
        "task": task,
        "stages": {},
        "queries": [],
        "sources": [],
        "reflections": [],
        "synthesis": None,
        "cache_hits": {"llm": [], "search": []},
    }

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
    llm_max_tokens = int(os.environ.get("RESEARCH_LLM_MAX_TOKENS", str(DEFAULT_LLM_MAX_TOKENS)))
    temperature = 0.0

    agent_prompt = resolve_agent_prompt(agent_name, agent_id)
    user_task = build_user_task(agent_prompt, task)

    adapter = get_adapter()
    model_name = getattr(adapter, "model", "unknown")

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
    growth_forced = False
    growth_required = _growth_required(user_task)
    storage_focus_forced = False
    storage_focus_required = _storage_focus_required(user_task)
    iterations_run = 0
    fallback_used = False
    executed_queries: List[str] = []

    for _ in range(max_iters):
        iterations_run += 1
        if degraded_mode:
            break
        if pending_queries:
            queries = _dedupe_sort_queries(pending_queries)
            pending_queries = None
        else:
            # Plan
            trace["stages"].setdefault("plan", []).append({"start": time.time()})
            plan_prompt = _render_prompt(plan_template, task=user_task)
            plan_cache_key = _llm_cache_key(
                system=BASIC_SYSTEM_PROMPT,
                prompt=plan_prompt,
                model=model_name,
                temperature=temperature,
                max_tokens=llm_max_tokens,
            )
            plan_cached = _load_cache("llm", plan_cache_key)
            if plan_cached:
                plan_result = plan_cached
                trace["cache_hits"]["llm"].append("plan")
            else:
                plan_result = adapter.chat(
                    system=BASIC_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": plan_prompt}],
                    temperature=temperature,
                    max_tokens=llm_max_tokens,
                    force_json=True,
                )
                _save_cache("llm", plan_cache_key, plan_result)
            trace["stages"]["plan"][-1]["end"] = time.time()
            try:
                plan_payload = _parse_json(plan_result.get("content", ""))
                plan = PlanResponse(**plan_payload)
            except Exception:
                plan = PlanResponse(queries=_fallback_queries(user_task))
                fallback_used = True
            if not plan.queries:
                plan = PlanResponse(queries=_fallback_queries(user_task))
                fallback_used = True

            queries = _dedupe_sort_queries(plan.queries)[:max_queries]

        # Search
        trace["stages"].setdefault("search", []).append({"start": time.time()})
        for query in queries:
            if degraded_mode:
                break
            executed_queries.append(query.query)
            total_queries += 1
            search_key = _search_cache_key(query.query, max_sources)
            cached_search = _load_cache("search", search_key)
            if cached_search is not None:
                results = [AgentSource(**item) for item in cached_search.get("results", [])]
                failed = cached_search.get("failed", False)
                trace["cache_hits"]["search"].append(query.query)
            else:
                results, failed = _search_with_retry(
                    query.query,
                    max_sources=max_sources,
                    error_log=error_log,
                )
                _save_cache(
                    "search",
                    search_key,
                    {
                        "results": [item.model_dump() for item in results],
                        "failed": failed,
                    },
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

            if results:
                results = _filter_sources(results)

            if consecutive_failures >= 3 or (total_queries >= 4 and failed_count / total_queries >= 0.5):
                degraded_mode = True
                break

        all_sources = _dedupe_sources(_filter_sources(all_sources))
        trace["stages"]["search"][-1]["end"] = time.time()

        # Reflect
        trace["stages"].setdefault("reflect", []).append({"start": time.time()})
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
        reflect_cache_key = _llm_cache_key(
            system=BASIC_SYSTEM_PROMPT,
            prompt=reflect_prompt,
            model=model_name,
            temperature=temperature,
            max_tokens=llm_max_tokens,
        )
        reflect_cached = _load_cache("llm", reflect_cache_key)
        if reflect_cached:
            reflect_result = reflect_cached
            trace["cache_hits"]["llm"].append("reflect")
        else:
            reflect_result = adapter.chat(
                system=BASIC_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": reflect_prompt}],
                temperature=temperature,
                max_tokens=llm_max_tokens,
                force_json=True,
            )
            _save_cache("llm", reflect_cache_key, reflect_result)
        trace["stages"]["reflect"][-1]["end"] = time.time()
        try:
            reflect_payload = _parse_json(reflect_result.get("content", ""))
            reflection = ReflectionResponse(**reflect_payload)
        except Exception:
            _write_trace(trace)
            return _to_agent_error(
                "reflection phase returned invalid JSON", reflect_result.get("raw")
            )
        trace["reflections"].append(reflection.model_dump())

        growth_missing = growth_required and not _growth_covered(all_sources)
        storage_missing = storage_focus_required and _storage_focus_missing(all_sources)
        if reflection.sufficient and storage_missing and not storage_focus_forced and not degraded_mode:
            storage_focus_forced = True
            pending_queries = _storage_focus_query_fallback()[:max_queries]
            continue
        if reflection.sufficient and growth_missing and not growth_forced and not degraded_mode:
            growth_forced = True
            pending_queries = _growth_query_fallback(user_task)[:max_queries]
            continue

        if reflection.sufficient or degraded_mode:
            break

        if reflection.new_queries:
            pending_queries = reflection.new_queries[:max_queries]
        else:
            break

    # Synthesize
    trace["stages"].setdefault("synthesize", []).append({"start": time.time()})
    synthesis_sources_list, synth_sources_text, synth_compacted, omitted_synth, _ = _build_synthesis_sources(
        all_sources,
        token_budget=token_budget,
        keep_recent=keep_recent,
        force_compact=force_compact_before_synth,
    )
    if synth_compacted and omitted_synth > 0:
        compacted_once = True
    synth_prompt = _render_prompt(synth_template, task=user_task, sources=synth_sources_text)
    synth_cache_key = _llm_cache_key(
        system=BASIC_SYSTEM_PROMPT,
        prompt=synth_prompt,
        model=model_name,
        temperature=temperature,
        max_tokens=llm_max_tokens,
    )
    synth_cached = _load_cache("llm", synth_cache_key)
    if synth_cached:
        synth_result = synth_cached
        trace["cache_hits"]["llm"].append("synthesize")
    else:
        synth_result = adapter.chat(
            system=BASIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": synth_prompt}],
            temperature=temperature,
            max_tokens=llm_max_tokens,
            force_json=True,
        )
        _save_cache("llm", synth_cache_key, synth_result)
    trace["stages"]["synthesize"][-1]["end"] = time.time()
    try:
        synth_payload = _parse_json(synth_result.get("content", ""))
        synthesis = SynthesisResponse(**synth_payload)
    except Exception:
        _write_trace(trace)
        return _to_agent_error("synthesis phase returned invalid JSON", synth_result.get("raw"))

    id_map = {entry["id"]: entry for entry in _assign_source_ids(synthesis_sources_list)}
    raw_citations = [c.model_dump() for c in synthesis.citations]
    citations, invalid_ids = _validate_citations(raw_citations, id_map)
    trace["queries"] = executed_queries
    trace["sources"] = [s.model_dump() for s in all_sources]
    trace["synthesis"] = synthesis.model_dump()

    risks: List[str] = []
    if degraded_mode:
        risks.append("Search degraded; answer may be based on partial information.")
    if compacted_once:
        risks.append("Some sources were omitted due to context budget limits.")
    if invalid_ids:
        risks.append("Some citations were invalid and were omitted.")
    if not _has_authoritative_sources(all_sources):
        risks.append("No top-tier analyst or major tech news sources found.")

    metadata = {
        "iterations": iterations_run,
        "model": model_name,
        "max_tokens": llm_max_tokens,
        "queries_count": len(executed_queries),
        "sources_count": len(all_sources),
        "forced_flags": {
            "fallback_query_used": fallback_used,
            "cache_hit": {
                "llm": trace["cache_hits"]["llm"],
                "search": trace["cache_hits"]["search"],
            },
        },
    }

    trace_path = TRACE_DIR / f"{run_id}.json"
    trace_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")

    return AgentResponse(
        summary=synthesis.answer,
        key_findings=[],
        recommendations=[],
        risks=risks,
        open_questions=[],
        sources=citations,
        raw=synth_result.get("raw"),
        metadata=metadata,
    )
