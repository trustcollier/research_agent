from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class ModelAdapter(Protocol):
    def chat(
        self,
        *,
        system: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        force_json: bool = True,
    ) -> Dict[str, Any]:
        ...
