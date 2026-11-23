import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    format_actor_id,
    get_app_settings,
    get_db,
    get_storage,
    require_roles,
)
from app.core.config import Settings
from app.models.matter import Matter
from app.models.organization import Organization, WebhookSubscription
from app.models.user import User
from app.schemas.admin import (
    AdminUserUpdate,
    OrganizationRead,
    OrganizationUpdate,
    RetentionSweepReport,
    RetentionSweepRequest,
)
from app.schemas.auth import UserRead
from app.services.retention import run_retention_sweep
from app.storage.base import StorageBackend

router = APIRouter(prefix="/admin", tags=["admin"])


async def _load_caller_org(session: AsyncSession, current_user: User) -> Organization:
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Authenticated user is not bound to an organization tenant.",
        )
    org = await session.get(Organization, current_user.organization_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )
    return org


async def _build_org_read(session: AsyncSession, org: Organization) -> OrganizationRead:
    user_count = int(
        (
            await session.execute(
                select(func.count(User.id)).where(User.organization_id == org.id)
            )
        ).scalar_one()
    )
    matter_count = int(
        (
            await session.execute(
                select(func.count(Matter.id)).where(Matter.organization_id == org.id)
            )
        ).scalar_one()
    )
    webhook_count = int(
        (
            await session.execute(
                select(func.count(WebhookSubscription.id)).where(
                    WebhookSubscription.organization_id == org.id
                )
            )
        ).scalar_one()
    )
    read_obj = OrganizationRead.model_validate(org)
    read_obj.user_count = user_count
    read_obj.matter_count = matter_count
    read_obj.webhook_count = webhook_count
    return read_obj


@router.get("/organization", response_model=OrganizationRead)
async def get_organization(
    current_user: User = Depends(require_roles("partner", "associate", "paralegal", "auditor")),
    session: AsyncSession = Depends(get_db),
) -> OrganizationRead:
    """Return the authenticated caller's tenant organization and resource summary."""
    org = await _load_caller_org(session, current_user)
    return await _build_org_read(session, org)


@router.patch("/organization", response_model=OrganizationRead)
async def update_organization(
    payload: OrganizationUpdate,
    current_user: User = Depends(require_roles("partner")),
    session: AsyncSession = Depends(get_db),
) -> OrganizationRead:
    """Update tenant organization display name or default document retention window (partner)."""
    org = await _load_caller_org(session, current_user)
    if payload.name is not None:
        org.name = payload.name.strip()
    if payload.retention_days is not None:
        org.retention_days = payload.retention_days
    await session.commit()
    await session.refresh(org)
    return await _build_org_read(session, org)


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_tenant_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    current_user: User = Depends(require_roles("partner")),
    session: AsyncSession = Depends(get_db),
) -> UserRead:
    """Update a user's firm role or active status within the caller's tenant (partner only)."""
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if (
        current_user.organization_id is not None
        and target.organization_id != current_user.organization_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: user belongs to another organization tenant.",
        )

    removing_partner = (
        target.role == "partner"
        and target.is_active
        and (
            (payload.role is not None and payload.role != "partner")
            or (payload.is_active is False)
        )
    )
    if removing_partner:
        partner_stmt = select(func.count(User.id)).where(
            User.role == "partner",
            User.is_active.is_(True),
        )
        if current_user.organization_id is not None:
            partner_stmt = partner_stmt.where(
                User.organization_id == current_user.organization_id
            )
        active_partners = int((await session.execute(partner_stmt)).scalar_one())
        if active_partners <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote or deactivate the last active partner in the organization.",
            )

    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active
    await session.commit()
    await session.refresh(target)
    return UserRead.model_validate(target)


@router.post("/retention/sweep", response_model=RetentionSweepReport)
async def trigger_retention_sweep(
    payload: RetentionSweepRequest,
    current_user: User = Depends(require_roles("partner")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_storage),
) -> RetentionSweepReport:
    """Preview or execute a legal-hold-aware document retention purge for the caller's tenant."""
    return await run_retention_sweep(
        session,
        storage,
        organization_id=current_user.organization_id,
        as_of=payload.as_of,
        dry_run=payload.dry_run,
        actor_id=format_actor_id(current_user),
        settings=settings,
    )
