# Implementation Plan (Temporary)

## 1. Executive Summary
This plan converts the Research Loop Design v2 into code with a focus on a working loop first, then resilience, verification, and observability. It assumes the existing local model adapter (Ollama) and avoids provider‑specific decisions, cost modeling, and calendar/time‑boxed scheduling.

## 2. Critical Path (Core Loop)
### 2.1 State Model & Serialization
- Use Pydantic v2 for runtime validation and JSON serialization.
- Enforce strict schemas (`extra='forbid'`) to catch drift early.
- Track iteration monotonicity and append‑only collections.

Example invariants:
- `iteration` never decreases
- `search_results` and `citations` are append‑only
- `sufficient` is set only by reflection

### 2.2 Orchestration Pattern
- Use an imperative loop (`while`) to match the design pseudo‑code.
- Each phase is an isolated function that mutates state and can raise typed errors.
- Guard conditions: `sufficient`, `max_iters`, `max_execution_time_s`.

### 2.3 Phase Function Interfaces
```
async def execute_plan_phase(state) -> None
async def execute_search_phase(state) -> None
async def execute_reflect_phase(state) -> None
async def execute_synthesis_phase(state) -> SynthesisResponse
```

## 3. Prompt & Structured Output Strategy
### 3.1 Prompt Files
- `prompts/plan_prompt.txt`
- `prompts/reflect_prompt.txt`
- `prompts/synthesize_prompt.txt`

### 3.2 Structured Output Handling
- Use JSON‑only responses; validate against Pydantic schemas.
- On parse failure, retry with explicit schema instructions and return ErrorResponse if still invalid.

## 4. Tiered Parameterization
### 4.1 Tier Selection
- Default: **standard** tier.
- Allow explicit override via `complexity_tier` in request.
- Optional: classify in plan phase (if implemented, log decisions for audit).

### 4.2 Tier Definitions
- **Simple**: 2 iterations, 3 queries, 5 sources
- **Standard**: 5 iterations, 10 queries, 15 sources
- **Deep**: 10+ iterations, 15+ queries, 20+ sources
- All tiers include `max_execution_time_s` as a safety guard.

## 5. Search Integration & Error Handling
### 5.1 Retry + Reformulation
- Tier 1: exponential backoff with jitter, 2–3 retries per query.
- Tier 2: reflection generates reformulated queries for `failed_queries`.

### 5.2 Circuit Breaker
- Trip after 3 consecutive failures or 50% failure rate (min 4 queries).
- Set `state.degraded_mode = True` and synthesize with uncertainty markers.

### 5.3 Fallback Providers
- Keep adapter interface; if additional providers exist, chain them in priority order.

## 6. State Compaction & Token Budget
### 6.1 Trigger Conditions
- Token budget below 25% of initial allocation
- OR serialized state exceeds 50K tokens
- OR before final synthesis

### 6.2 Compaction Strategy
- Summarize older results into a single compacted summary.
- Keep recent N results; store full results externally if required.

## 7. Citation Verification
### 7.1 Source ID Mapping
- Assign `[1]`, `[2]`, … to retrieved sources.
- Require LLM to cite by source ID only.

### 7.2 Validation
- Existence check for cited IDs.
- Optional semantic verification (NLI/LLM) for high‑stakes tasks.

## 8. Observability
- Emit structured logs at phase boundaries.
- Include `iteration`, `token_budget`, `search_results_count`, `failed_queries_count`.
- Metrics: loop duration, iterations per request, search failure count, compaction events.

## 9. Testing Strategy
### 9.1 Unit Tests
- Plan phase generates queries
- Search phase retries on transient errors
- Reflect phase updates sufficiency and confidence
- Synthesis phase enforces citations only from retrieved sources

### 9.2 Integration Tests
- End‑to‑end loop with mocked search/LLM
- Insufficient termination path (max_iters or max_execution_time_s)
- Degraded mode path (circuit breaker trips)

### 9.3 Error & Citation Tests
- Retry/backoff correctness
- Reformulation triggered by failed queries
- Invalid citation ID detection
- Optional semantic verification failure on unsupported claim

## 10. Rollout & Compatibility
- Keep `/run` as the main entrypoint.
- Consider optional `/run-simple` for fast responses.
- Preserve `AgentResponse` schema for client compatibility.

## 11. Open Questions (Implementation‑Only)
- Should tier classification be enabled by default or gated behind a flag?
- Should state persistence survive server restarts (if yes, where stored)?
- What is the acceptable upper bound on loop latency for production?

## 12. Milestones (No Calendar Assumptions)
1) **Core loop**: state model, plan/search/reflect/synthesize phases wired.
2) **Resilience**: retry, reformulation, circuit breaker, error logging.
3) **Compaction**: token tracking and summarization strategy.
4) **Citations**: source ID mapping + validation, optional semantic check.
5) **Observability + tests**: logs, metrics, unit/integration tests.
