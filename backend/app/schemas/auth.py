import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UserRole = Literal["partner", "associate", "paralegal", "auditor"]
AccessLevel = Literal["owner", "editor", "reviewer", "viewer"]


class UserRegister(BaseModel):
    email: str = Field(..., min_length=4, max_length=255)
    full_name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole = "associate"
    organization_slug: str | None = Field(
        default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$"
    )
    organization_name: str | None = Field(default=None, min_length=2, max_length=255)


class UserLogin(BaseModel):
    email: str = Field(..., min_length=4, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)
    totp_code: str | None = Field(default=None, min_length=6, max_length=12)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None = None
    email: str
    full_name: str
    role: str
    is_active: bool
    mfa_enabled: bool = False
    created_at: datetime


class MFAEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MFAVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=12)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int = 28800
    token_type: str = "bearer"
    user: UserRead


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=16, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class AuthProvidersResponse(BaseModel):
    local_enabled: bool = True
    oidc_enabled: bool = False
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None


class OIDCExchangeRequest(BaseModel):
    id_token: str = Field(..., min_length=10)
    nonce: str | None = Field(default=None, max_length=256)
    access_token: str | None = Field(default=None, min_length=10)


class MatterMemberAdd(BaseModel):
    user_id: uuid.UUID
    access_level: AccessLevel = "editor"


class MatterMemberRead(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    user_full_name: str
    user_role: str
    access_level: str
    created_at: datetime
