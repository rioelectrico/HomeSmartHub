"""Transport-neutral contracts for Realtime voice sessions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from app.ai.provider import ProviderStatus
from app.models import MessageRole


@dataclass(frozen=True, slots=True)
class RealtimeSessionConfig:
    """The internal session settings required by every Realtime transport."""

    model: str
    instructions: str
    voice: str
    language: str


@dataclass(frozen=True, slots=True)
class SessionReady:
    """The transport has confirmed the requested session configuration."""


@dataclass(frozen=True, slots=True)
class SpeechStarted:
    """User speech has started."""


@dataclass(frozen=True, slots=True)
class SpeechStopped:
    """User speech has stopped."""


@dataclass(frozen=True, slots=True)
class ResponseStarted:
    """The assistant has started a response."""


@dataclass(frozen=True, slots=True)
class AudioDelta:
    """A PCM16 audio chunk from the assistant response."""

    pcm16: bytes


@dataclass(frozen=True, slots=True)
class TranscriptFinal:
    """A completed transcript item using the persisted conversation role."""

    role: MessageRole
    text: str
    item_id: str


@dataclass(frozen=True, slots=True)
class ResponseFinished:
    """The assistant response has finished."""


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """A provider-level failure expressed without external transport payloads."""

    code: Literal["AI_UNAVAILABLE"]


type RealtimeEvent = (
    SessionReady
    | SpeechStarted
    | SpeechStopped
    | ResponseStarted
    | AudioDelta
    | TranscriptFinal
    | ResponseFinished
    | ProviderFailure
)


class AIRealtimeSession(Protocol):
    """An opened Realtime session with a single ordered event stream."""

    async def send_audio(self, pcm16: bytes) -> None: ...

    def events(self) -> AsyncIterator[RealtimeEvent]: ...

    async def cancel_response(self) -> None: ...

    async def close(self) -> None: ...


class AIRealtimeProvider(Protocol):
    """A provider capable of diagnosing and opening internal Realtime sessions."""

    def diagnose(self) -> ProviderStatus: ...

    async def open_session(self, config: RealtimeSessionConfig) -> AIRealtimeSession: ...
