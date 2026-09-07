"""Strict HTTP and WebSocket contracts for devices."""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from app.schemas.conversations import (
    ConversationAudioOutputOverflow,
    ConversationStart,
    ConversationStop,
)

DEVICE_ID_PATTERN = r"^PI-[0-9]{6}$"
MAX_SEQUENCE = 9_223_372_036_854_775_807
MAX_RESULT_BYTES = 8_192

DeviceIdentifier = Annotated[
    str,
    StringConstraints(pattern=DEVICE_ID_PATTERN, strict=True),
]
BootIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", strict=True),
]
Nonce = Annotated[
    str,
    StringConstraints(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", strict=True),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$", strict=True),
]
Sequence = Annotated[StrictInt, Field(ge=0, le=MAX_SEQUENCE)]
Uptime = Annotated[StrictInt, Field(ge=0, le=MAX_SEQUENCE)]
DeviceErrorCode = Literal[
    "AUTH_FAILED",
    "PROTOCOL_VERSION_UNSUPPORTED",
    "INVALID_COMMAND",
    "INVALID_PARAMETER",
    "DEVICE_BUSY",
    "CAMERA_NOT_READY",
    "CAMERA_CAPTURE_FAILED",
    "MIC_NOT_READY",
    "SPEAKER_NOT_READY",
    "INTERNAL_ERROR",
]
DeviceCapability = Literal["audio_pcm16_v1"]


class StrictMessage(BaseModel):
    """Base for wire messages that rejects undocumented input."""

    model_config = ConfigDict(extra="forbid")

    boot_id: BootIdentifier
    seq: Sequence


class DeviceHello(StrictMessage):
    type: Literal["device.hello"]
    version: Literal["v1"]
    device_id: DeviceIdentifier
    capabilities: list[DeviceCapability] = Field(default_factory=list, max_length=8)


class AuthResponse(StrictMessage):
    type: Literal["auth.response"]
    nonce: Nonce
    digest: Sha256Digest


class DeviceHeartbeat(StrictMessage):
    type: Literal["device.heartbeat"]
    uptime_seconds: Uptime


class DeviceStatusMessage(StrictMessage):
    type: Literal["device.status"]
    firmware_version: Annotated[str, StringConstraints(min_length=1, max_length=64, strict=True)]
    hardware_model: Annotated[str, StringConstraints(min_length=1, max_length=128, strict=True)]
    uptime_seconds: Uptime
    ethernet: Literal["online", "offline", "error"]
    camera: Literal["ready", "unavailable", "error"]
    microphone: Literal["ready", "unavailable", "error"]
    speaker: Literal["ready", "unavailable", "error"]
    free_heap_bytes: Annotated[StrictInt, Field(ge=0, le=MAX_SEQUENCE)]


class CommandAck(StrictMessage):
    type: Literal["command.ack"]
    command_id: UUID
    status: Literal["accepted", "rejected"]
    error_code: DeviceErrorCode | None = None

    @model_validator(mode="after")
    def validate_error_code(self) -> "CommandAck":
        """Require failure metadata exactly when the ACK rejects a command."""

        if (self.status == "rejected") != (self.error_code is not None):
            raise ValueError("rejected ACK requires error_code; accepted ACK forbids it")
        return self


class CommandResult(StrictMessage):
    type: Literal["command.result"]
    command_id: UUID
    status: Literal["completed", "failed"]
    result: dict[str, object] = Field(default_factory=dict)
    error_code: DeviceErrorCode | None = None

    @model_validator(mode="after")
    def validate_result_size(self) -> "CommandResult":
        """Bound variable result data before it reaches durable storage."""

        if (
            len(
                json.dumps(
                    self.result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            > MAX_RESULT_BYTES
        ):
            raise ValueError(f"result exceeds {MAX_RESULT_BYTES} bytes")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("failed result requires error_code; completed result forbids it")
        return self


DeviceInboundMessage = Annotated[
    DeviceHello
    | AuthResponse
    | DeviceHeartbeat
    | DeviceStatusMessage
    | CommandAck
    | CommandResult
    | ConversationStart
    | ConversationStop
    | ConversationAudioOutputOverflow,
    Field(discriminator="type"),
]


class DeviceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: DeviceIdentifier
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class DeviceResponse(BaseModel):
    id: UUID
    home_id: UUID
    device_id: DeviceIdentifier
    name: str
    status: Literal["online", "offline", "provisioning", "disabled"]
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DevicesResponse(BaseModel):
    items: list[DeviceResponse]


class ProvisionedDeviceResponse(DeviceResponse):
    secret: str = Field(min_length=32, max_length=128)
