import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractedFindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    matter_id: uuid.UUID
    document_id: uuid.UUID | None
    review_task_id: uuid.UUID | None
    finding_type: str
    category: str
    title: str
    summary: str
    edited_summary: str | None
    severity: str
    confidence: float
    grounding_score: float
    requires_escalation: bool
    escalation_reason: str | None
    recommended_action: str | None
    citations_json: list[dict[str, Any]]
    metadata_json: dict[str, Any]
    review_status: str
    reviewer_name: str | None
    reviewer_notes: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FindingReviewUpdate(BaseModel):
    review_status: Literal["pending_review", "approved", "flagged"]
    reviewer_name: str | None = Field(default=None, max_length=128)
    reviewer_notes: str | None = None
    edited_summary: str | None = None


class BatchReviewRequest(BaseModel):
    finding_ids: list[uuid.UUID] = Field(..., min_length=1)
    review_status: Literal["pending_review", "approved", "flagged"]
    reviewer_name: str | None = Field(default=None, max_length=128)
    reviewer_notes: str | None = None


class BatchReviewResponse(BaseModel):
    updated_count: int
    findings: list[ExtractedFindingRead]
