# Research Loop Design (Temporary)

## 1. Goal
Define a concrete, minimal design to add an agentic research loop to the existing `research_agent` service: plan → search → reflect → iterate → synthesize with citations, while keeping `/run` as the main entrypoint.

## 2. Scope
- In scope: loop orchestration, structured schemas, prompt templates, `/run` wiring, citations from retrieved sources, operational safeguards.
- Out of scope: UI changes, external clients, MCP transport, new providers beyond current adapter (unless used as fallback search providers).

## 3. Architecture Overview
### 3.1 Components
- **Orchestrator**: `research_loop.py` drives the loop and owns state.
- **Schema layer**: typed models for plan, reflection, synthesis, and errors.
- **Prompt templates**: plan/reflect/synthesize prompts stored as files.
- **Search tool**: existing `tools/web_search.py` remains the side-effecting web tool.
- **Server**: `/run` calls the orchestrator and returns final structured output.

### 3.2 Data Flow
1) Receive user task.
2) Plan → generate search queries.
3) Search → run queries, collect sources.
4) Reflect → decide sufficiency or propose new queries.
5) Loop until sufficient or `max_iters`/`max_execution_time` reached.
6) Synthesize → answer using only retrieved sources; return citations.

## 4. Loop State (Serialized)
A single state object (dict or Pydantic model) passed through each step.

### 4.1 Fields
- `task: str`
- `plan_queries: list[PlanQuery]`
- `search_results: list[SearchResult]`
- `citations: list[AgentSource]`
- `iteration: int`
- `max_iters: int`
- `max_execution_time_s: int`
- `sufficient: bool`
- `confidence: float`
- `gaps: list[str]`
- `failed_queries: list[str]`
- `error_log: list[ErrorRecord]`
- `token_budget: int`

### 4.2 Invariants
- State is append-only for `search_results` and `citations`.
- `iteration` increments once per loop.
- `citations` derived only from retrieved sources.
- `sufficient` from reflection is the primary stopping condition.

## 5. Schema Contracts
### 5.1 PlanResponse
```
{
  "queries": [
    {
      "query": "string",
      "intent": "string"
    }
  ]
}
```

### 5.2 ReflectionResponse
```
{
  "sufficient": true|false,
  "confidence": 0.0,
  "gaps": ["string"],
  "new_queries": [
    {
      "query": "string",
      "intent": "string"
    }
  ],
  "metadata": {
    "timestamp": "string",
    "model": "string",
    "token_count": 0
  }
}
```

### 5.3 SynthesisResponse
```
{
  "answer": "string",
  "citations": [
    {
      "id": "[1]",
      "title": "string",
      "type": "string",
      "location": "string"
    }
  ],
  "metadata": {
    "timestamp": "string",
    "model": "string",
    "token_count": 0
  }
}
```

### 5.4 ErrorResponse
```
{
  "error": {
    "type": "string",
    "message": "string",
    "retryable": true|false
  }
}
```

### 5.5 External Response (AgentResponse)
- Keep `AgentResponse` for backward compatibility.
- Map `SynthesisResponse.answer` → `summary` or add a new `final_answer` field.
- `sources` populated only from `citations` that pass validation.

## 6. Prompt Templates
### 6.1 plan_prompt.txt
- Input: `task`
- Output: `PlanResponse`
- Constraints: 3–6 focused queries; include intent per query.

### 6.2 reflect_prompt.txt
- Input: `task`, `search_results` summary, `failed_queries`
- Output: `ReflectionResponse`
- Constraints: decide sufficiency; list gaps; propose new queries if not sufficient; for each query in `failed_queries` that returned <3 results, generate a reformulated version using synonyms, broader terms, or different angles.

### 6.3 synthesize_prompt.txt
- Input: `task`, `search_results` (with URLs + IDs)
- Output: `SynthesisResponse`
- Constraints: use only retrieved sources; cite by source ID only.

## 7. Orchestrator API
### 7.1 Function
```
run_research(task: str, *, max_iters: int, max_queries: int, max_sources: int, max_execution_time_s: int) -> AgentResponse
```

### 7.2 Steps (Pseudo)
```
state = init(task)
start = now()
while not state.sufficient and state.iteration < max_iters and (now() - start) < max_execution_time_s:
  plan = call_plan_llm(state.task)
  queries = trim(plan.queries, max_queries)
  results = search_all(queries, max_sources)
  state.append(results)
  reflection = call_reflect_llm(state.task, state.results)
  state.sufficient = reflection.sufficient
  state.confidence = reflection.confidence
  if not state.sufficient:
    state.plan_queries = reflection.new_queries
  state.iteration += 1
if state.iteration >= max_iters or (now() - start) >= max_execution_time_s:
  state.insufficient_termination = True
synthesis = call_synthesize_llm(state.task, state.results_with_ids, state.insufficient_termination)
return to_agent_response(synthesis)
```

