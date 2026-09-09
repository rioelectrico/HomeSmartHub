"""OpenAI Realtime WebSocket boundary; only internal events escape this module."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from time import monotonic
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

from websockets.asyncio.client import connect

from app.ai.provider import (
    ConfiguredOpenAIRealtimeProvider,
    ProviderStatus,
    resolve_realtime_model,
)
from app.ai.realtime import (
    AudioDelta,
    ProviderFailure,
    RealtimeEvent,
    RealtimeSessionConfig,
    ResponseFinished,
    ResponseStarted,
    SessionReady,
    SpeechStarted,
    SpeechStopped,
    TranscriptFinal,
)
from app.config import Settings
from app.models import MessageRole

# websockets logs handshake headers at DEBUG. Use a private, disabled logger,
# independent of application/root logging configuration, to protect credentials.
_TRANSPORT_LOGGER = logging.Logger("portero.realtime.transport")
_TRANSPORT_LOGGER.disabled = True
_TRANSPORT_LOGGER.propagate = False
_FAILURE = "AI_UNAVAILABLE"
_WEBSOCKET_CLOSE_TIMEOUT = 2.0
# Give websockets time to perform its own timeout/abort before our fallback.
_CLOSE_TIMEOUT = 3.0
_ABORT_TIMEOUT = 1.0
_CANCEL_CORRELATION_SECONDS = 30.0
_MAX_PENDING_CANCELS = 64


class _AbortableTransport(Protocol):
    def abort(self) -> None: ...


class _RealtimeTransport(Protocol):
    @property
    def transport(self) -> _AbortableTransport: ...

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


def _decode(message: str | bytes) -> dict[str, Any]:
    event = json.loads(message)
    if not isinstance(event, dict):
        raise ValueError(_FAILURE)
    return event


def _text(event: dict[str, Any], key: str) -> str:
    value = event.get(key)
    if not isinstance(value, str):
        raise ValueError(_FAILURE)
    return value


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _merge_dicts(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, update in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(update, dict):
            merged[key] = _merge_dicts(current, update)
        else:
            merged[key] = update
    return merged


def _normalize(event: dict[str, Any]) -> RealtimeEvent | None:
    match event.get("type"):
        case "input_audio_buffer.speech_started":
            return SpeechStarted()
        case "input_audio_buffer.speech_stopped":
            return SpeechStopped()
        case "response.created":
            return ResponseStarted()
        case "response.done":
            response = event.get("response")
            if not isinstance(response, dict):
                raise ValueError(_FAILURE)
            if response.get("status") == "failed":
                return ProviderFailure(code="AI_UNAVAILABLE")
            return ResponseFinished()
        case "response.output_audio.delta":
            return AudioDelta(base64.b64decode(_text(event, "delta"), validate=True))
        case "conversation.item.input_audio_transcription.completed":
            return TranscriptFinal(
                MessageRole.USER, _text(event, "transcript"), _text(event, "item_id")
            )
        case "response.output_audio_transcript.done":
            return TranscriptFinal(
                MessageRole.ASSISTANT, _text(event, "transcript"), _text(event, "item_id")
            )
        case "error" | "conversation.item.input_audio_transcription.failed":
            return ProviderFailure(code="AI_UNAVAILABLE")
        case _:
            # Includes session.created and duplicate session.updated notifications.
            return None


async def _close_transport(transport: _RealtimeTransport) -> bool:
    try:
        async with asyncio.timeout(_CLOSE_TIMEOUT):
            await transport.close()
    except Exception:
        # A timed-out/canceled graceful close doesn't guarantee TCP termination.
        # Abort synchronously, then join the connection-lost notification.
        with suppress(Exception):
            transport.transport.abort()
            async with asyncio.timeout(_ABORT_TIMEOUT):
                await transport.wait_closed()
        return False
    return True


async def _finish_cleanup[T](task: asyncio.Task[T]) -> T:
    """Join shared cleanup despite caller cancellation, then restore cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return task.result()


