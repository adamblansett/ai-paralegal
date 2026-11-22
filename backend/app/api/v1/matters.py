import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    format_actor_id,
    get_current_user,
    get_db,
    require_matter_access,
    require_roles,
)
from app.models.document import Document
from app.models.finding import ExtractedFinding
from app.models.matter import Matter
from app.models.user import MatterMembership, User
from app.schemas.auth import MatterMemberAdd, MatterMemberRead
from app.schemas.matter import MatterCreate, MatterRead, MatterStats, MatterUpdate
from app.services.audit import record_audit_log

router = APIRouter(prefix="/matters", tags=["matters"])


async def _compute_matter_stats(
    session: AsyncSession, matter_ids: list[uuid.UUID]
) -> dict[uuid.UUID, MatterStats]:
    stats_map: dict[uuid.UUID, MatterStats] = {mid: MatterStats() for mid in matter_ids}
    if not matter_ids:
        return stats_map

    doc_rows = (
        await session.execute(
            select(Document.matter_id, Document.ingestion_status, func.count(Document.id))
            .where(Document.matter_id.in_(matter_ids))
            .group_by(Document.matter_id, Document.ingestion_status)
        )
    ).all()
    for mid, ing_status, cnt in doc_rows:
        st = stats_map[mid]
        st.document_count += int(cnt)
        if ing_status == "indexed":
            st.indexed_document_count += int(cnt)

    finding_rows = (
        await session.execute(
            select(
                ExtractedFinding.matter_id,
                ExtractedFinding.review_status,
                func.count(ExtractedFinding.id),
            )
            .where(ExtractedFinding.matter_id.in_(matter_ids))
            .group_by(ExtractedFinding.matter_id, ExtractedFinding.review_status)
        )
    ).all()
    for mid, rev_status, cnt in finding_rows:
        st = stats_map[mid]
        st.finding_count += int(cnt)
        if rev_status == "pending_review":
            st.pending_review_count += int(cnt)
        elif rev_status == "flagged":
            st.flagged_count += int(cnt)
        elif rev_status == "approved":
            st.approved_count += int(cnt)

    return stats_map


def _to_matter_read(matter: Matter, stats: MatterStats) -> MatterRead:
    read_obj = MatterRead.model_validate(matter)
    read_obj.stats = stats
    return read_obj


