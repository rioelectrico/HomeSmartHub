"""Realtime contracts and real adapter behavior through a scripted transport."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from collections import deque
from dataclasses import FrozenInstanceError, is_dataclass
from typing import Any

import pytest

from app.ai.fake_realtime import FakeRealtimeProvider
from app.ai.provider import ProviderStatus
from app.ai.realtime import (
    AudioDelta,
    ProviderFailure,
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


async def test_fake_session_records_audio_and_emits_events() -> None:
    """Removing audio capture or queue delivery must break this contract."""
    provider = FakeRealtimeProvider()
    session = await provider.open_session(
        RealtimeSessionConfig("gpt-realtime-2.1", "Sé breve", "default", "es-AR")
    )

    await session.send_audio(b"\x00\x00" * 480)
    await session.emit(TranscriptFinal(role=MessageRole.USER, text="Hola", item_id="item-1"))

    event = await anext(session.events())

    assert session.received_audio == [b"\x00\x00" * 480]
    assert event == TranscriptFinal(role=MessageRole.USER, text="Hola", item_id="item-1")


async def test_fake_session_exposes_one_event_iterator_and_stops_when_closed() -> None:
    """Returning a fresh iterator or omitting its close signal must break this contract."""
    session = await FakeRealtimeProvider().open_session(
        RealtimeSessionConfig("model", "instructions", "voice", "es-AR")
    )

    events = session.events()
    assert events is session.events()
    await session.emit(SessionReady())
    assert await anext(events) == SessionReady()

    await session.close()
    await session.close()

    with pytest.raises(StopAsyncIteration):
        await anext(events)


async def test_fake_session_rejects_all_operations_after_close_without_side_effects() -> None:
    """Accepting input, cancellation, or events after close must break this contract."""
    session = await FakeRealtimeProvider().open_session(
        RealtimeSessionConfig("model", "instructions", "voice", "es-AR")
    )
    events = session.events()

    await session.close()
    await session.close()

    assert session._event_queue.qsize() == 1
    with pytest.raises(RuntimeError, match=r"^session is closed$"):
        await session.send_audio(b"\x00\x00")
    with pytest.raises(RuntimeError, match=r"^session is closed$"):
        await session.cancel_response()
    with pytest.raises(RuntimeError, match=r"^session is closed$"):
        await session.emit(SessionReady())

    assert session.received_audio == []
    assert session.cancel_count == 0
    with pytest.raises(StopAsyncIteration):
        await anext(events)
    with pytest.raises(StopAsyncIteration):
        await anext(events)
    assert session._event_queue.empty()


async def test_fake_provider_models_readiness_cancellation_and_open_failure() -> None:
    """Ignoring readiness, cancellation, or failure state must break this contract."""
    provider = FakeRealtimeProvider()
    session = await provider.open_session(
        RealtimeSessionConfig("model", "instructions", "voice", "es-AR")
    )

    await session.emit(SessionReady())
    assert await anext(session.events()) == SessionReady()
    await session.cancel_response()

    assert provider.sessions == [session]
    assert session.cancel_count == 1
    assert provider.diagnose() == ProviderStatus.AVAILABLE

    provider.fail_open = True
    assert provider.diagnose() == ProviderStatus.UNAVAILABLE
    with pytest.raises(RuntimeError, match="fail opening"):
        await provider.open_session(RealtimeSessionConfig("model", "i", "voice", "es-AR"))


@pytest.mark.parametrize(
    "value",
    [
        RealtimeSessionConfig("model", "instructions", "voice", "es-AR"),
        SessionReady(),
        SpeechStarted(),
        SpeechStopped(),
        ResponseStarted(),
        AudioDelta(pcm16=b"\x00\x00"),
        TranscriptFinal(role=MessageRole.ASSISTANT, text="Bienvenido", item_id="item-2"),
        ResponseFinished(),
        ProviderFailure(code="AI_UNAVAILABLE"),
    ],
)
def test_contract_values_are_frozen_and_slotted(value: object) -> None:
    """Removing frozen slots from any config or event DTO must break this contract."""
    assert is_dataclass(value)
    assert type(value).__dataclass_params__.frozen is True  # type: ignore[attr-defined]
    assert hasattr(type(value), "__slots__")


def test_contract_config_is_immutable_and_transcript_uses_persistent_role() -> None:
    """Mutable config or string transcript roles must break this contract."""
    config = RealtimeSessionConfig("model", "instructions", "voice", "es-AR")
    transcript = TranscriptFinal(role=MessageRole.ASSISTANT, text="Bienvenido", item_id="item-2")

    with pytest.raises(FrozenInstanceError):
        config.model = "other"  # type: ignore[misc]

    assert transcript.role is MessageRole.ASSISTANT


class ScriptedRealtimeTransport:
    """Replace only the network boundary; capture real adapter wire messages."""

    def __init__(self, events: list[dict[str, Any] | str | Exception]) -> None:
        self.script = deque(events)
        self.sent: list[dict[str, Any]] = []
        self.close_count = 0
        self.reading = asyncio.Event()
        self.release = asyncio.Event()
        self.send_error: Exception | None = None
        self.close_error: Exception | None = None
        self.terminal = asyncio.Event()
        self.abort_count = 0
        self.transport = self

    async def send(self, message: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        self.reading.set()
        if not self.script:
            await self.release.wait()
        if not self.script:
            raise EOFError("socket closed")
        event = self.script.popleft()
        if isinstance(event, Exception):
            raise event
        return event if isinstance(event, str) else json.dumps(event)

    async def close(self) -> None:
        self.close_count += 1
        self.release.set()
        if self.close_error is not None:
            raise self.close_error
        self.terminal.set()

    def abort(self) -> None:
        self.abort_count += 1
        self.terminal.set()
        self.release.set()

    async def wait_closed(self) -> None:
        await self.terminal.wait()


@pytest.fixture
def realtime_settings() -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://test:test@localhost/portero_test",
        APP_SECRET_KEY="test-secret",
        DEVICE_CREDENTIAL_ENCRYPTION_KEY="test-encryption",
        OPENAI_API_KEY="synthetic-private-credential",
        OPENAI_REALTIME_MODEL="environment/model & revision",
    )


def wire_provider(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    transport: ScriptedRealtimeTransport,
    *,
    connect_error: Exception | None = None,
    open_timeout: float = 0.1,
) -> Any:
    from app.ai import openai_realtime

    async def connect(url: str, **kwargs: Any) -> ScriptedRealtimeTransport:
        assert url.startswith("wss://api.openai.com/v1/realtime?model=")
        assert kwargs["additional_headers"] == {
            "Authorization": "Bearer synthetic-private-credential"
        }
        # A transport debug logger can otherwise print the Authorization header.
        logger = kwargs["logger"]
        assert isinstance(logger, logging.Logger)
        assert logger.disabled and not logger.propagate
        if connect_error is not None:
            raise connect_error
        transport.url = url  # type: ignore[attr-defined]
        return transport

    monkeypatch.setattr(openai_realtime, "connect", connect)
    return openai_realtime.OpenAIRealtimeProvider(settings, open_timeout=open_timeout)


async def test_openai_configures_confirmed_pcm_session_and_sends_audio_once(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
) -> None:
    """Wrong wire schema/model, premature ready, duplicate appends or cancel must fail."""
    transport = ScriptedRealtimeTransport(
        [
            {"type": "session.created", "session": {"id": "sess-1"}},
            {"type": "session.updated", "session": {"id": "sess-1"}},
            {"type": "session.updated", "session": {"id": "sess-1"}},
            {"type": "response.created", "response": {"id": "resp-1"}},
        ]
    )
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    assert provider.diagnose() == ProviderStatus.CONFIGURED
    session = await provider.open_session(
        RealtimeSessionConfig(
            "env-default",
            "System rules\nAgent rules",
            "marin",
            "es-AR",
        )
    )
    assert transport.url.endswith("model=environment%2Fmodel%20%26%20revision")
    assert provider.diagnose() == ProviderStatus.AVAILABLE
    assert transport.sent == [
        {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": "environment/model & revision",
                "instructions": "System rules\nAgent rules",
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "turn_detection": {"type": "server_vad", "create_response": True},
                        "transcription": {"model": "gpt-4o-mini-transcribe", "language": "es"},
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": "marin",
                        "speed": 1.0,
                    },
                },
            },
        }
    ]
    assert session.events() is session.events()
    assert await anext(session.events()) == SessionReady()
    assert await anext(session.events()) == ResponseStarted()
    await session.send_audio(b"\x00\x01\x02\x03")
    await session.cancel_response()
    assert transport.sent[1] == {"type": "input_audio_buffer.append", "audio": "AAECAw=="}
    await session.cancel_response()
    cancellations = transport.sent[-2:]
    assert all(set(event) == {"type", "event_id"} for event in cancellations)
    assert all(event["type"] == "response.cancel" for event in cancellations)
    assert all(
        isinstance(event["event_id"], str) and 1 <= len(event["event_id"]) <= 512
        for event in cancellations
    )
    assert cancellations[0]["event_id"] != cancellations[1]["event_id"]
    assert "synthetic-private-credential" not in repr(provider) + repr(session)
    await session.close()


def cancel_error(client_event_id, **changes):
    error = {
        "type": "invalid_request_error",
        "code": "response_cancel_not_active",
        "message": "No active response found",
        "param": None,
        "event_id": client_event_id,
    }
    error.update(changes)
    return {"type": "error", "event_id": "server-error", "error": error}


async def test_openai_ignores_only_correlated_well_formed_no_active_cancel(
    monkeypatch, realtime_settings
):
    transport = ScriptedRealtimeTransport([{"type": "session.updated"}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert await anext(session.events()) == SessionReady()
    await session.cancel_response()
    client_id = transport.sent[-1].get("event_id")
    assert isinstance(client_id, str) and client_id
    # The response completion can arrive before the error for our racing cancel.
    transport.script.extend(
        [
            {"type": "response.done", "response": {"status": "completed"}},
            cancel_error(client_id),
            {"type": "input_audio_buffer.speech_started"},
        ]
    )
    assert await anext(session.events()) == ResponseFinished()
    assert await anext(session.events()) == SpeechStarted()
    assert provider.diagnose() == ProviderStatus.AVAILABLE
    assert transport.close_count == 0
    assert not session._pending_cancels
    await session.close()


@pytest.mark.parametrize(
    "change",
    [
        {"event_id": "unknown"},
        {"event_id": None},
        {"code": "server_error"},
        {"type": "server_error"},
        {"message": None},
        {"param": "response_id"},
    ],
)
async def test_openai_cancel_does_not_suppress_uncorrelated_or_other_errors(
    monkeypatch, realtime_settings, change
):
    transport = ScriptedRealtimeTransport([{"type": "session.updated"}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert await anext(session.events()) == SessionReady()
    await session.cancel_response()
    client_id = transport.sent[-1].get("event_id")
    assert isinstance(client_id, str) and client_id
    transport.script.append(cancel_error(client_id, **change))
    assert await anext(session.events()) == ProviderFailure("AI_UNAVAILABLE")
    assert provider.diagnose() == ProviderStatus.UNAVAILABLE
    assert transport.close_count == 1
    assert not session._pending_cancels


async def test_openai_cancel_tracking_is_bounded_consumed_once_and_cleared_on_close(
    monkeypatch, realtime_settings
):
    transport = ScriptedRealtimeTransport([{"type": "session.updated"}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert await anext(session.events()) == SessionReady()
    for _ in range(200):
        await session.cancel_response()
    assert len(session._pending_cancels) <= 64
    oldest = transport.sent[1]["event_id"]
    latest = transport.sent[-1]["event_id"]
    assert oldest not in session._pending_cancels
    transport.script.extend([cancel_error(latest), {"type": "input_audio_buffer.speech_started"}])
    assert await anext(session.events()) == SpeechStarted()
    assert latest not in session._pending_cancels
    transport.script.append(cancel_error(latest))
    assert await anext(session.events()) == ProviderFailure("AI_UNAVAILABLE")
    assert not session._pending_cancels


async def test_openai_expired_cancel_correlation_cannot_suppress_error(
    monkeypatch, realtime_settings
):
    from app.ai import openai_realtime

    clock = 100.0
    monkeypatch.setattr(openai_realtime, "monotonic", lambda: clock, raising=False)
    transport = ScriptedRealtimeTransport([{"type": "session.updated"}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert await anext(session.events()) == SessionReady()
    await session.cancel_response()
    client_id = transport.sent[-1].get("event_id")
    assert isinstance(client_id, str) and client_id
    clock += 31
    transport.script.append(cancel_error(client_id))
    assert await anext(session.events()) == ProviderFailure("AI_UNAVAILABLE")
    assert not session._pending_cancels


async def test_openai_normalizes_documented_events_and_ignores_informational_events(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
) -> None:
    """Wrong event families/roles or leaking raw dictionaries must fail."""
    transport = ScriptedRealtimeTransport(
        [
            {"type": "session.updated", "session": {}},
            {"type": "rate_limits.updated", "rate_limits": []},
            {"type": "unknown.informational", "data": "ignored"},
            {"type": "input_audio_buffer.speech_started", "item_id": "u", "audio_start_ms": 0},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "u", "audio_end_ms": 200},
            {"type": "response.created", "response": {"id": "r"}},
            {"type": "response.output_audio.delta", "delta": "AAECAw==", "item_id": "a"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "u",
                "transcript": "Hola",
                "content_index": 0,
            },
            {
                "type": "response.output_audio_transcript.done",
                "item_id": "a",
                "transcript": "Bienvenido",
                "response_id": "r",
                "content_index": 0,
            },
            {"type": "response.done", "response": {"id": "r", "status": "completed"}},
        ]
    )
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("persisted", "i", "cedar", "en"))
    assert transport.url.endswith("model=persisted")
    assert transport.sent[0]["session"]["audio"]["output"]["voice"] == "cedar"
    expected = [
        SessionReady(),
        SpeechStarted(),
        SpeechStopped(),
        ResponseStarted(),
        AudioDelta(b"\x00\x01\x02\x03"),
        TranscriptFinal(MessageRole.USER, "Hola", "u"),
        TranscriptFinal(MessageRole.ASSISTANT, "Bienvenido", "a"),
        ResponseFinished(),
    ]
    assert [await anext(session.events()) for _ in expected] == expected
    await session.close()


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "session.created", "session": {}}],
        [{"type": "error", "error": {"message": "synthetic-private-credential"}}],
        [OSError("synthetic-private-credential")],
    ],
)
async def test_openai_open_requires_updated_and_sanitizes_failures(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
    events: list[Any],
) -> None:
    """Accepting created alone, missing timeout or leaking transport errors must fail."""
    transport = ScriptedRealtimeTransport(events)
    provider = wire_provider(monkeypatch, realtime_settings, transport, open_timeout=0.01)
    with pytest.raises(RuntimeError, match="^AI_UNAVAILABLE$") as caught:
        await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert "synthetic-private-credential" not in "".join(traceback.format_exception(caught.value))
    assert provider.diagnose() == ProviderStatus.UNAVAILABLE
    assert transport.close_count == 1


async def test_openai_sanitizes_connect_exception(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A connect exception must never expose the key in public traceback/logging."""
    provider = wire_provider(
        monkeypatch,
        realtime_settings,
        ScriptedRealtimeTransport([]),
        connect_error=OSError("synthetic-private-credential"),
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as caught:
        await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert str(caught.value) == "AI_UNAVAILABLE"
    assert "synthetic-private-credential" not in (
        "".join(traceback.format_exception(caught.value)) + caplog.text + repr(provider)
    )
    assert provider.diagnose() == ProviderStatus.UNAVAILABLE


@pytest.mark.parametrize("key", [None, "", "  ", "sk-example-not-a-real-key"])
async def test_openai_unconfigured_credentials_never_connect(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
    key: str | None,
) -> None:
    """Missing/example credentials must not initiate a connection or become unavailable."""
    from pydantic import SecretStr

    settings = realtime_settings.model_copy(
        update={
            "openai_api_key": SecretStr(key) if key is not None else None,
        }
    )
    transport = ScriptedRealtimeTransport([])
    provider = wire_provider(monkeypatch, settings, transport)
    assert provider.diagnose() == ProviderStatus.UNCONFIGURED
    with pytest.raises(RuntimeError, match="^AI_UNAVAILABLE$"):
        await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert transport.sent == []
    assert not hasattr(transport, "url")
    assert provider.diagnose() == ProviderStatus.UNCONFIGURED


@pytest.mark.parametrize(
    "event",
    [
        {"type": "error", "error": {"code": "bad", "message": "synthetic-private-credential"}},
        OSError("synthetic-private-credential"),
        "not json synthetic-private-credential",
        {"type": "response.output_audio.delta", "delta": "!!!"},
        {"type": "response.output_audio_transcript.done", "transcript": [], "item_id": "a"},
        {
            "type": "response.done",
            "response": {
                "status": "failed",
                "status_details": {"error": {"message": "synthetic-private-credential"}},
            },
        },
    ],
)
async def test_openai_runtime_failures_are_internal_sanitized_and_terminal(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
    event: Any,
) -> None:
    """Provider errors, disconnects, malformed events and failed responses must be safe."""
    transport = ScriptedRealtimeTransport([{"type": "session.updated", "session": {}}, event])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert await anext(session.events()) == SessionReady()
    failure = await anext(session.events())
    assert failure == ProviderFailure(code="AI_UNAVAILABLE")
    assert "synthetic-private-credential" not in repr(failure)
    assert provider.diagnose() == ProviderStatus.UNAVAILABLE
    with pytest.raises(StopAsyncIteration):
        await anext(session.events())
    await session.close()
    assert transport.close_count == 1


async def test_openai_close_wakes_reader_and_is_terminal_even_if_transport_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
) -> None:
    """A blocked read must end on close; repeated close/input must have no side effects."""
    transport = ScriptedRealtimeTransport([{"type": "session.updated", "session": {}}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert await anext(session.events()) == SessionReady()
    transport.reading.clear()
    reader = asyncio.create_task(anext(session.events()))
    await transport.reading.wait()
    transport.close_error = OSError("synthetic-private-credential")
    await session.close()
    await session.close()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(reader, 0.1)
    with pytest.raises(RuntimeError, match="^session is closed$"):
        await session.send_audio(b"\x00\x00")
    with pytest.raises(RuntimeError, match="^session is closed$"):
        await session.cancel_response()
    assert transport.close_count == 1
    assert len(transport.sent) == 1


async def test_openai_send_failure_is_sanitized_and_marks_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
) -> None:
    """Failed sends must update diagnosis and close without leaking external exceptions."""
    transport = ScriptedRealtimeTransport([{"type": "session.updated", "session": {}}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    transport.send_error = OSError("synthetic-private-credential")
    with pytest.raises(RuntimeError, match="^AI_UNAVAILABLE$") as caught:
        await session.send_audio(b"\x00\x00")
    assert "synthetic-private-credential" not in "".join(traceback.format_exception(caught.value))
    assert provider.diagnose() == ProviderStatus.UNAVAILABLE
    assert transport.close_count == 1


def test_provider_failure_exposes_only_a_closed_internal_code() -> None:
    """Freeform failure messages must not remain in the shared public contract."""
    from typing import Literal, get_type_hints

    assert get_type_hints(ProviderFailure) == {"code": Literal["AI_UNAVAILABLE"]}


async def test_openai_resolves_persisted_default_voice_at_provider_boundary(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
) -> None:
    """The neutral persisted default must resolve to a supported OpenAI voice."""
    transport = ScriptedRealtimeTransport([{"type": "session.updated", "session": {}}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "default", "es"))
    assert transport.sent[0]["session"]["audio"]["output"]["voice"] == "marin"
    await session.close()


@pytest.mark.parametrize("voice", ["", "   "])
async def test_openai_rejects_empty_voice_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
    voice: str,
) -> None:
    """An empty voice must fail locally, without sending an invalid configuration."""
    transport = ScriptedRealtimeTransport([{"type": "session.updated", "session": {}}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    with pytest.raises(RuntimeError, match="^AI_UNAVAILABLE$"):
        await provider.open_session(RealtimeSessionConfig("model", "i", voice, "es"))
    assert not hasattr(transport, "url")
    assert provider.diagnose() == ProviderStatus.UNAVAILABLE


class BlockingCloseTransport(ScriptedRealtimeTransport):
    """Model a peer that never acknowledges close until the socket is aborted."""

    def __init__(self, events: list[dict[str, Any] | str | Exception]) -> None:
        super().__init__(events)
        self.close_started = asyncio.Event()
        self.close_tasks: list[asyncio.Task[Any]] = []

    async def close(self) -> None:
        self.close_count += 1
        task = asyncio.current_task()
        assert task is not None
        self.close_tasks.append(task)
        self.close_started.set()
        await self.terminal.wait()


@pytest.mark.parametrize("mode", ["timeout", "caller_cancelled", "concurrent"])
async def test_openai_blocked_close_finishes_socket_and_all_cleanup_tasks(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
    mode: str,
) -> None:
    """A timeout/cancel/concurrent close must share completed cleanup and abort the socket."""
    from app.ai import openai_realtime

    monkeypatch.setattr(openai_realtime, "_CLOSE_TIMEOUT", 0.02)
    transport = BlockingCloseTransport([{"type": "session.updated", "session": {}}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    initial_tasks = asyncio.all_tasks()
    first = asyncio.create_task(session.close())
    await transport.close_started.wait()
    second = asyncio.create_task(session.close()) if mode == "concurrent" else None
    if second is not None:
        await asyncio.sleep(0)
        assert not second.done(), "Concurrent close returned before shared cleanup completed"
    if mode == "caller_cancelled":
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    else:
        await asyncio.wait_for(first, 0.5)
    if second is not None:
        await asyncio.wait_for(second, 0.5)
    assert transport.terminal.is_set()
    assert transport.abort_count == 1
    assert transport.close_count == 1
    assert all(task.done() for task in transport.close_tasks)
    assert asyncio.all_tasks() <= initial_tasks
    await session.close()
    with pytest.raises(RuntimeError, match="^session is closed$"):
        await session.send_audio(b"\x00\x00")
    with pytest.raises(RuntimeError, match="^session is closed$"):
        await session.cancel_response()
    assert transport.close_count == 1


@pytest.mark.parametrize("stop", ["cancel", "aclose"])
async def test_openai_ending_event_consumer_closes_session_terminally(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
    stop: str,
) -> None:
    """Canceling a blocked anext or closing the generator must terminate the usable session."""
    from app.ai import openai_realtime

    monkeypatch.setattr(openai_realtime, "_CLOSE_TIMEOUT", 0.02)
    transport = BlockingCloseTransport([{"type": "session.updated", "session": {}}])
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    session = await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert await anext(session.events()) == SessionReady()
    initial_tasks = asyncio.all_tasks()
    if stop == "cancel":
        transport.reading.clear()
        reader = asyncio.create_task(anext(session.events()))
        await transport.reading.wait()
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
    else:
        await session.events().aclose()
    assert transport.terminal.is_set()
    assert transport.close_count == 1
    assert all(task.done() for task in transport.close_tasks)
    assert asyncio.all_tasks() <= initial_tasks
    with pytest.raises(RuntimeError, match="^session is closed$"):
        await session.send_audio(b"\x00\x00")
    with pytest.raises(RuntimeError, match="^session is closed$"):
        await session.cancel_response()
    with pytest.raises(StopAsyncIteration):
        await anext(session.events())
    await session.close()


async def test_openai_open_timeout_also_finishes_blocked_transport_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
) -> None:
    """The pre-session failure path must abort and join cleanup too."""
    from app.ai import openai_realtime

    monkeypatch.setattr(openai_realtime, "_CLOSE_TIMEOUT", 0.02)
    transport = BlockingCloseTransport([{"type": "session.created", "session": {}}])
    provider = wire_provider(monkeypatch, realtime_settings, transport, open_timeout=0.01)
    initial_tasks = asyncio.all_tasks()
    with pytest.raises(RuntimeError, match="^AI_UNAVAILABLE$"):
        await provider.open_session(RealtimeSessionConfig("model", "i", "marin", "es"))
    assert transport.terminal.is_set()
    assert transport.abort_count == 1
    assert all(task.done() or task is asyncio.current_task() for task in transport.close_tasks)
    assert asyncio.all_tasks() <= initial_tasks


@pytest.mark.parametrize("operation", ["open", "send"])
async def test_openai_cancel_during_failure_cleanup_does_not_chain_external_errors(
    monkeypatch: pytest.MonkeyPatch,
    realtime_settings: Settings,
    operation: str,
) -> None:
    """Cancellation during cleanup must not chain the raw provider error into its traceback."""
    from app.ai import openai_realtime

    monkeypatch.setattr(openai_realtime, "_CLOSE_TIMEOUT", 0.02)
    script = (
        [OSError("synthetic-private-credential")]
        if operation == "open"
        else [{"type": "session.updated", "session": {}}]
    )
    transport = BlockingCloseTransport(script)
    provider = wire_provider(monkeypatch, realtime_settings, transport)
    config = RealtimeSessionConfig("model", "i", "marin", "es")
    initial_tasks = asyncio.all_tasks()
    if operation == "open":
        caller = asyncio.create_task(provider.open_session(config))
    else:
        session = await provider.open_session(config)
        transport.send_error = OSError("synthetic-private-credential")
        caller = asyncio.create_task(session.send_audio(b"\x00\x00"))
    await transport.close_started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await caller
    assert "synthetic-private-credential" not in "".join(traceback.format_exception(caught.value))
    assert transport.terminal.is_set()
    assert asyncio.all_tasks() <= initial_tasks
