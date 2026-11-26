import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    format_actor_id,
    get_app_settings,
    get_current_user,
    get_db,
    require_matter_access,
    require_roles,
)
from app.core.config import Settings
from app.models.legal_ops import EthicalWall, MatterObligation
from app.models.user import MatterMembership, User
from app.schemas.legal_ops import (
    ConflictCheckRequest,
    ConflictCheckResponse,
    DocumentCompareRequest,
    DocumentComparisonResponse,
    EthicalWallCreate,
    EthicalWallRead,
    ObligationAlertResult,
    ObligationCreate,
    ObligationRead,
    ObligationUpdate,
)
from app.services.audit import record_audit_log
from app.services.comparison import compare_matter_documents
from app.services.conflicts import run_conflict_check
from app.services.obligations import (
    check_and_alert_obligations,
    extract_obligations_from_matter,
    render_obligations_ics,
)

router = APIRouter(tags=["legal_ops"])


@router.post(
    "/matters/{matter_id}/documents/compare",
    response_model=DocumentComparisonResponse,
)
async def compare_documents_route(
    matter_id: uuid.UUID,
    payload: DocumentCompareRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DocumentComparisonResponse:
    """Compare two contract versions in a matter and flag legal risk regressions."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    try:
        return await compare_matter_documents(
            session,
            matter_id=matter_id,
            base_document_id=payload.base_document_id,
            revised_document_id=payload.revised_document_id,
            actor_id=format_actor_id(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/matters/conflict-check", response_model=ConflictCheckResponse)
async def conflict_check_route(
    payload: ConflictCheckRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ConflictCheckResponse:
    """Screen prospective clients/counterparties against existing tenant matters & entities."""
    return await run_conflict_check(
        session,
        organization_id=current_user.organization_id,
        client_name=payload.client_name,
        counterparties=payload.counterparties,
        matter_title=payload.matter_title,
        exclude_matter_id=payload.exclude_matter_id,
    )


@router.get("/matters/{matter_id}/ethical-walls", response_model=list[EthicalWallRead])
async def list_ethical_walls(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[EthicalWallRead]:
    """List active ethical wall information barriers on a matter."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    stmt = (
        select(EthicalWall, User)
        .join(User, EthicalWall.user_id == User.id)
        .where(EthicalWall.matter_id == matter_id, EthicalWall.is_active.is_(True))
        .order_by(EthicalWall.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    results: list[EthicalWallRead] = []
    for wall, usr in rows:
        item = EthicalWallRead.model_validate(wall)
        item.user_email = usr.email
        item.user_full_name = usr.full_name
        results.append(item)
    return results


@router.post(
    "/matters/{matter_id}/ethical-walls",
    response_model=EthicalWallRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_ethical_wall(
    matter_id: uuid.UUID,
    payload: EthicalWallCreate,
    current_user: User = Depends(require_roles("partner")),
    session: AsyncSession = Depends(get_db),
) -> EthicalWallRead:
    """Place an ethical wall screen blocking a user from accessing a conflicted matter."""
    matter = await require_matter_access(
        session, matter_id=matter_id, user=current_user, write=True
    )
    if payload.user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot place an ethical wall on yourself while managing this matter.",
        )

    target_user = await session.get(User, payload.user_id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found.")
    if (
        current_user.organization_id is not None
        and target_user.organization_id != current_user.organization_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target user belongs to another organization tenant.",
        )

    existing = (
        await session.execute(
            select(EthicalWall).where(
                EthicalWall.matter_id == matter_id,
                EthicalWall.user_id == payload.user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.is_active = True
        existing.reason = payload.reason.strip()
        existing.created_by_id = current_user.id
        wall = existing
    else:
        wall = EthicalWall(
            organization_id=matter.organization_id,
            matter_id=matter_id,
            user_id=payload.user_id,
            reason=payload.reason.strip(),
            created_by_id=current_user.id,
            is_active=True,
        )
        session.add(wall)

    await session.execute(
        delete(MatterMembership).where(
            MatterMembership.matter_id == matter_id,
            MatterMembership.user_id == payload.user_id,
        )
    )
    await record_audit_log(
        session,
        matter_id=matter_id,
        action="ethical_wall_created",
        actor_type="human",
        actor_id=format_actor_id(current_user),
        new_state="walled",
        notes=f"Placed ethical wall blocking {target_user.email}: {payload.reason.strip()}",
    )
    await session.commit()
    await session.refresh(wall)

    res = EthicalWallRead.model_validate(wall)
    res.user_email = target_user.email
    res.user_full_name = target_user.full_name
    return res


@router.delete(
    "/matters/{matter_id}/ethical-walls/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_ethical_wall(
    matter_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(require_roles("partner")),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Lift an active ethical wall barrier for a user on a matter (partner only)."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)
    wall = (
        await session.execute(
            select(EthicalWall).where(
                EthicalWall.matter_id == matter_id,
                EthicalWall.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if wall is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ethical wall not found.")

    wall.is_active = False
    await record_audit_log(
        session,
        matter_id=matter_id,
        action="ethical_wall_removed",
        actor_type="human",
        actor_id=format_actor_id(current_user),
        previous_state="walled",
        new_state="lifted",
        notes=f"Removed ethical wall for user_id={user_id}.",
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/matters/{matter_id}/obligations", response_model=list[ObligationRead])
async def list_matter_obligations(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[ObligationRead]:
    """List contractual obligations and filing/notice deadlines for a matter."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    rows = (
        await session.execute(
            select(MatterObligation)
            .where(MatterObligation.matter_id == matter_id)
            .order_by(MatterObligation.due_date.asc())
        )
    ).scalars().all()
    return [ObligationRead.model_validate(r) for r in rows]


@router.post(
    "/matters/{matter_id}/obligations",
    response_model=ObligationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_matter_obligation(
    matter_id: uuid.UUID,
    payload: ObligationCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ObligationRead:
    """Create a manual obligation or deadline entry on a matter."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)
    ob = MatterObligation(
        matter_id=matter_id,
        document_id=payload.document_id,
        title=payload.title.strip(),
        category=payload.category,
        due_date=payload.due_date,
        notice_window_days=payload.notice_window_days,
        owner_name=payload.owner_name.strip() if payload.owner_name else None,
        status="open",
        source_quote=payload.source_quote,
        notes=payload.notes,
    )
    session.add(ob)
    await record_audit_log(
        session,
        matter_id=matter_id,
        document_id=payload.document_id,
        action="obligation_created",
        actor_type="human",
        actor_id=format_actor_id(current_user),
        new_state="open",
        notes=f"Added obligation '{ob.title}' due {payload.due_date.isoformat()}.",
    )
    await session.commit()
    await session.refresh(ob)
    return ObligationRead.model_validate(ob)


@router.post("/matters/{matter_id}/obligations/extract", response_model=list[ObligationRead])
async def extract_matter_obligations(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[ObligationRead]:
    """Extract notice windows, cure periods, and explicit deadlines from indexed documents."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)
    created = await extract_obligations_from_matter(
        session,
        matter_id=matter_id,
        actor_id=format_actor_id(current_user),
    )
    return [ObligationRead.model_validate(ob) for ob in created]


@router.patch("/obligations/{obligation_id}", response_model=ObligationRead)
async def update_matter_obligation(
    obligation_id: uuid.UUID,
    payload: ObligationUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ObligationRead:
    """Update status, due date, owner, or notes on a matter obligation."""
    ob = await session.get(MatterObligation, obligation_id)
    if ob is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Obligation not found.")
    await require_matter_access(session, matter_id=ob.matter_id, user=current_user, write=True)

    prev_status = ob.status
    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(ob, k, v)
    if "status" in updates and updates["status"] != prev_status:
        await record_audit_log(
            session,
            matter_id=ob.matter_id,
            document_id=ob.document_id,
            action="obligation_updated",
            actor_type="human",
            actor_id=format_actor_id(current_user),
            previous_state=prev_status,
            new_state=ob.status,
            notes=f"Updated obligation '{ob.title}' status to {ob.status}.",
        )
    await session.commit()
    await session.refresh(ob)
    return ObligationRead.model_validate(ob)


@router.delete("/obligations/{obligation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_matter_obligation(
    obligation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    ob = await session.get(MatterObligation, obligation_id)
    if ob is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Obligation not found.")
    await require_matter_access(session, matter_id=ob.matter_id, user=current_user, write=True)
    await session.delete(ob)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/matters/{matter_id}/obligations/calendar.ics")
async def export_obligations_calendar_ics(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Export matter obligations and notice windows as an RFC 5545 `.ics` calendar file."""
    matter = await require_matter_access(
        session, matter_id=matter_id, user=current_user, write=False
    )
    rows = list(
        (
            await session.execute(
                select(MatterObligation)
                .where(MatterObligation.matter_id == matter_id)
                .order_by(MatterObligation.due_date.asc())
            )
        )
        .scalars()
        .all()
    )
    ics_text = render_obligations_ics(matter, rows)
    return Response(
        content=ics_text,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{matter.matter_number}_obligations.ics"'
        },
    )


@router.post("/matters/{matter_id}/obligations/check-alerts", response_model=ObligationAlertResult)
async def check_obligation_alerts(
    matter_id: uuid.UUID,
    due_within_days: int = Query(default=14, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ObligationAlertResult:
    """Mark overdue obligations and dispatch signed webhook alerts for upcoming deadlines."""
    matter = await require_matter_access(
        session, matter_id=matter_id, user=current_user, write=True
    )
    return await check_and_alert_obligations(
        session,
        matter=matter,
        due_within_days=due_within_days,
        settings=settings,
    )
