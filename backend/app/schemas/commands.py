"""Strict HTTP and outbound wire contracts for device commands."""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models import CommandStatus

SupportedCommand = Literal["camera.capture", "device.status.request"]
MAX_COMMAND_PAYLOAD_BYTES = 8_192


class CommandPayloadTooLarge(ValueError):
    """Raised when serialized command parameters exceed the protocol bound."""


def validate_command_payload(payload: dict[str, object]) -> dict[str, object]:
    """Validate a deterministic UTF-8 JSON size before persistence or transport."""

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("command payload must be JSON serializable") from error
    if len(encoded) > MAX_COMMAND_PAYLOAD_BYTES:
        raise CommandPayloadTooLarge(f"payload exceeds {MAX_COMMAND_PAYLOAD_BYTES} bytes")
    return payload


class CommandRequestMessage(BaseModel):
    """Protocol V1 command frame sent to an authenticated device."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["command.request"] = "command.request"
    command_id: UUID
    command: SupportedCommand
    payload: dict[str, object] = Field(default_factory=dict)

    _bounded_payload = field_validator("payload")(validate_command_payload)


class CommandCreateRequest(BaseModel):
    """Operator request; unsupported names reach the stable domain error."""

    model_config = ConfigDict(extra="forbid")

    command: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    payload: dict[str, object] = Field(default_factory=dict)

    _bounded_payload = field_validator("payload")(validate_command_payload)


class CommandTimelineEntry(BaseModel):
    status: CommandStatus
    at: datetime


class CommandResponse(BaseModel):
    command_id: UUID
    device_id: UUID
    requested_by_user_id: UUID | None
    command: str
    payload: dict[str, object]
    status: CommandStatus
    result: dict[str, object] | None
    created_at: datetime
    timeline: list[CommandTimelineEntry]


class CommandsResponse(BaseModel):
    items: list[CommandResponse]
