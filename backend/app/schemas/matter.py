import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MatterCreate(BaseModel):
    matter_number: str | None = Field(default=None, max_length=64)
    title: str = Field(..., min_length=2, max_length=255)
    client_name: str = Field(..., min_length=2, max_length=255)
    practice_area: str = Field(default="Corporate", max_length=120)
    jurisdiction: str | None = Field(default=None, max_length=120)
    description: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class MatterUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    client_name: str | None = Field(default=None, min_length=2, max_length=255)
    practice_area: str | None = Field(default=None, max_length=120)
    jurisdiction: str | None = Field(default=None, max_length=120)
    description: str | None = None
    status: Literal["active", "archived", "closed"] | None = None


class MatterStats(BaseModel):
    document_count: int = 0
    indexed_document_count: int = 0
    finding_count: int = 0
    pending_review_count: int = 0
    flagged_count: int = 0
    approved_count: int = 0


class MatterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    matter_number: str
    title: str
    client_name: str
    practice_area: str
    jurisdiction: str | None
    description: str | None
    status: str
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    stats: MatterStats = Field(default_factory=MatterStats)
