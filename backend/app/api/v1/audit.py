import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_matter_access
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditChainVerificationRead, AuditLogRead
from app.services.audit import verify_matter_audit_chain

router = APIRouter(tags=["audit"])


@router.get("/matters/{matter_id}/audit-logs", response_model=list[AuditLogRead])
async def list_matter_audit_logs(
    matter_id: uuid.UUID,
    action: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[AuditLogRead]:
    """Return immutable audit log entries for a matter (newest first)."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)

    stmt = (
        select(AuditLog)
        .where(AuditLog.matter_id == matter_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    if action:
        stmt = stmt.where(AuditLog.action == action)

    rows = (await session.execute(stmt)).scalars().all()
    return [AuditLogRead.model_validate(r) for r in rows]


@router.get("/matters/{matter_id}/audit-logs/verify", response_model=AuditChainVerificationRead)
async def verify_audit_log_chain(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> AuditChainVerificationRead:
    """Verify tamper-evident SHA-256 hash chain integrity across a matter's audit trail."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    result = await verify_matter_audit_chain(session, matter_id=matter_id)
    return AuditChainVerificationRead(**result)
