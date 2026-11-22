import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def record_audit_log(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    action: str,
    actor_type: str = "system",
    actor_id: str = "system",
    document_id: uuid.UUID | None = None,
    review_task_id: uuid.UUID | None = None,
    finding_id: uuid.UUID | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    prompt_template: str | None = None,
    prompt_version: str | None = None,
    prompt_text: str | None = None,
    retrieved_spans: list[dict[str, Any]] | None = None,
    response_metadata: dict[str, Any] | None = None,
    token_usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    grounding_score: float | None = None,
    escalated: bool = False,
    previous_state: str | None = None,
    new_state: str | None = None,
    notes: str | None = None,
) -> AuditLog:
    """Create and stage an immutable AuditLog record in the active database session."""
    entry = AuditLog(
        matter_id=matter_id,
        document_id=document_id,
        review_task_id=review_task_id,
        finding_id=finding_id,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        provider=provider,
        model_name=model_name,
        prompt_template=prompt_template,
        prompt_version=prompt_version,
        prompt_text=prompt_text,
        retrieved_spans_json=retrieved_spans or [],
        response_metadata_json=response_metadata or {},
        token_usage_json=token_usage or {},
        latency_ms=latency_ms,
        grounding_score=grounding_score,
        escalated=escalated,
        previous_state=previous_state,
        new_state=new_state,
        notes=notes,
    )
    session.add(entry)
    return entry
