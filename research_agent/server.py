from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from .agents_store import list_agents
from .ollama_client import ollama_chat
from .schemas import AgentRequest, AgentResponse
from .tools.web_search import search_web


APP = FastAPI()

DEFAULT_MODEL = "llama3.1:latest"
DEFAULT_MAX_TOKENS = 1600
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system_prompt.txt"
ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_PATH)


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _resolve_agent_prompt(agent_name: Optional[str], agent_id: Optional[str]) -> Optional[str]:
    if not agent_name and not agent_id:
        return None
    agents = list_agents()
    for agent in agents:
        if agent_id and agent.get("id") == agent_id:
            return agent.get("prompt")
        if agent_name and agent.get("name") == agent_name:
            return agent.get("prompt")
    return None


def _build_user_task(agent_prompt: Optional[str], task: str) -> str:
    if agent_prompt:
        return f"{agent_prompt}\n\n{task}"
    return task


def _fallback_response(content: str, raw: Dict[str, Any]) -> AgentResponse:
    reason = "Model did not return structured JSON."
    return AgentResponse(
        summary=f"ERROR: {reason}",
        key_findings=[],
        recommendations=[],
        risks=[reason],
        open_questions=[],
        sources=[],
        raw=raw,
    )


def _parse_agent_response(content: str, raw: Dict[str, Any]) -> AgentResponse:
    try:
        payload = json.loads(content)
        response = AgentResponse(**payload)
        response.raw = raw
        return response
    except Exception:
        return _fallback_response(content, raw)


@APP.post("/run")
async def run_agent(request: AgentRequest):
    model = os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL
    max_tokens = int(os.environ.get("OLLAMA_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
    agent_prompt = _resolve_agent_prompt(request.agent_name, request.agent_id)
    user_task = _build_user_task(agent_prompt, request.task)

    try:
        result = ollama_chat(
            model=model,
            system=load_system_prompt(),
            messages=[{"role": "user", "content": user_task}],
            temperature=0.2,
            max_tokens=max_tokens,
            force_json=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}") from exc

    response = _parse_agent_response(result.get("content", ""), result.get("raw", {}))

    include_web = False
    if request.options and isinstance(request.options, dict):
        include_web = bool(request.options.get("include_web"))
    if include_web:
        response.sources = search_web(request.task, limit=5)

    return JSONResponse(status_code=200, content=response.model_dump())
