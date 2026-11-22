from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.models.organization import Organization
from app.models.playbook import Playbook
from app.models.user import User
from app.services.playbooks import DEFAULT_SAAS_MSA_RULES

DEMO_USERS: list[tuple[str, str, str, str]] = [
    ("partner@firm.example.com", "Elena Rostova, Esq.", "partner", "ParalegalDemo2026!"),
    ("associate@firm.example.com", "David Park, Esq.", "associate", "ParalegalDemo2026!"),
    ("paralegal@firm.example.com", "Maya Lin (Paralegal)", "paralegal", "ParalegalDemo2026!"),
    ("auditor@firm.example.com", "Compliance Auditor", "auditor", "ParalegalDemo2026!"),
]


async def ensure_default_organization(
    session: AsyncSession, settings: Settings | None = None
) -> Organization:
    """Idempotently ensure the default law-firm organization tenant exists."""
    cfg = settings or get_settings()
    slug = cfg.default_organization_slug.strip().lower()
    existing = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    org = Organization(
        slug=slug,
        name=cfg.default_organization_name,
        retention_days=2555,
        is_active=True,
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


async def ensure_default_playbook(session: AsyncSession) -> Playbook:
    """Idempotently seed the default Commercial SaaS / MSA playbook."""
    existing = (
        await session.execute(select(Playbook).where(Playbook.is_default.is_(True)))
    ).scalars().first()
    if existing is not None:
        return existing

    by_name = (
        await session.execute(
            select(Playbook).where(Playbook.name == "Commercial SaaS / MSA Standard Playbook")
        )
    ).scalar_one_or_none()
    if by_name is not None:
        by_name.is_default = True
        await session.commit()
        return by_name

    playbook = Playbook(
        name="Commercial SaaS / MSA Standard Playbook",
        practice_area="Commercial Contracts",
        description=(
            "Baseline client-protective commercial contracting positions, red-flag triggers, "
            "and ready-to-insert fallback redline clauses."
        ),
        is_default=True,
        rules_json=DEFAULT_SAAS_MSA_RULES,
    )
    session.add(playbook)
    await session.commit()
    await session.refresh(playbook)
    return playbook


async def ensure_demo_users(
    session: AsyncSession,
    settings: Settings | None = None,
    *,
    default_org: Organization | None = None,
) -> list[User]:
    """Idempotently seed demo firm roles when `AUTH_AUTO_SEED_DEMO_USERS=true`."""
    cfg = settings or get_settings()
    if not cfg.auth_auto_seed_demo_users:
        return []

    org = default_org or await ensure_default_organization(session, cfg)
    created_or_existing: list[User] = []
    dirty = False
    for email, full_name, role, password in DEMO_USERS:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.organization_id is None:
                existing.organization_id = org.id
                dirty = True
            created_or_existing.append(existing)
            continue
        user = User(
            organization_id=org.id,
            email=email,
            full_name=full_name,
            role=role,
            password_hash=hash_password(password),
            is_active=True,
        )
        session.add(user)
        created_or_existing.append(user)
        dirty = True

    if dirty:
        await session.commit()
    return created_or_existing


async def seed_defaults(session: AsyncSession, settings: Settings | None = None) -> None:
    """Seed default organization tenant, baseline playbook, and demo RBAC accounts."""
    org = await ensure_default_organization(session, settings)
    await ensure_default_playbook(session)
    await ensure_demo_users(session, settings, default_org=org)
