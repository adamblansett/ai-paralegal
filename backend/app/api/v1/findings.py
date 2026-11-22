import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    format_actor_id,
    get_current_user,
    get_db,
    require_finding_access,
    require_matter_access,
)
from app.core.security import ATTORNEY_ROLES
from app.models.finding import ExtractedFinding
from app.models.user import User
from app.schemas.finding import (
    BatchReviewRequest,
    BatchReviewResponse,
    ExtractedFindingRead,
    FindingReviewUpdate,
)
from app.services.audit import record_audit_log

router = APIRouter(tags=["findings"])


def _check_approval_role(review_status: str, user: User) -> None:
    if review_status == "approved" and user.role not in ATTORNEY_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Role '{user.role}' cannot approve legal findings. "
                "Licensed attorney role (partner or associate) required."
            ),
        )


@router.get("/matters/{matter_id}/findings", response_model=list[ExtractedFindingRead])
async def list_matter_findings(
    matter_id: uuid.UUID,
    review_status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    finding_type: str | None = Query(default=None),
    category: str | None = Query(default=None),
    requires_escalation: bool | None = Query(default=None),
    document_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[ExtractedFindingRead]:
    """List extracted findings for a matter with optional review/severity filters."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)

    stmt = (
        select(ExtractedFinding)
        .where(ExtractedFinding.matter_id == matter_id)
        .order_by(ExtractedFinding.created_at.desc())
    )
    if review_status:
        stmt = stmt.where(ExtractedFinding.review_status == review_status)
    if severity:
        stmt = stmt.where(ExtractedFinding.severity == severity)
    if finding_type:
        stmt = stmt.where(ExtractedFinding.finding_type == finding_type)
    if category:
        stmt = stmt.where(ExtractedFinding.category == category)
    if requires_escalation is not None:
        stmt = stmt.where(ExtractedFinding.requires_escalation == requires_escalation)
    if document_id is not None:
        stmt = stmt.where(ExtractedFinding.document_id == document_id)

    findings = (await session.execute(stmt)).scalars().all()
    return [ExtractedFindingRead.model_validate(f) for f in findings]


@router.get("/findings/{finding_id}", response_model=ExtractedFindingRead)
async def get_finding(
    finding_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ExtractedFindingRead:
    """Fetch a single extracted finding with full citation provenance."""
    finding = await require_finding_access(
        session, finding_id=finding_id, user=current_user, write=False
    )
    return ExtractedFindingRead.model_validate(finding)


@router.patch("/findings/{finding_id}/review", response_model=ExtractedFindingRead)
async def review_finding(
    finding_id: uuid.UUID,
    payload: FindingReviewUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ExtractedFindingRead:
    """Update human approval state (`pending_review`, `approved`, `flagged`) and edits."""
    finding = await require_finding_access(
        session, finding_id=finding_id, user=current_user, write=True
    )
    _check_approval_role(payload.review_status, current_user)

    previous_status = finding.review_status
    authoritative_reviewer = f"{current_user.full_name} ({current_user.role})"
    finding.review_status = payload.review_status
    finding.reviewer_name = authoritative_reviewer
    if payload.reviewer_notes is not None:
        finding.reviewer_notes = payload.reviewer_notes
    if payload.edited_summary is not None:
        finding.edited_summary = payload.edited_summary
    finding.reviewed_at = datetime.now(UTC)

    await record_audit_log(
        session,
        matter_id=finding.matter_id,
        document_id=finding.document_id,
        review_task_id=finding.review_task_id,
        finding_id=finding.id,
        action="finding_reviewed",
        actor_type="human",
        actor_id=format_actor_id(current_user),
        grounding_score=finding.grounding_score,
        escalated=finding.requires_escalation,
        previous_state=previous_status,
        new_state=finding.review_status,
        notes=payload.reviewer_notes or f"Reviewed finding '{finding.title}'",
    )

    await session.commit()
    await session.refresh(finding)
    return ExtractedFindingRead.model_validate(finding)


@router.post("/findings/batch-review", response_model=BatchReviewResponse)
async def batch_review_findings(
    payload: BatchReviewRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> BatchReviewResponse:
    """Batch approve, flag, or reset review state across multiple findings."""
    _check_approval_role(payload.review_status, current_user)

    stmt = select(ExtractedFinding).where(ExtractedFinding.id.in_(payload.finding_ids))
    findings = list((await session.execute(stmt)).scalars().all())
    if not findings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No matching findings found."
        )

    for finding in findings:
        await require_matter_access(
            session, matter_id=finding.matter_id, user=current_user, write=True
        )

    now = datetime.now(UTC)
    authoritative_reviewer = f"{current_user.full_name} ({current_user.role})"
    actor_id = format_actor_id(current_user)
    for finding in findings:
        prev = finding.review_status
        finding.review_status = payload.review_status
        finding.reviewer_name = authoritative_reviewer
        if payload.reviewer_notes is not None:
            finding.reviewer_notes = payload.reviewer_notes
        finding.reviewed_at = now

        await record_audit_log(
            session,
            matter_id=finding.matter_id,
            document_id=finding.document_id,
            review_task_id=finding.review_task_id,
            finding_id=finding.id,
            action="finding_reviewed",
            actor_type="human",
            actor_id=actor_id,
            grounding_score=finding.grounding_score,
            escalated=finding.requires_escalation,
            previous_state=prev,
            new_state=finding.review_status,
            notes=payload.reviewer_notes or "Batch review action",
        )

    await session.commit()
    for finding in findings:
        await session.refresh(finding)
    return BatchReviewResponse(
        updated_count=len(findings),
        findings=[ExtractedFindingRead.model_validate(f) for f in findings],
    )
