"""HTTP contracts for home access listings."""

from uuid import UUID

from pydantic import BaseModel, Field


class HomeResponse(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=128)
    timezone: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=64)
    permissions: list[str]


class HomesResponse(BaseModel):
    items: list[HomeResponse]
