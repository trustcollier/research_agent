from __future__ import annotations

import os
from typing import Optional

from .adapters import ModelAdapter, OllamaAdapter


DEFAULT_BACKEND = "ollama"


def get_adapter(backend: Optional[str] = None) -> ModelAdapter:
    key = (backend or os.environ.get("MODEL_BACKEND") or DEFAULT_BACKEND).lower()
    if key in ("ollama", "ollama-chat"):
        return OllamaAdapter()
    raise ValueError(f"Unsupported backend: {key}")
