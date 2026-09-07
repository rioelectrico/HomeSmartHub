"""Bounded keyset queries and safe event representations."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        timestamp, identifier = value.split("|")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed, UUID(identifier)
    except (ValueError, OverflowError) as error:
        raise ValueError("Invalid pagination cursor") from error


class PageQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=128)
    date_from: AwareDatetime | None = None
    date_to: AwareDatetime | None = None

    @field_validator("cursor")
    @classmethod
    def validate_cursor(cls, value: str | None) -> str | None:
        if value is not None:
            decode_cursor(value)
        return value

    @model_validator(mode="after")
    def ordered_dates(self) -> "PageQuery":
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from >= self.date_to
        ):
            raise ValueError("date_from must precede date_to")
        return self


class EventQuery(PageQuery):
    event_type: str | None = Field(default=None, min_length=1, max_length=128)
    device_id: UUID | None = None


_PUBLIC_CONTEXT_KEYS = frozenset(
    {
        "device_id",
        "command_id",
        "media_id",
        "command",
        "status",
        "error_code",
        "firmware_version",
        "hardware_model",
        "uptime_seconds",
        "ethernet",
        "camera",
        "microphone",
        "speaker",
        "free_heap_bytes",
        "ip_address",
        "created_user_id",
        "target_user_id",
        "role",
        "enabled",
        "voice",
    }
)


def public_context(value: dict[str, object]) -> dict[str, object]:
    """Expose only known scalar metadata, never arbitrary nested JSON or credentials."""
    return {
        key: item
        for key, item in value.items()
        if key in _PUBLIC_CONTEXT_KEYS and isinstance(item, (str, int, float, bool))
    }


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    home_id: UUID
    device_id: UUID | None
    event_type: str
    created_at: datetime
    payload: dict[str, object]

    @field_validator("payload")
    @classmethod
    def safe_payload(cls, value: dict[str, object]) -> dict[str, object]:
        return public_context(value)


class EventsResponse(BaseModel):
    items: Annotated[list[EventResponse], Field(max_length=100)]
    next_cursor: str | None