## 8. Parameterization (Tiered Defaults)
Use task complexity tiers to set bounds; sufficiency is primary stop condition.

### 8.1 Tier Selection
- **Auto-detect**: LLM classifies task complexity in initial planning phase.
- **Explicit override**: request can specify tier via `complexity_tier` parameter.
- **Default**: use **Standard research** tier if unspecified.

### 8.2 Tier Definitions
- **Simple lookups**: 2 iterations, 3 queries, 5 sources
- **Standard research**: 5 iterations, 10 queries, 15 sources
- **Deep research**: 10+ iterations, 15+ queries, 20+ sources
- **All tiers**: `max_execution_time_s` = 60–120 seconds

## 9. State Compaction
- Use bounded list reducers to keep only N most recent results in active context.
- When token budget is exceeded, summarize older results and retain handles only.
- Store raw results externally and keep summaries + metadata in context.

### 9.1 Trigger Conditions
- When `state.token_budget` drops below 25% of initial allocation.
- OR when total state serialization exceeds 50K tokens.
- OR before each synthesis call to maximize output budget.

## 10. Error Handling (Tiered)
- **Tier 1 — Retry**: exponential backoff with jitter for transient errors; 2–3 attempts.
- **Tier 2 — Reformulate**: use reflection to create broader/synonym queries on empty results.
- **Tier 3 — Degrade**: circuit breaker after N failures; synthesize with uncertainty markers.
- **Tier 4 — Fallback providers**: switch search backend if primary fails.

### 10.1 Tier 1 — Retry Implementation
```python
max_retries = 3
for attempt in range(max_retries):
    try:
        return search_tool(query)
    except TransientError as e:
        if attempt == max_retries - 1:
            raise
        sleep_time = min(2 ** attempt + random.uniform(0, 1), 10)
        sleep(sleep_time)
        state.error_log.append({\"attempt\": attempt, \"error\": str(e)})
```

### 10.3 Tier 3 — Circuit Breaker
- Trigger after **3 consecutive failed search attempts** OR **50% of total queries failing**.
- Set `state.degraded_mode = True`.
- Synthesis prompt must include: \"Search capabilities were limited; answer is based on partial information.\"

## 11. Citation Verification
- **Source IDs**: assign `[1]`, `[2]`, … to retrieved sources; LLM must cite by ID only.
- **Existence check**: validate all cited IDs exist in retrieved set.
- **Semantic verification**: optional NLI/LLM check to verify claims are supported.

## 12. Server Integration
- `/run` calls `run_research` (not a single LLM call).
- `/search` remains available but is not required for clients to use.

## 13. Configuration
Environment defaults (override via env or request options):
- `RESEARCH_MAX_ITERS`
- `RESEARCH_MAX_QUERIES`
- `RESEARCH_MAX_SOURCES`
- `RESEARCH_MAX_EXECUTION_TIME_S`
- `RESEARCH_TOKEN_BUDGET`

## 14. Error Surfaces
- If planning/reflection/synthesis fails to parse, return structured `ErrorResponse`.
- Record errors in `error_log` for observability.

## 15. Testing (Smoke)
- Run `/run` with a simple task; assert JSON response schema.
- Force `max_iters=1` and ensure loop terminates.
- Simulate empty search results and ensure graceful fallback.
- Validate citation IDs are resolved and verified.

### 15.1 Testing (Extended)
- **Error Handling Tests**\n  - Simulate search API 500 → verify retry + backoff.\n  - Simulate 3 consecutive empty results → verify reformulation triggered.\n  - Simulate timeout → verify graceful degradation.\n  - Force token budget exhaustion → verify compaction triggered.
- **Citation Verification Tests**\n  - Valid IDs → pass.\n  - Hallucinated ID [99] → fail validation.\n  - Claims unsupported by sources → semantic check fails.

## 16. Migration Notes
- Existing clients calling `/run` continue to work.
- New behavior: `/run` now performs multiple LLM calls and may be slower.

### 16.1 Performance Migration Options
- Add `/run-simple` that defaults to `complexity_tier=\"simple\"`.
- OR add `stream=true` for progress updates.
- OR add `async=true` to return a job ID for polling.

## 17. Observability
- Emit structured logs at phase boundaries (plan/search/reflect/synthesize).
- Include `state.iteration`, `state.token_budget`, `len(state.search_results)` in logs.
- Metrics: `research_loop_duration_seconds`, `iterations_per_request`, `search_failures_total`.
