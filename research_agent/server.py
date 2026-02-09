from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from .research_loop import run_research
from .schemas import AgentRequest, AgentResponse, WebSearchRequest
from .tools.web_search import search_web


APP = FastAPI()

ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_PATH)


def _error_response(reason: str, status_code: int = 502) -> JSONResponse:
    response = AgentResponse(
        summary=f"ERROR: {reason}",
        key_findings=[],
        recommendations=[],
        risks=[reason],
        open_questions=[],
        sources=[],
        raw=None,
    )
    return JSONResponse(status_code=status_code, content=response.model_dump())


@APP.post("/run")
async def run_agent(request: AgentRequest):
    max_iters: Optional[int] = None
    max_queries: Optional[int] = None
    max_sources: Optional[int] = None
    if request.options and isinstance(request.options, dict):
        max_iters = request.options.get("max_iters")
        max_queries = request.options.get("max_queries")
        max_sources = request.options.get("max_sources")

    try:
        response = run_research(
            request.task,
            agent_name=request.agent_name,
            agent_id=request.agent_id,
            max_iters=max_iters,
            max_queries=max_queries,
            max_sources=max_sources,
        )
    except Exception as exc:
        return _error_response(f"Research loop failed: {exc}")

    return JSONResponse(status_code=200, content=response.model_dump())


@APP.post("/search")
async def web_search(request: WebSearchRequest):
    sources = search_web(request.query, limit=request.limit)
    return JSONResponse(
        status_code=200,
        content=[source.model_dump() for source in sources],
    )
