import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SeverityLevel = Literal["info", "low", "medium", "high", "critical"]


class PlaybookRuleSchema(BaseModel):
    clause_type: str = Field(..., min_length=2, max_length=100)
    standard_position: str = Field(..., min_length=3)
    fallback_position: str = Field(..., min_length=3)
    red_flag_patterns: list[str] = Field(default_factory=list)
    required: bool = True
    severity_if_deviated: SeverityLevel = "high"


class PlaybookCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    practice_area: str = Field(default="Commercial Contracts", max_length=120)
    description: str | None = None
    is_default: bool = False
    rules: list[PlaybookRuleSchema] = Field(..., min_length=1)


class PlaybookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    practice_area: str | None = Field(default=None, max_length=120)
    description: str | None = None
    is_default: bool | None = None
    rules: list[PlaybookRuleSchema] | None = None


class PlaybookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    practice_area: str
    description: str | None
    is_default: bool
    created_by_id: uuid.UUID | None
    rules_json: list[PlaybookRuleSchema]
    created_at: datetime
    updated_at: datetime
