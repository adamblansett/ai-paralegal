import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_current_user, get_db
from app.core.config import Settings
from app.core.rate_limit import rate_limit_auth
from app.core.security import (
    ALLOWED_ROLES,
    TokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.organization import Organization
from app.models.user import RefreshToken, RevokedToken, User
from app.schemas.auth import (
    AuthProvidersResponse,
    LogoutRequest,
    OIDCExchangeRequest,
    RefreshRequest,
    TokenResponse,
    UserLogin,
    UserRead,
    UserRegister,
)
from app.schemas.common import MessageResponse
from app.services.seed import ensure_default_organization

router = APIRouter(prefix="/auth", tags=["auth"])
_optional_bearer = HTTPBearer(auto_error=False)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


async def _issue_token_pair(
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> TokenResponse:
    access_token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        settings=settings,
    )
    raw_refresh, refresh_hash = generate_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=datetime.now(UTC) + timedelta(days=settings.auth_refresh_token_expire_days),
        )
    )
    await session.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.auth_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


_ROLE_RANK: dict[str, int] = {
    "partner": 4,
    "associate": 3,
    "paralegal": 2,
    "auditor": 1,
}


def map_oidc_groups_to_role(claims: dict[str, Any], settings: Settings) -> str:
    """Resolve highest-privilege firm role from OIDC group/role claims."""
    try:
        mapping = json.loads(settings.oidc_role_mapping_json or "{}")
    except Exception:
        mapping = {}
    if not isinstance(mapping, dict):
        mapping = {}

    raw_groups = claims.get(settings.oidc_role_claim) or claims.get("roles") or []
    if isinstance(raw_groups, str):
        groups = [g.strip() for g in raw_groups.split(",") if g.strip()]
    elif isinstance(raw_groups, list):
        groups = [str(g).strip() for g in raw_groups if str(g).strip()]
    else:
        groups = []

    matched_roles: list[str] = []
    for grp in groups:
        mapped = mapping.get(grp)
        if isinstance(mapped, str) and mapped in ALLOWED_ROLES:
            matched_roles.append(mapped)
        elif grp.lower() in ALLOWED_ROLES:
            matched_roles.append(grp.lower())

    if not matched_roles:
        return "associate"
    return max(matched_roles, key=lambda r: _ROLE_RANK.get(r, 0))


