from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_current_user, get_db
from app.core.config import Settings
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import TokenResponse, UserLogin, UserRead, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: UserRegister,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TokenResponse:
    """Register a new legal-ops user account and issue a bearer JWT."""
    normalized_email = payload.email.strip().lower()
    existing = (
        await session.execute(select(User).where(User.email == normalized_email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{normalized_email}' is already registered.",
        )

    user = User(
        email=normalized_email,
        full_name=payload.full_name.strip(),
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        settings=settings,
    )
    return TokenResponse(access_token=token, user=UserRead.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login_user(
    payload: UserLogin,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TokenResponse:
    """Authenticate by email and password and return a signed HS256 JWT."""
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

    token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        settings=settings,
    )
    return TokenResponse(access_token=token, user=UserRead.model_validate(user))


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    """Return profile and role of the currently authenticated user."""
    return UserRead.model_validate(current_user)


@router.get("/users", response_model=list[UserRead])
async def list_firm_users(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[UserRead]:
    """List active users available for matter team assignment."""
    _ = current_user
    stmt = select(User).where(User.is_active.is_(True)).order_by(User.full_name.asc())
    users = (await session.execute(stmt)).scalars().all()
    return [UserRead.model_validate(u) for u in users]
