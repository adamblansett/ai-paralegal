import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ObligationCategory = Literal[
    "renewal_notice",
    "payment",
    "cure_period",
    "filing_deadline",
    "compliance",
    "termination_window",
    "other",
]
ObligationStatus = Literal["open", "completed", "waived", "overdue"]


class DocumentCompareRequest(BaseModel):
    base_document_id: uuid.UUID
    revised_document_id: uuid.UUID


class ClauseDiffHunk(BaseModel):
    section_heading: str
    change_type: Literal["added", "removed", "modified", "unchanged"]
    base_text: str | None = None
    revised_text: str | None = None
    unified_diff: str
    risk_impact: str | None = None


class RiskRegressionFlag(BaseModel):
    severity: Literal["high", "medium", "low"]
    category: str
    title: str
    detail: str
    base_excerpt: str | None = None
    revised_excerpt: str | None = None


class DocumentComparisonResponse(BaseModel):
    matter_id: uuid.UUID
    base_document_id: uuid.UUID
    base_filename: str
    revised_document_id: uuid.UUID
    revised_filename: str
    total_hunks: int
    added_count: int
    removed_count: int
    modified_count: int
    unchanged_count: int
    hunks: list[ClauseDiffHunk] = Field(default_factory=list)
    risk_regressions: list[RiskRegressionFlag] = Field(default_factory=list)
    summary_markdown: str


class ConflictCheckRequest(BaseModel):
    client_name: str = Field(..., min_length=2, max_length=255)
    counterparties: list[str] = Field(default_factory=list)
    matter_title: str | None = None
    exclude_matter_id: uuid.UUID | None = None


class ConflictMatch(BaseModel):
    matter_id: uuid.UUID
    matter_number: str
    matter_title: str
    client_name: str
    matched_party: str
    match_source: Literal["client_name", "matter_title", "discovered_entity", "document_text"]
    confidence: float = Field(ge=0.0, le=1.0)
    severity: Literal["high", "medium", "low"]
    snippet: str | None = None


class ConflictCheckResponse(BaseModel):
    query_client: str
    query_counterparties: list[str]
    has_conflicts: bool
    risk_level: Literal["none", "low", "medium", "high"]
    recommended_action: str
    matches: list[ConflictMatch] = Field(default_factory=list)


class EthicalWallCreate(BaseModel):
    user_id: uuid.UUID
    reason: str = Field(..., min_length=4, max_length=2000)


class EthicalWallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None = None
    matter_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    reason: str
    is_active: bool
    created_at: datetime


class ObligationCreate(BaseModel):
    document_id: uuid.UUID | None = None
    title: str = Field(..., min_length=2, max_length=255)
    category: ObligationCategory = "compliance"
    due_date: datetime
    notice_window_days: int | None = Field(default=None, ge=0, le=3650)
    owner_name: str | None = Field(default=None, max_length=255)
    source_quote: str | None = None
    notes: str | None = None


class ObligationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    category: ObligationCategory | None = None
    due_date: datetime | None = None
    notice_window_days: int | None = Field(default=None, ge=0, le=3650)
    owner_name: str | None = Field(default=None, max_length=255)
    status: ObligationStatus | None = None
    notes: str | None = None


class ObligationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    matter_id: uuid.UUID
    document_id: uuid.UUID | None = None
    title: str
    category: str
    due_date: datetime
    notice_window_days: int | None = None
    owner_name: str | None = None
    status: str
    source_quote: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class ObligationAlertResult(BaseModel):
    matter_id: uuid.UUID
    due_within_days: int
    alerted_count: int
    overdue_count: int
    webhooks_dispatched: int
    obligations: list[ObligationRead] = Field(default_factory=list)
