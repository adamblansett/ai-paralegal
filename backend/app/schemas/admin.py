import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import UserRole


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    retention_days: int
    is_active: bool
    created_at: datetime
    user_count: int = 0
    matter_count: int = 0
    webhook_count: int = 0


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    retention_days: int | None = Field(default=None, ge=1, le=36500)


class AdminUserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class DocumentRetentionUpdate(BaseModel):
    retention_until: datetime | None = None


class RetentionSweepRequest(BaseModel):
    dry_run: bool = True
    as_of: datetime | None = None


class RetentionSweepItem(BaseModel):
    document_id: uuid.UUID
    matter_id: uuid.UUID
    filename: str
    action: Literal["purged", "eligible_for_purge", "skipped_legal_hold"]
    reason: str
    retention_until: datetime | None = None


class RetentionSweepReport(BaseModel):
    organization_id: uuid.UUID | None = None
    dry_run: bool
    as_of: datetime
    evaluated_count: int
    purged_count: int
    eligible_count: int
    skipped_legal_hold_count: int
    items: list[RetentionSweepItem] = Field(default_factory=list)
