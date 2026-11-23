import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import TokenError, decode_access_token
from app.db.rls import set_tenant_rls_context
from app.db.session import get_db as _get_db
from app.llm.embeddings import EmbeddingService
from app.llm.router import ModelRouter
from app.models.document import Document
from app.models.finding import ExtractedFinding
from app.models.matter import Matter
from app.models.user import MatterMembership, RevokedToken, User
from app.storage import StorageBackend, get_storage_backend

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in _get_db():
        yield session


def get_app_settings() -> Settings:
    return get_settings()


def get_storage() -> StorageBackend:
    return get_storage_backend(get_settings())


def get_router() -> ModelRouter:
    return ModelRouter(get_settings())


def get_embeddings() -> EmbeddingService:
    return EmbeddingService(get_settings())


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    token_query: str | None = Query(default=None, alias="token"),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> User:
    """Resolve and validate the authenticated firm user from Bearer header or `?token=`."""
    raw_token = credentials.credentials if credentials is not None else token_query
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = decode_access_token(raw_token, settings)
        user_id = uuid.UUID(claims.sub)
    except (TokenError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired authentication token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if claims.jti:
        revoked = await session.get(RevokedToken, claims.jti)
        if revoked is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication token has been revoked.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user account not found or inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    await set_tenant_rls_context(session, user.organization_id)
    return user


def require_roles(*allowed_roles: str) -> Callable[..., Coroutine[Any, Any, User]]:
    """Dependency factory enforcing firm-level role membership."""

    async def _role_guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user.role}' is not permitted for this action "
                    f"(requires one of: {', '.join(allowed_roles)})."
                ),
            )
        return user

    return _role_guard


def format_actor_id(user: User) -> str:
    """Build cryptographic audit actor string from authenticated user claims."""
    return f"{user.full_name} <{user.email}> [{user.role}]"


async def require_matter_access(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    user: User,
    write: bool = False,
) -> Matter:
    """Enforce tenant isolation + role + matter-level membership permissions and return Matter."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

    # Strict multi-tenant isolation boundary checked before firm role shortcuts
    if (
        user.organization_id is not None
        and matter.organization_id is not None
        and user.organization_id != matter.organization_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: matter belongs to another organization tenant.",
        )

    if user.role == "auditor":
        if write:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Auditor role has read-only access and cannot modify matter resources.",
            )
        return matter

    if user.role == "partner":
        return matter

    stmt = select(MatterMembership).where(
        MatterMembership.matter_id == matter_id,
        MatterMembership.user_id == user.id,
    )
    membership = (await session.execute(stmt)).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: you are not assigned to this matter.",
        )

    if write and membership.access_level == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewer membership cannot modify matter documents or findings.",
        )
    return matter


async def require_document_access(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    user: User,
    write: bool = False,
) -> Document:
    """Load Document and enforce matter-level RBAC on its parent matter."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    await require_matter_access(session, matter_id=doc.matter_id, user=user, write=write)
    return doc


async def require_finding_access(
    session: AsyncSession,
    *,
    finding_id: uuid.UUID,
    user: User,
    write: bool = False,
) -> ExtractedFinding:
    """Load ExtractedFinding and enforce matter-level RBAC on its parent matter."""
    finding = await session.get(ExtractedFinding, finding_id)
    if finding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found.")
    await require_matter_access(session, matter_id=finding.matter_id, user=user, write=write)
    return finding
