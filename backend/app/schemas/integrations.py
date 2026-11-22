import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DMSImportRequest(BaseModel):
    provider: Literal["mock_dms", "sharepoint", "google_drive", "imanage", "netdocuments"] = (
        "mock_dms"
    )
    external_id: str = Field(..., min_length=1, max_length=255)
    document_type: str = "general"


class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    channel_type: Literal["webhook", "slack", "teams"] = "webhook"
    target_url: str = Field(..., min_length=8, max_length=1024)
    signing_secret: str | None = Field(default=None, max_length=128)
    matter_id: uuid.UUID | None = None
    events: list[str] = Field(
        default_factory=lambda: [
            "finding.escalated",
            "legal_hold.changed",
            "batch_review.completed",
        ]
    )


class WebhookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None
    matter_id: uuid.UUID | None
    name: str
    channel_type: str
    target_url: str
    events_json: list[str]
    is_active: bool
    created_at: datetime


class WebhookTestResult(BaseModel):
    delivered: bool
    status_code: int | None = None
    error: str | None = None


class WordRedlineItem(BaseModel):
    finding_id: uuid.UUID
    category: str
    severity: str
    title: str
    review_status: str
    original_quote: str
    proposed_redline: str
    rationale: str | None = None
    document_filename: str | None = None


class WordRedlinePayloadResponse(BaseModel):
    matter_id: uuid.UUID
    matter_title: str
    items: list[WordRedlineItem]
    disclaimer: str
