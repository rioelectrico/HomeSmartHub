"""Byte-exact helpers for Portero protocol V1."""

import hmac
import random
from collections.abc import Callable, Iterator
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError


def build_hmac(secret: str, device_id: str, boot_id: str, nonce: str) -> str:
    """Return the lowercase HMAC-SHA256 digest expected by the backend."""

    canonical = f"v1\n{device_id}\n{boot_id}\n{nonce}".encode()
    return hmac.new(secret.encode("utf-8"), canonical, sha256).hexdigest()


def backoff_delays(
    *,
    max_seconds: float = 30,
    jitter: bool = True,
    jitter_source: Callable[[float, float], float] = random.uniform,
) -> Iterator[float]:
    """Yield the V1 retry schedule forever, optionally with full jitter."""

    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    retry_seconds = (1.0, 2.0, 4.0, 8.0, 15.0)
    attempt = 0
    while True:
        base = min(retry_seconds[min(attempt, len(retry_seconds) - 1)], max_seconds)
        if attempt >= len(retry_seconds):
            base = max_seconds
        yield jitter_source(0.0, base) if jitter else base
        attempt += 1


class ProtocolError(ValueError):
    """Raised when the backend sends a frame outside protocol V1."""


class _StrictServerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthChallenge(_StrictServerMessage):
    type: Literal["auth.challenge"]
    algorithm: Literal["HMAC-SHA256"]
    nonce: Annotated[
        str,
        StringConstraints(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", strict=True),
    ]
    expires_at: datetime


class AuthOk(_StrictServerMessage):
    type: Literal["auth.ok"]
    upload_token: Annotated[str, StringConstraints(min_length=1, strict=True)]
    upload_expires_at: datetime
    heartbeat_interval_seconds: int = Field(gt=0)


class CommandRequest(_StrictServerMessage):
    type: Literal["command.request"]
    command_id: UUID
    command: Annotated[str, StringConstraints(min_length=1, max_length=128, strict=True)]
    payload: dict[str, object]


ServerMessage = Annotated[
    AuthChallenge | AuthOk | CommandRequest,
    Field(discriminator="type"),
]
_server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


def parse_server_message(payload: str | bytes) -> ServerMessage:
    """Parse one strict text frame from the backend."""

    if isinstance(payload, bytes):
        raise ProtocolError("binary backend frame is not valid protocol V1")
    try:
        return _server_message_adapter.validate_json(payload)
    except (ValidationError, ValueError, RecursionError) as error:
        raise ProtocolError("invalid backend protocol V1 frame") from error