async def _require_matter_manager(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    user: User,
) -> Matter:
    matter = await require_matter_access(session, matter_id=matter_id, user=user, write=True)
    if user.role == "partner":
        return matter
    membership = (
        await session.execute(
            select(MatterMembership).where(
                MatterMembership.matter_id == matter_id,
                MatterMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None or membership.access_level != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a partner or matter owner may manage matter membership or delete matters.",
        )
    return matter


@router.post("", response_model=MatterRead, status_code=status.HTTP_201_CREATED)
async def create_matter(
    payload: MatterCreate,
    current_user: User = Depends(require_roles("partner", "associate", "paralegal")),
    session: AsyncSession = Depends(get_db),
) -> MatterRead:
    """Create a new legal matter/case and assign the creator as matter owner."""
    matter_number = (
        payload.matter_number.strip()
        if payload.matter_number and payload.matter_number.strip()
        else f"MAT-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    )

    existing = (
        await session.execute(select(Matter).where(Matter.matter_number == matter_number))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Matter number '{matter_number}' already exists.",
        )

    matter = Matter(
        matter_number=matter_number,
        title=payload.title.strip(),
        client_name=payload.client_name.strip(),
        practice_area=payload.practice_area.strip(),
        jurisdiction=payload.jurisdiction.strip() if payload.jurisdiction else None,
        description=payload.description,
        metadata_json=payload.metadata_json,
    )
    session.add(matter)
    await session.flush()

    session.add(
        MatterMembership(
            matter_id=matter.id,
            user_id=current_user.id,
            access_level="owner",
            added_by_id=current_user.id,
        )
    )
    await session.flush()

    await record_audit_log(
        session,
        matter_id=matter.id,
        action="matter_created",
        actor_type="human",
        actor_id=format_actor_id(current_user),
        new_state="active",
        notes=f"Created matter {matter.matter_number}: {matter.title}",
    )
    await session.commit()
    await session.refresh(matter)
    return _to_matter_read(matter, MatterStats())


@router.get("", response_model=list[MatterRead])
async def list_matters(
    status_filter: str | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[MatterRead]:
    """List matters visible to the authenticated user based on role and memberships."""
    stmt = select(Matter).order_by(Matter.updated_at.desc())
    if current_user.role not in {"partner", "auditor"}:
        stmt = stmt.join(
            MatterMembership,
            MatterMembership.matter_id == Matter.id,
        ).where(MatterMembership.user_id == current_user.id)

    if status_filter:
        stmt = stmt.where(Matter.status == status_filter)
    matters = list((await session.execute(stmt)).scalars().all())
    stats_map = await _compute_matter_stats(session, [m.id for m in matters])
    return [_to_matter_read(m, stats_map[m.id]) for m in matters]


@router.get("/{matter_id}", response_model=MatterRead)
async def get_matter(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MatterRead:
    """Retrieve a single matter with document and finding summary counts."""
    matter = await require_matter_access(
        session, matter_id=matter_id, user=current_user, write=False
    )
    stats_map = await _compute_matter_stats(session, [matter.id])
    return _to_matter_read(matter, stats_map[matter.id])


@router.patch("/{matter_id}", response_model=MatterRead)
async def update_matter(
    matter_id: uuid.UUID,
    payload: MatterUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MatterRead:
    """Update matter metadata or status."""
    matter = await require_matter_access(
        session, matter_id=matter_id, user=current_user, write=True
    )

    prev_status = matter.status
    updates = payload.model_dump(exclude_unset=True)
    for field_name, val in updates.items():
        setattr(matter, field_name, val)

    if "status" in updates and updates["status"] != prev_status:
        await record_audit_log(
            session,
            matter_id=matter.id,
            action="matter_status_updated",
            actor_type="human",
            actor_id=format_actor_id(current_user),
            previous_state=prev_status,
            new_state=matter.status,
        )

    await session.commit()
    await session.refresh(matter)
    stats_map = await _compute_matter_stats(session, [matter.id])
    return _to_matter_read(matter, stats_map[matter.id])


@router.delete("/{matter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_matter(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a matter and its cascading records (partner or owner required)."""
    matter = await _require_matter_manager(session, matter_id=matter_id, user=current_user)
    await session.delete(matter)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{matter_id}/members", response_model=list[MatterMemberRead])
async def list_matter_members(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[MatterMemberRead]:
    """List users assigned to a legal matter."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    stmt = (
        select(MatterMembership, User)
        .join(User, MatterMembership.user_id == User.id)
        .where(MatterMembership.matter_id == matter_id)
        .order_by(MatterMembership.created_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        MatterMemberRead(
            id=mem.id,
            matter_id=mem.matter_id,
            user_id=user.id,
            user_email=user.email,
            user_full_name=user.full_name,
            user_role=user.role,
            access_level=mem.access_level,
            created_at=mem.created_at,
        )
        for mem, user in rows
    ]


@router.post(
    "/{matter_id}/members",
    response_model=MatterMemberRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_matter_member(
    matter_id: uuid.UUID,
    payload: MatterMemberAdd,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MatterMemberRead:
    """Grant or update a user's access membership on a matter."""
    await _require_matter_manager(session, matter_id=matter_id, user=current_user)
    target_user = await session.get(User, payload.user_id)
    if target_user is None or not target_user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found.")

    existing = (
        await session.execute(
            select(MatterMembership).where(
                MatterMembership.matter_id == matter_id,
                MatterMembership.user_id == payload.user_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        prev_level = existing.access_level
        existing.access_level = payload.access_level
        membership = existing
        action_note = (
            f"Updated {target_user.email} access from {prev_level} to {payload.access_level}"
        )
    else:
        membership = MatterMembership(
            matter_id=matter_id,
            user_id=payload.user_id,
            access_level=payload.access_level,
            added_by_id=current_user.id,
        )
        session.add(membership)
        await session.flush()
        action_note = f"Granted {payload.access_level} access to {target_user.email}"

    await record_audit_log(
        session,
        matter_id=matter_id,
        action="matter_member_updated",
        actor_type="human",
        actor_id=format_actor_id(current_user),
        new_state=payload.access_level,
        notes=action_note,
    )
    await session.commit()
    await session.refresh(membership)

    return MatterMemberRead(
        id=membership.id,
        matter_id=membership.matter_id,
        user_id=target_user.id,
        user_email=target_user.email,
        user_full_name=target_user.full_name,
        user_role=target_user.role,
        access_level=membership.access_level,
        created_at=membership.created_at,
    )


@router.delete("/{matter_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_matter_member(
    matter_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke a user's membership on a matter."""
    await _require_matter_manager(session, matter_id=matter_id, user=current_user)
    membership = (
        await session.execute(
            select(MatterMembership).where(
                MatterMembership.matter_id == matter_id,
                MatterMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Matter membership not found."
        )

    await record_audit_log(
        session,
        matter_id=matter_id,
        action="matter_member_removed",
        actor_type="human",
        actor_id=format_actor_id(current_user),
        previous_state=membership.access_level,
        notes=f"Removed user {user_id} from matter",
    )
    await session.delete(membership)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