class OpenAIRealtimeSession:
    """A single consumer stream with backpressure and terminal, idempotent close."""

    def __init__(self, transport: _RealtimeTransport, on_failure: Callable[[], None]) -> None:
        self._transport = transport
        self._on_failure = on_failure
        self._closed = False
        self._pending_read: asyncio.Task[str | bytes] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._events = self._event_stream()
        self._pending_cancels: OrderedDict[str, float] = OrderedDict()

    def events(self) -> AsyncIterator[RealtimeEvent]:
        return self._events

    async def send_audio(self, pcm16: bytes) -> None:
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm16).decode("ascii"),
            }
        )

    async def cancel_response(self) -> None:
        self._prune_cancels()
        event_id = uuid4().hex
        self._pending_cancels[event_id] = monotonic()
        if len(self._pending_cancels) > _MAX_PENDING_CANCELS:
            self._pending_cancels.popitem(last=False)
        try:
            await self._send({"type": "response.cancel", "event_id": event_id})
        except BaseException:
            self._pending_cancels.pop(event_id, None)
            raise

    def _prune_cancels(self) -> None:
        cutoff = monotonic() - _CANCEL_CORRELATION_SECONDS
        while self._pending_cancels:
            if next(iter(self._pending_cancels.values())) > cutoff:
                break
            self._pending_cancels.popitem(last=False)

    def _recoverable_cancel_error(self, event: dict[str, Any]) -> bool:
        self._prune_cancels()
        if event.get("type") != "error":
            return False
        error = event.get("error")
        if not isinstance(error, dict):
            return False
        event_id = error.get("event_id")
        if not isinstance(event_id, str) or event_id not in self._pending_cancels:
            return False
        # Only the no-active-response race of OUR well-formed cancel is benign.
        # Other errors, malformed envelopes and uncorrelated events remain fatal.
        self._pending_cancels.pop(event_id)
        return (
            isinstance(event.get("event_id"), str)
            and bool(event["event_id"])
            and error.get("type") == "invalid_request_error"
            and error.get("code") == "response_cancel_not_active"
            and isinstance(error.get("message"), str)
            and bool(error["message"])
            and error.get("param") is None
        )

    async def _send(self, event: dict[str, str]) -> None:
        if self._closed:
            raise RuntimeError("session is closed")
        try:
            await self._transport.send(json.dumps(event))
        except Exception:
            self._on_failure()
        else:
            return
        await self.close()
        # Raise outside the handler so even __context__ has no provider payload.
        raise RuntimeError(_FAILURE)

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._pending_cancels.clear()
            pending = self._pending_read
            if pending is not None:
                pending.cancel()
            self._close_task = asyncio.create_task(self._cleanup(pending))
        await _finish_cleanup(self._close_task)

    async def _cleanup(self, pending: asyncio.Task[str | bytes] | None) -> None:
        # Join only the transport receive, never its consuming generator: that
        # generator can itself be awaiting this same cleanup from its finally.
        if pending is not None:
            with suppress(asyncio.CancelledError, Exception):
                await pending
        if not await _close_transport(self._transport):
            self._on_failure()

    async def _event_stream(self) -> AsyncIterator[RealtimeEvent]:
        try:
            if self._closed:
                return
            yield SessionReady()
            while not self._closed:
                try:
                    self._pending_read = asyncio.create_task(self._transport.recv())
                    raw = await self._pending_read
                    decoded = _decode(raw)
                    event = None if self._recoverable_cancel_error(decoded) else _normalize(decoded)
                except asyncio.CancelledError:
                    if self._closed:
                        return
                    raise
                except Exception:
                    event = ProviderFailure(code="AI_UNAVAILABLE")
                finally:
                    self._pending_read = None
                if self._closed:
                    return
                if isinstance(event, ProviderFailure):
                    self._on_failure()
                    await self.close()
                    yield event
                    return
                if event is not None:
                    yield event
        finally:
            await self.close()


class OpenAIRealtimeProvider(ConfiguredOpenAIRealtimeProvider):
    """Connect using SecretStr credentials and confirm configuration before opening."""

    def __init__(self, settings: Settings, *, open_timeout: float = 10.0) -> None:
        super().__init__(settings)
        self._settings = settings
        self._open_timeout = open_timeout

    def _unavailable(self) -> None:
        self._status = ProviderStatus.UNAVAILABLE

    async def open_session(self, config: RealtimeSessionConfig) -> OpenAIRealtimeSession:
        if self.diagnose() == ProviderStatus.UNCONFIGURED:
            raise RuntimeError(_FAILURE)
        if not config.voice.strip():
            self._unavailable()
            raise RuntimeError(_FAILURE)
        voice = "marin" if config.voice == "default" else config.voice
        key = self._settings.openai_api_key
        assert key is not None
        model = resolve_realtime_model(config.model, self._settings)
        transport: _RealtimeTransport | None = None
        cancelled = False
        try:
            # One deadline covers connect, configuration send and acknowledgement.
            async with asyncio.timeout(self._open_timeout):
                transport = await connect(
                    "wss://api.openai.com/v1/realtime?model=" + quote(model, safe=""),
                    additional_headers={"Authorization": "Bearer " + key.get_secret_value()},
                    open_timeout=self._open_timeout,
                    close_timeout=_WEBSOCKET_CLOSE_TIMEOUT,
                    logger=_TRANSPORT_LOGGER,
                )
                base_session = {
                    "type": "realtime",
                    "model": model,
                    "instructions": config.instructions,
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "turn_detection": {
                                "type": "server_vad",
                                "create_response": True,
                            },
                            "transcription": {
                                "model": "gpt-4o-mini-transcribe",
                                "language": config.language.split("-")[0].lower(),
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "voice": voice,
                            "speed": config.voice_speed,
                        },
                    },
                }
                session = _merge_dicts(
                    base_session, _dict(config.openai_session_options) if config.openai_session_options else {}
                )
                audio = _dict(session.get("audio"))
                audio_input = _dict(audio.get("input"))
                audio_output = _dict(audio.get("output"))
                audio.setdefault("input", audio_input)
                audio.setdefault("output", audio_output)
                audio_input.setdefault("format", {"type": "audio/pcm", "rate": 24000})
                audio_output.setdefault("format", {"type": "audio/pcm", "rate": 24000})
                transcription = _dict(audio_input.get("transcription"))
                transcription.setdefault("model", "gpt-4o-mini-transcribe")
                transcription.setdefault("language", config.language.split("-")[0].lower())
                audio_input["transcription"] = transcription
                audio_output["voice"] = voice
                audio_output["speed"] = config.voice_speed
                audio["input"] = audio_input
                audio["output"] = audio_output
                session["audio"] = audio
                await transport.send(
                    json.dumps(
                        {
                            "type": "session.update",
                            "session": session,
                        }
                    )
                )
                while True:
                    event = _decode(await transport.recv())
                    if event.get("type") == "session.updated":
                        break
                    if isinstance(_normalize(event), ProviderFailure):
                        raise RuntimeError(_FAILURE)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            self._unavailable()
        else:
            self._status = ProviderStatus.AVAILABLE
            return OpenAIRealtimeSession(transport, self._unavailable)
        # Cleanup may receive caller cancellation. Keep it outside raw exception
        # handlers so its CancelledError cannot chain provider error details.
        if transport is not None:
            await _finish_cleanup(asyncio.create_task(_close_transport(transport)))
        if cancelled:
            raise asyncio.CancelledError
        raise RuntimeError(_FAILURE)
