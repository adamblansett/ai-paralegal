import hmac
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db
from app.core.config import Settings
from app.core.security import ALLOWED_ROLES, hash_password
from app.models.organization import Organization
from app.models.user import RefreshToken, User

router = APIRouter(prefix="/scim/v2", tags=["scim"])
_scim_bearer = HTTPBearer(auto_error=False)
_USER_NAME_FILTER_RE = re.compile(r'userName\s+eq\s+"([^"]+)"', re.IGNORECASE)


def _require_scim_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_scim_bearer),
    settings: Settings = Depends(get_app_settings),
) -> None:
    if not settings.scim_enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SCIM 2.0 provisioning is disabled (set SCIM_ENABLED=true).",
        )
    expected = settings.scim_bearer_token.strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SCIM_BEARER_TOKEN is not configured.",
        )
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid SCIM bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _user_to_scim(user: User) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "externalId": user.scim_external_id,
        "userName": user.email,
        "name": {"formatted": user.full_name},
        "displayName": user.full_name,
        "emails": [{"value": user.email, "primary": True}],
        "roles": [{"value": user.role, "primary": True}],
        "active": user.is_active,
        "meta": {
            "resourceType": "User",
            "created": user.created_at.isoformat(),
            "lastModified": user.updated_at.isoformat(),
        },
    }


async def _resolve_scim_org_id(session: AsyncSession, settings: Settings) -> uuid.UUID | None:
    slug = settings.default_organization_slug.strip().lower()
    org = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if org is not None:
        return org.id
    first_org = (await session.execute(select(Organization).limit(1))).scalar_one_or_none()
    return first_org.id if first_org is not None else None


async def _revoke_user_refresh_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


@router.get("/Users", dependencies=[Depends(_require_scim_token)])
async def scim_list_users(
    filter: str | None = Query(default=None),
    startIndex: int = Query(default=1, ge=1),
    count: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List or filter SCIM 2.0 Users (supports `filter=userName eq \"email\"`)."""
    stmt = select(User).order_by(User.created_at.asc())
    if filter:
        match = _USER_NAME_FILTER_RE.search(filter)
        if match:
            stmt = stmt.where(User.email == match.group(1).strip().lower())

    users = list((await session.execute(stmt)).scalars().all())
    total = len(users)
    offset = max(0, startIndex - 1)
    page = users[offset : offset + count]
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": total,
        "startIndex": startIndex,
        "itemsPerPage": len(page),
        "Resources": [_user_to_scim(u) for u in page],
    }


@router.post(
    "/Users",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_scim_token)],
)
async def scim_create_user(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Provision a new firm user from Okta / Entra ID via SCIM 2.0."""
    raw_email = str(payload.get("userName") or "").strip().lower()
    if not raw_email:
        emails = payload.get("emails")
        if isinstance(emails, list) and emails and isinstance(emails[0], dict):
            raw_email = str(emails[0].get("value") or "").strip().lower()
    if not raw_email or "@" not in raw_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SCIM payload must provide a valid email userName.",
        )

    existing = (
        await session.execute(select(User).where(User.email == raw_email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"SCIM user '{raw_email}' already exists.",
        )

    raw_name = payload.get("name")
    name_obj: dict[str, Any] = raw_name if isinstance(raw_name, dict) else {}
    full_name = str(
        payload.get("displayName")
        or name_obj.get("formatted")
        or f"{name_obj.get('givenName', '')} {name_obj.get('familyName', '')}".strip()
        or raw_email.split("@")[0]
    ).strip()

    role = "associate"
    roles_raw = payload.get("roles")
    if isinstance(roles_raw, list) and roles_raw:
        first_role = roles_raw[0]
        candidate = (
            str(first_role.get("value") or "")
            if isinstance(first_role, dict)
            else str(first_role)
        ).strip().lower()
        if candidate in ALLOWED_ROLES:
            role = candidate

    org_id = await _resolve_scim_org_id(session, settings)
    user = User(
        organization_id=org_id,
        email=raw_email,
        full_name=full_name,
        role=role,
        password_hash=hash_password(uuid.uuid4().hex + uuid.uuid4().hex),
        is_active=bool(payload.get("active", True)),
        scim_external_id=str(payload.get("externalId")) if payload.get("externalId") else None,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _user_to_scim(user)


@router.get("/Users/{user_id}", dependencies=[Depends(_require_scim_token)])
async def scim_get_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SCIM user not found.")
    return _user_to_scim(user)


@router.patch("/Users/{user_id}", dependencies=[Depends(_require_scim_token)])
async def scim_patch_user(
    user_id: uuid.UUID,
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Process SCIM `PatchOp` updates (activation/deactivation, role sync, profile updates)."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SCIM user not found.")

    ops = payload.get("Operations") or []
    deactivated = False
    for op in ops:
        if not isinstance(op, dict):
            continue
        path = str(op.get("path") or "").strip().lower()
        val = op.get("value")

        if path == "" and isinstance(val, dict):
            if "active" in val:
                user.is_active = bool(val["active"])
                if not user.is_active:
                    deactivated = True
            if "displayName" in val and str(val["displayName"]).strip():
                user.full_name = str(val["displayName"]).strip()
            if "externalId" in val:
                user.scim_external_id = str(val["externalId"]) if val["externalId"] else None
        elif path == "active":
            user.is_active = bool(val)
            if not user.is_active:
                deactivated = True
        elif path in {"displayname", "name.formatted"} and val:
            user.full_name = str(val).strip()
        elif path == "externalid":
            user.scim_external_id = str(val) if val else None
        elif path == "roles" and isinstance(val, list) and val:
            first = val[0]
            cand = str(first.get("value") if isinstance(first, dict) else first).strip().lower()
            if cand in ALLOWED_ROLES:
                user.role = cand

    if deactivated:
        await _revoke_user_refresh_tokens(session, user.id)

    await session.commit()
    await session.refresh(user)
    return _user_to_scim(user)


@router.delete(
    "/Users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_scim_token)],
)
async def scim_delete_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Deprovision/deactivate a SCIM user and immediately revoke active refresh tokens."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SCIM user not found.")
    user.is_active = False
    await _revoke_user_refresh_tokens(session, user.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
