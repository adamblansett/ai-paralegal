import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.document import Document
from app.models.finding import ExtractedFinding
from app.models.matter import Matter
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


@router.post("", response_model=MatterRead, status_code=status.HTTP_201_CREATED)
async def create_matter(
    payload: MatterCreate,
    session: AsyncSession = Depends(get_db),
) -> MatterRead:
    """Create a new legal matter/case."""
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

    await record_audit_log(
        session,
        matter_id=matter.id,
        action="matter_created",
        actor_type="human",
        actor_id="legal_ops_user",
        new_state="active",
        notes=f"Created matter {matter.matter_number}: {matter.title}",
    )
    await session.commit()
    await session.refresh(matter)
    return _to_matter_read(matter, MatterStats())


@router.get("", response_model=list[MatterRead])
async def list_matters(
    status_filter: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[MatterRead]:
    """List matters ordered by most recently updated."""
    stmt = select(Matter).order_by(Matter.updated_at.desc())
    if status_filter:
        stmt = stmt.where(Matter.status == status_filter)
    matters = list((await session.execute(stmt)).scalars().all())
    stats_map = await _compute_matter_stats(session, [m.id for m in matters])
    return [_to_matter_read(m, stats_map[m.id]) for m in matters]


@router.get("/{matter_id}", response_model=MatterRead)
async def get_matter(
    matter_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> MatterRead:
    """Retrieve a single matter with document and finding summary counts."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")
    stats_map = await _compute_matter_stats(session, [matter.id])
    return _to_matter_read(matter, stats_map[matter.id])


@router.patch("/{matter_id}", response_model=MatterRead)
async def update_matter(
    matter_id: uuid.UUID,
    payload: MatterUpdate,
    session: AsyncSession = Depends(get_db),
) -> MatterRead:
    """Update matter metadata or status."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

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
            actor_id="legal_ops_user",
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
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a matter and its cascading records."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")
    await session.delete(matter)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