async def _resolve_org_for_registration(
    session: AsyncSession,
    *,
    slug: str | None,
    name: str | None,
    settings: Settings,
) -> Organization:
    if not slug or not slug.strip():
        return await ensure_default_organization(session, settings)
    norm_slug = slug.strip().lower()
    existing = (
        await session.execute(select(Organization).where(Organization.slug == norm_slug))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    org = Organization(
        slug=norm_slug,
        name=(name or norm_slug.replace("-", " ").title()).strip(),
        retention_days=2555,
        is_active=True,
    )
    session.add(org)
    await session.flush()
    return org


@router.get("/providers", response_model=AuthProvidersResponse)
async def get_auth_providers(
    settings: Settings = Depends(get_app_settings),
) -> AuthProvidersResponse:
    """Discover enabled authentication methods (local credentials and OIDC/SSO)."""
    return AuthProvidersResponse(
        local_enabled=True,
        oidc_enabled=settings.oidc_enabled,
        oidc_issuer_url=settings.oidc_issuer_url or None,
        oidc_client_id=settings.oidc_client_id or None,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
)
async def register_user(
    payload: UserRegister,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TokenResponse:
    """Register a new legal-ops user account and issue access + refresh tokens."""
    normalized_email = payload.email.strip().lower()
    existing = (
        await session.execute(select(User).where(User.email == normalized_email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{normalized_email}' is already registered.",
        )

    org = await _resolve_org_for_registration(
        session,
        slug=payload.organization_slug,
        name=payload.organization_name,
        settings=settings,
    )
    user = User(
        organization_id=org.id,
        email=normalized_email,
        full_name=payload.full_name.strip(),
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return await _issue_token_pair(session, user, settings)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit_auth)],
)
async def login_user(
    payload: UserLogin,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TokenResponse:
    """Authenticate by email and password and return rotated access + refresh tokens."""
    normalized_email = payload.email.strip().lower()
    user = (
        await session.execute(select(User).where(User.email == normalized_email))
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )

    return await _issue_token_pair(session, user, settings)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TokenResponse:
    """Exchange a valid refresh token for a fresh rotated access + refresh token pair."""
    token_hash = hash_refresh_token(payload.refresh_token)
    rt = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = datetime.now(UTC)
    if rt.revoked_at is not None:
        # Refresh token replay detected: revoke all active sessions for this user
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == rt.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected; all user sessions revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if _as_utc(rt.expires_at) < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await session.get(User, rt.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    rt.revoked_at = now
    raw_next, next_hash = generate_refresh_token()
    next_rt = RefreshToken(
        user_id=user.id,
        token_hash=next_hash,
        expires_at=now + timedelta(days=settings.auth_refresh_token_expire_days),
    )
    session.add(next_rt)
    await session.flush()
    rt.replaced_by_id = next_rt.id
    await session.commit()

    access_token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        settings=settings,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_next,
        expires_in=settings.auth_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post("/logout", response_model=MessageResponse)
async def logout_session(
    payload: LogoutRequest | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessageResponse:
    """Revoke the active access JWT (by jti) and optional refresh token."""
    now = datetime.now(UTC)
    if credentials is not None:
        try:
            claims = decode_access_token(credentials.credentials, settings)
            if claims.jti and await session.get(RevokedToken, claims.jti) is None:
                session.add(
                    RevokedToken(
                        jti=claims.jti,
                        user_id=current_user.id,
                        expires_at=datetime.fromtimestamp(claims.exp, tz=UTC),
                        revoked_at=now,
                    )
                )
        except TokenError:
            pass

    if payload and payload.refresh_token:
        rt_hash = hash_refresh_token(payload.refresh_token)
        rt = (
            await session.execute(select(RefreshToken).where(RefreshToken.token_hash == rt_hash))
        ).scalar_one_or_none()
        if rt is not None and rt.revoked_at is None:
            rt.revoked_at = now

    await session.commit()
    return MessageResponse(detail="Signed out and token revoked.")


@router.post(
    "/oidc/exchange",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit_auth)],
)
async def oidc_exchange(
    payload: OIDCExchangeRequest,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TokenResponse:
    """Exchange an enterprise OIDC/SSO ID token for a workspace session (when enabled)."""
    if not settings.oidc_enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="OIDC SSO is not enabled in this deployment (set OIDC_ENABLED=true).",
        )
    if not settings.oidc_issuer_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OIDC_ISSUER_URL is not configured.",
        )

    discovery_url = f"{settings.oidc_issuer_url.rstrip('/')}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            disc_resp = await client.get(discovery_url)
            disc_resp.raise_for_status()
            disc_data: dict[str, Any] = disc_resp.json()
            userinfo_ep = str(disc_data.get("userinfo_endpoint") or "")
            if not userinfo_ep:
                raise ValueError("OIDC discovery missing userinfo_endpoint.")
            ui_resp = await client.get(
                userinfo_ep,
                headers={"Authorization": f"Bearer {payload.id_token}"},
            )
            ui_resp.raise_for_status()
            claims: dict[str, Any] = ui_resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"OIDC token verification failed: {exc}",
        ) from exc

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC profile did not include an email address.",
        )

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        full_name = str(claims.get("name") or email.split("@")[0]).strip()
        resolved_role = map_oidc_groups_to_role(claims, settings)
        org_slug = claims.get("organization_slug") or claims.get("org")
        org = await _resolve_org_for_registration(
            session,
            slug=str(org_slug) if org_slug else None,
            name=None,
            settings=settings,
        )
        _, random_pw = generate_refresh_token()
        user = User(
            organization_id=org.id,
            email=email,
            full_name=full_name,
            role=resolved_role,
            password_hash=hash_password(random_pw),
            is_active=True,
        )
        session.add(user)
        await session.flush()
        await session.refresh(user)
    elif not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )

    return await _issue_token_pair(session, user, settings)


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    """Return profile and role of the currently authenticated user."""
    return UserRead.model_validate(current_user)


@router.get("/users", response_model=list[UserRead])
async def list_firm_users(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[UserRead]:
    """List active users available for matter team assignment within the caller's tenant."""
    stmt = select(User).where(User.is_active.is_(True)).order_by(User.full_name.asc())
    if current_user.organization_id is not None:
        stmt = stmt.where(User.organization_id == current_user.organization_id)
    users = (await session.execute(stmt)).scalars().all()
    return [UserRead.model_validate(u) for u in users]
