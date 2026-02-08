from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    task: str = Field(..., min_length=1)
    agent_name: Optional[str] = None
    agent_id: Optional[str] = None
    options: Optional[dict] = None


class AgentSource(BaseModel):
    title: str
    type: str
    location: str


class AgentResponse(BaseModel):
    summary: str
    key_findings: List[str]
    recommendations: List[str]
    risks: List[str]
    open_questions: List[str]
    sources: List[AgentSource]
    raw: Optional[dict] = None
