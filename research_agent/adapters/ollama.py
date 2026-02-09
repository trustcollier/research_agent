from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..ollama_client import ollama_chat
from .base import ModelAdapter


DEFAULT_MODEL = "llama3.1:latest"
DEFAULT_MAX_TOKENS = 1600


class OllamaAdapter(ModelAdapter):
    def __init__(
        self,
        *,
        model: Optional[str] = None,
        host: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL
        self.host = host or os.environ.get("OLLAMA_HOST")
        self.max_tokens = max_tokens or int(
            os.environ.get("OLLAMA_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))
        )

    def chat(
        self,
        *,
        system: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        force_json: bool = True,
    ) -> Dict[str, Any]:
        return ollama_chat(
            model=self.model,
            host=self.host,
            system=system,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens or self.max_tokens,
            force_json=force_json,
        )
