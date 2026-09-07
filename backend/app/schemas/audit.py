"""Read-only home audit API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.events import PageQuery, public_context


class AuditQuery(PageQuery):
    action: str | None = Field(default=None, min_length=1, max_length=128)
    user_id: UUID | None = None


class AuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    home_id: UUID | None
    user_id: UUID | None
    action: str
    detail: str | None
    context: dict[str, object]
    created_at: datetime

    @field_validator("context")
    @classmethod
    def safe_context(cls, value: dict[str, object]) -> dict[str, object]:
        return public_context(value)


class AuditListResponse(BaseModel):
    items: list[AuditResponse] = Field(max_length=100)
    next_cursor: str | None
