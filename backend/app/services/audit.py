import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

GENESIS_HASH = "GENESIS"


def compute_audit_entry_hash(
    *,
    matter_id: uuid.UUID,
    sequence_number: int,
    previous_hash: str,
    action: str,
    actor_type: str,
    actor_id: str,
    previous_state: str | None,
    new_state: str | None,
    notes: str | None,
) -> str:
    """Compute canonical SHA-256 chain link digest for tamper-evident audit logging."""
    canonical_payload = json.dumps(
        {
            "matter_id": str(matter_id),
            "sequence_number": sequence_number,
            "previous_hash": previous_hash,
            "action": action,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "previous_state": previous_state,
            "new_state": new_state,
            "notes": notes,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical_payload).hexdigest()


async def _resolve_chain_head(
    session: AsyncSession,
    matter_id: uuid.UUID,
) -> tuple[int, str]:
    """Find highest sequence_number/hash across pending session objects and persisted rows."""
    pending_entries = [
        obj
        for obj in session.new
        if isinstance(obj, AuditLog) and obj.matter_id == matter_id and obj.entry_hash
    ]
    if pending_entries:
        latest_pending = max(pending_entries, key=lambda o: o.sequence_number)
        return latest_pending.sequence_number, latest_pending.entry_hash or GENESIS_HASH

    stmt = (
        select(AuditLog)
        .where(AuditLog.matter_id == matter_id)
        .order_by(AuditLog.sequence_number.desc(), AuditLog.created_at.desc())
        .limit(1)
    )
    latest_db = (await session.execute(stmt)).scalars().first()
    if latest_db is None:
        return 0, GENESIS_HASH
    return int(latest_db.sequence_number or 0), latest_db.entry_hash or GENESIS_HASH


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
    """Create and stage a tamper-evident hash-chained AuditLog record in the active session."""
    prev_seq, prev_hash = await _resolve_chain_head(session, matter_id)
    next_seq = prev_seq + 1
    entry_hash = compute_audit_entry_hash(
        matter_id=matter_id,
        sequence_number=next_seq,
        previous_hash=prev_hash,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        previous_state=previous_state,
        new_state=new_state,
        notes=notes,
    )

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
        sequence_number=next_seq,
        previous_hash=prev_hash,
        entry_hash=entry_hash,
    )
    session.add(entry)
    return entry


async def verify_matter_audit_chain(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
) -> dict[str, Any]:
    """Recompute and verify SHA-256 chain integrity across all audit entries for a matter."""
    stmt = (
        select(AuditLog)
        .where(AuditLog.matter_id == matter_id)
        .order_by(AuditLog.sequence_number.asc(), AuditLog.created_at.asc())
    )
    entries = list((await session.execute(stmt)).scalars().all())
    if not entries:
        return {
            "matter_id": matter_id,
            "total_entries": 0,
            "verified": True,
            "broken_at_sequence": None,
            "latest_hash": None,
            "detail": "No audit entries recorded for this matter yet.",
        }

    expected_prev = GENESIS_HASH
    for idx, entry in enumerate(entries, start=1):
        seq = int(entry.sequence_number or 0)
        if seq != idx or (entry.previous_hash or GENESIS_HASH) != expected_prev:
            return {
                "matter_id": matter_id,
                "total_entries": len(entries),
                "verified": False,
                "broken_at_sequence": seq or idx,
                "latest_hash": entry.entry_hash,
                "detail": (
                    f"Chain linkage mismatch at sequence {seq or idx}: "
                    "previous hash or sequence number does not match predecessor."
                ),
            }

        recomputed = compute_audit_entry_hash(
            matter_id=entry.matter_id,
            sequence_number=seq,
            previous_hash=entry.previous_hash or GENESIS_HASH,
            action=entry.action,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            previous_state=entry.previous_state,
            new_state=entry.new_state,
            notes=entry.notes,
        )
        if entry.entry_hash != recomputed:
            return {
                "matter_id": matter_id,
                "total_entries": len(entries),
                "verified": False,
                "broken_at_sequence": seq,
                "latest_hash": entry.entry_hash,
                "detail": f"Digest tampering detected at audit sequence #{seq} ({entry.action}).",
            }
        expected_prev = recomputed

    return {
        "matter_id": matter_id,
        "total_entries": len(entries),
        "verified": True,
        "broken_at_sequence": None,
        "latest_hash": expected_prev,
        "detail": f"Verified intact SHA-256 hash chain across {len(entries)} audit record(s).",
    }
