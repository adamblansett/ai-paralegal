import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.llm.types import VerifiedCitation


class ContractReviewRequest(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    clause_types: list[str] | None = None
    playbook_notes: str | None = None
    provider: Literal["mock", "ollama", "gemini"] | None = None
    model: str | None = None


class DiscoverySynthesisRequest(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    focus_topics: list[str] | None = None
    provider: Literal["mock", "ollama", "gemini"] | None = None
    model: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    document_ids: list[uuid.UUID] | None = None
    top_k: int = Field(default=8, ge=1, le=30)


class SearchHitRead(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str
    document_type: str
    chunk_index: int
    content: str
    page_number: int | None
    paragraph_index: int | None
    section_heading: str | None
    char_start: int | None
    char_end: int | None
    lexical_score: float
    semantic_score: float
    rrf_score: float


class SearchResponse(BaseModel):
    matter_id: uuid.UUID
    query: str
    hits: list[SearchHitRead]


class ResearchQueryRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    document_ids: list[uuid.UUID] | None = None
    top_k: int = Field(default=8, ge=1, le=20)
    provider: Literal["mock", "ollama", "gemini"] | None = None
    model: str | None = None
    save_to_queue: bool = False


class ResearchQueryResponse(BaseModel):
    task_id: uuid.UUID
    finding_id: uuid.UUID | None = None
    question: str
    answer: str
    key_points: list[str]
    uncertainties: list[str]
    missing_context: list[str]
    confidence: float
    grounding_score: float
    requires_escalation: bool
    escalation_reason: str | None = None
    citations: list[VerifiedCitation]
    provider: str
    model_name: str
    disclaimer: str


class DraftRequest(BaseModel):
    draft_type: Literal[
        "internal_memo",
        "clause_comparison",
        "discovery_requests",
        "discovery_responses",
        "review_checklist",
    ] = "internal_memo"
    instructions: str = Field(..., min_length=4, max_length=4000)
    document_ids: list[uuid.UUID] | None = None
    provider: Literal["mock", "ollama", "gemini"] | None = None
    model: str | None = None


class ReviewTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    matter_id: uuid.UUID
    document_id: uuid.UUID | None
    task_type: str
    status: str
    celery_task_id: str | None
    requested_provider: str | None
    requested_model: str | None
    resolved_provider: str | None
    resolved_model: str | None
    input_params_json: dict[str, Any]
    result_json: dict[str, Any]
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
