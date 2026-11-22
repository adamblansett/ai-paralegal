import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    matter_id: uuid.UUID
    document_id: uuid.UUID | None
    review_task_id: uuid.UUID | None
    finding_id: uuid.UUID | None
    action: str
    actor_type: str
    actor_id: str
    provider: str | None
    model_name: str | None
    prompt_template: str | None
    prompt_version: str | None
    prompt_text: str | None
    retrieved_spans_json: list[dict[str, Any]]
    response_metadata_json: dict[str, Any]
    token_usage_json: dict[str, Any]
    latency_ms: int | None
    grounding_score: float | None
    escalated: bool
    previous_state: str | None
    new_state: str | None
    notes: str | None
    created_at: datetime
