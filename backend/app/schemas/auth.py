"""Authentication HTTP contracts."""

from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class PermissionResponse(BaseModel):
    name: str


class HomeAccessResponse(BaseModel):
    id: UUID
    name: str
    timezone: str
    role: str
    permissions: list[str]


class CurrentUserResponse(BaseModel):
    id: UUID
    username: str
    email: str
    homes: list[HomeAccessResponse]
