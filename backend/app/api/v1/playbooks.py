import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_roles
from app.models.playbook import Playbook
from app.models.user import User
from app.schemas.playbook import PlaybookCreate, PlaybookRead, PlaybookUpdate
from app.services.seed import ensure_default_playbook

router = APIRouter(prefix="/playbooks", tags=["playbooks"])


@router.get("", response_model=list[PlaybookRead])
async def list_playbooks(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[PlaybookRead]:
    """List all firm contract review playbooks."""
    _ = current_user
    await ensure_default_playbook(session)
    stmt = select(Playbook).order_by(Playbook.is_default.desc(), Playbook.name.asc())
    rows = (await session.execute(stmt)).scalars().all()
    return [PlaybookRead.model_validate(r) for r in rows]


@router.post("", response_model=PlaybookRead, status_code=status.HTTP_201_CREATED)
async def create_playbook(
    payload: PlaybookCreate,
    current_user: User = Depends(require_roles("partner", "associate")),
    session: AsyncSession = Depends(get_db),
) -> PlaybookRead:
    """Create a reusable firm contract playbook with fallback clause positions."""
    existing = (
        await session.execute(select(Playbook).where(Playbook.name == payload.name.strip()))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Playbook '{payload.name.strip()}' already exists.",
        )

    if payload.is_default:
        await session.execute(update(Playbook).values(is_default=False))

    pb = Playbook(
        name=payload.name.strip(),
        practice_area=payload.practice_area.strip(),
        description=payload.description,
        is_default=payload.is_default,
        created_by_id=current_user.id,
        rules_json=[r.model_dump() for r in payload.rules],
    )
    session.add(pb)
    await session.commit()
    await session.refresh(pb)
    return PlaybookRead.model_validate(pb)


@router.get("/{playbook_id}", response_model=PlaybookRead)
async def get_playbook(
    playbook_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlaybookRead:
    """Get a single contract playbook."""
    _ = current_user
    pb = await session.get(Playbook, playbook_id)
    if pb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found.")
    return PlaybookRead.model_validate(pb)


@router.patch("/{playbook_id}", response_model=PlaybookRead)
async def update_playbook(
    playbook_id: uuid.UUID,
    payload: PlaybookUpdate,
    current_user: User = Depends(require_roles("partner", "associate")),
    session: AsyncSession = Depends(get_db),
) -> PlaybookRead:
    """Update playbook rules or metadata."""
    _ = current_user
    pb = await session.get(Playbook, playbook_id)
    if pb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found.")

    if payload.is_default:
        await session.execute(update(Playbook).values(is_default=False))

    if payload.name is not None:
        pb.name = payload.name.strip()
    if payload.practice_area is not None:
        pb.practice_area = payload.practice_area.strip()
    if payload.description is not None:
        pb.description = payload.description
    if payload.is_default is not None:
        pb.is_default = payload.is_default
    if payload.rules is not None:
        pb.rules_json = [r.model_dump() for r in payload.rules]

    await session.commit()
    await session.refresh(pb)
    return PlaybookRead.model_validate(pb)


@router.delete("/{playbook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playbook(
    playbook_id: uuid.UUID,
    current_user: User = Depends(require_roles("partner")),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a non-default playbook (partner role required)."""
    _ = current_user
    pb = await session.get(Playbook, playbook_id)
    if pb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found.")
    if pb.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the default firm playbook.",
        )
    await session.delete(pb)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
