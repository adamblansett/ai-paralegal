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


class UserLogin(BaseModel):
    email: str = Field(..., min_length=4, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


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
