import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.audit_log import AuditLog
from app.models.matter import Matter
from app.schemas.audit import AuditLogRead

router = APIRouter(tags=["audit"])


@router.get("/matters/{matter_id}/audit-logs", response_model=list[AuditLogRead])
async def list_matter_audit_logs(
    matter_id: uuid.UUID,
    action: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> list[AuditLogRead]:
    """Return immutable audit log entries for a matter (newest first)."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

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
