from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Literal, Optional, TypedDict
from urllib.request import Request, urlopen


Role = Literal["system", "user", "assistant"]


class OllamaMessage(TypedDict):
    role: Role
    content: str


class OllamaResponse(TypedDict, total=False):
    content: str
    raw: Dict[str, Any]


def _post_json(url: str, payload: Dict[str, Any], *, timeout: int) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def ollama_chat(
    *,
    model: str,
    system: str,
    messages: List[OllamaMessage],
    temperature: Optional[float] = 0.2,
    max_tokens: Optional[int] = 800,
    host: Optional[str] = None,
    timeout: Optional[int] = None,
    force_json: bool = True,
) -> OllamaResponse:
    endpoint = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    url = f"{endpoint}/api/chat"
    request_timeout = timeout or int(os.environ.get("OLLAMA_TIMEOUT", "120"))

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }

    if force_json:
        payload["format"] = "json"

    data = _post_json(url, payload, timeout=request_timeout)
    message = data.get("message") or {}

    return {
        "content": message.get("content", ""),
        "raw": {
            "model": data.get("model"),
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
        },
    }
