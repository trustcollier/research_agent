from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    task: str = Field(..., min_length=1)
    agent_name: Optional[str] = None
    agent_id: Optional[str] = None
    options: Optional[dict] = None


class WebSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(5, ge=1, le=20)


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
    metadata: Optional[dict] = None


class PlanQuery(BaseModel):
    query: str
    intent: str


class PlanResponse(BaseModel):
    queries: List[PlanQuery]


class ReflectionResponse(BaseModel):
    sufficient: bool
    confidence: float
    gaps: List[str]
    new_queries: List[PlanQuery]


class Citation(BaseModel):
    id: str
    title: str
    type: str
    location: str


class SynthesisResponse(BaseModel):
    answer: str
    citations: List[Citation]
