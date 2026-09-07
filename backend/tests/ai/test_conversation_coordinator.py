"""Ownership, ordering, and transactional lifecycle of SIM-1 conversations."""

import asyncio
import base64
import gc
import json
import weakref
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.fake_realtime import FakeRealtimeProvider
from app.ai.realtime import (
    AudioDelta,
    ProviderFailure,
    ResponseFinished,
    ResponseStarted,
    SpeechStarted,
    SpeechStopped,
    TranscriptFinal,
)
from app.devices.audio_protocol import (
    AudioDirection,
    AudioFrame,
    AudioFrameError,
    decode_audio_frame,
)
from app.devices.connections import DeviceConnectionRegistry, DeviceConversationTransport
from app.models import (
    AgentConfig,
    Conversation,
    ConversationMessage,
    ConversationOutcome,
    ConversationStatus,
    Device,
    MessageRole,
)
from app.schemas.conversations import ConversationError


class Transport:
    def __init__(self, factory):
        self.factory = factory
        self.controls = []
        self.fail_started = False
        self.audio = []
        self.pending_playback = []
        self.audio_entered = asyncio.Event()
        self.audio_release = asyncio.Event()
        self.audio_release.set()

    async def send_control(self, message):
        if message.type == "conversation.started":
            async with self.factory() as db:
                row = await db.get(Conversation, message.conversation_id)
                assert row is not None and row.status == ConversationStatus.OPEN
            if self.fail_started:
                raise RuntimeError("private transport failure")
        self.controls.append(message)
        if message.type == "conversation.audio.clear":
            self.pending_playback.clear()

    async def send_audio(self, data):
        self.audio_entered.set()
        await self.audio_release.wait()
        self.audio.append(data)
        self.pending_playback.append(data)

    async def send_json(self, data):
        pass

    async def close(self, code=1000, reason=None):
        pass


class ObservedProvider(FakeRealtimeProvider):
    def __init__(self, factory):
        super().__init__()
        self.factory = factory
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.close_count = 0

    async def open_session(self, config):
        async with self.factory() as db:
            assert (
                await db.scalars(
                    select(Conversation).where(Conversation.status == ConversationStatus.OPEN)
                )
            ).all() == []
        self.entered.set()
        await self.release.wait()
        session = await super().open_session(config)
        original_close = session.close

        async def close():
            self.close_count += 1
            await original_close()

        session.close = close
        return session


class BlockedTransport(Transport):
    def __init__(self, factory, message_type, *, cancel_send=False):
        super().__init__(factory)
        self.message_type = message_type
        self.cancel_send = cancel_send
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exited = asyncio.Event()

    async def send_control(self, message):
        if message.type == self.message_type:
            self.entered.set()
            try:
                if self.cancel_send:
                    self.cancel_send = False
                    raise asyncio.CancelledError
                await self.release.wait()
            finally:
                self.exited.set()
        await super().send_control(message)


class SlowCancellationTransport(Transport):
    """Cancellation forbids delivery, but transport cleanup can take arbitrarily long."""

    def __init__(self, factory, message_type, *, cleanup_fails=False):
        super().__init__(factory)
        self.message_type = message_type
        self.cleanup_fails = cleanup_fails
        self.entered = asyncio.Event()
        self.cancel_received = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.send_task = None

    async def send_control(self, message):
        if message.type == self.message_type:
            self.send_task = asyncio.current_task()
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancel_received.set()
                await self.release_cleanup.wait()
                if self.cleanup_fails:
                    raise RuntimeError("private delayed transport cleanup failure") from None
                raise
        await super().send_control(message)


@pytest.fixture
async def setup(db, home, async_engine, settings):
    # Import inside the fixture so RED reports the absent coordinator explicitly.
    from app.ai.conversation_coordinator import ConversationCoordinator

    device = Device(home_id=home.id, device_id="PI-COORDINATOR", name="Entrada")
    config = AgentConfig(
        home_id=home.id,
        enabled=True,
        system_prompt="Recibe paquetes en la entrada.",
        language="es-AR",
        voice="marin",
    )
    db.add_all([device, config])
    await db.commit()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    provider = ObservedProvider(factory)
    transport = Transport(factory)
    registry = DeviceConnectionRegistry()
    lease = await registry.claim(device.id, transport)
    settings.openai_api_key = SecretStr("sk-test-coordinator")
    coordinator = ConversationCoordinator(factory, provider, settings, registry)
    yield coordinator, device, config, provider, transport, registry, lease
    provider.release.set()
    await coordinator.shutdown()


async def rows(db):
    return (await db.scalars(select(Conversation).execution_options(populate_existing=True))).all()


async def test_service_revocation_closes_standalone_coordinator_without_socket_route(
    setup, db, settings
):
    """A standalone coordinator also needs committed revocation notifications."""
    from app.devices.credentials import rotate_device_secret

    coordinator, device, _, provider, transport, registry, lease = setup
    await coordinator.start(device.id, lease, transport)
    await rotate_device_secret(db, device, settings, registry=registry)
    await db.commit()
    await registry.wait_revocations()
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.ABANDONED
    assert provider.close_count == 1
    assert not await registry.is_current(lease)


async def test_retired_lease_input_pump_cannot_forward_previously_queued_frames(setup, db):
    """Receive-time validation alone cannot authorize PCM dequeued after lease retirement."""
    coordinator, device, _, provider, transport, registry, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    # Stage input and retire in the same event-loop turn, before the worker runs.
    active.input_queue.put_nowait(b"\x01\x02" * 480)
    registry.revoke_access(device.id)
    await registry.wait_revocations()
    assert provider.sessions[0].received_audio == []


async def test_start_opens_before_commit_then_sends_distinct_ids(setup, db, settings):
    coordinator, device, config, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    (row,) = await rows(db)
    started, listening = transport.controls
    assert listening.state == "listening"
    assert started.type == "conversation.started"
    assert started.conversation_id == row.id
    assert started.stream_id != row.id
    assert started.max_seconds == settings.conversation_max_seconds
    assert row.home_id == device.home_id
    assert row.device_id == device.id
    requested = provider.sessions[0].config
    assert requested.model == settings.openai_realtime_model
    assert requested.voice == "marin"
    assert requested.language == "es-AR"
    assert config.system_prompt in requested.instructions
    assert "No abras" in requested.instructions
    assert "prioridad" in requested.instructions


async def test_ready_is_published_after_durable_start_without_waiting_for_speech(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    provider.release.clear()
    starting = asyncio.create_task(coordinator.start(device.id, lease, transport))
    try:
        await provider.entered.wait()
        assert transport.controls == []
        assert await rows(db) == []
    finally:
        provider.release.set()
        await starting
    assert [message.type for message in transport.controls] == [
        "conversation.started",
        "conversation.state",
    ]
    started, listening = transport.controls
    assert listening.state == "listening"
    assert listening.conversation_id == started.conversation_id
    assert listening.seq > started.seq


async def test_initial_listening_failure_closes_durable_start_before_workers(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    failing = BlockedTransport(transport.factory, "conversation.state", cancel_send=True)
    await coordinator.start(device.id, lease, failing)
    await eventually(lambda: not coordinator._active and not coordinator._tasks)
    assert [message.type for message in failing.controls] == [
        "conversation.started",
        "conversation.ended",
        "conversation.error",
    ]
    assert failing.controls[-1].code == "AI_UNAVAILABLE"
    assert provider.close_count == 1
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.FAILED
    assert not coordinator._io_tasks


class GatedAudioSocket:
    """Network boundary only; registry, transport and coordinator stay real."""

    def __init__(self, *, fail_audio=False):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.writes = []
        self.closes = []
        self.fail_audio = fail_audio
        self.writing = False

    async def send_json(self, data):
        assert not self.writing, "JSON must not overlap an in-flight binary frame"
        self.writes.append(data)

    async def send_bytes(self, data):
        assert not self.writing
        self.writing = True
        self.entered.set()
        try:
            await self.release.wait()
            if self.fail_audio:
                raise ConnectionError("private socket failure")
            self.writes.append(data)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.writing = False

    async def close(self, code=1000, reason=None):
        self.closes.append((code, reason))


@pytest.mark.parametrize(
    "trigger",
    [
        "barge_in",
        "stop",
        "overflow",
        "local_overflow",
        "stop_during_barge_in",
        "local_overflow_during_barge_in",
    ],
)
async def test_inflight_real_socket_audio_finishes_before_clear_without_retiring_lease(
    setup, db, settings, trigger
):
    settings.audio_output_queue_frames = 1
    coordinator, device, _, provider, _, registry, _ = setup
    socket = GatedAudioSocket()
    lease = await registry.claim(device.id, socket, capabilities=["audio_pcm16_v1"])
    transport = DeviceConversationTransport(registry, lease)
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    session = provider.sessions[0]
    await session.emit(ResponseStarted())
    await session.emit(AudioDelta(b"a" * 960))
    await asyncio.wait_for(socket.entered.wait(), 1)
    await session.emit(AudioDelta(b"b" * 960))
    await eventually(lambda: active.output_queue.qsize() == 1)
    stopping = None
    try:
        if trigger in {"barge_in", "stop_during_barge_in", "local_overflow_during_barge_in"}:
            await session.emit(SpeechStarted())
            await eventually(lambda: session.cancel_count == 1)
            if trigger in {"stop_during_barge_in", "local_overflow_during_barge_in"}:
                # The finalizer cancels the provider worker while its gather is
                # already waiting for the interrupted output worker: two cancels.
                await eventually(lambda: active.output_task.cancelling())
                stopping = asyncio.create_task(
                    coordinator.stop(device.id, lease, "visitor_finished")
                    if trigger == "stop_during_barge_in"
                    else coordinator.report_output_overflow(
                        device.id, lease, active.conversation_id, active.stream_id
                    )
                )
                await eventually(lambda: active.closing)
        elif trigger == "overflow":
            await session.emit(AudioDelta(b"c" * 960))
            await eventually(lambda: active.closing)
        elif trigger == "local_overflow":
            stopping = asyncio.create_task(
                coordinator.report_output_overflow(
                    device.id, lease, active.conversation_id, active.stream_id
                )
            )
            await eventually(lambda: active.closing)
        else:
            stopping = asyncio.create_task(coordinator.stop(device.id, lease, "visitor_finished"))
            await eventually(lambda: active.closing)
        # Let cancellation traverse coordinator -> registry if it still does so.
        for _ in range(10):
            await asyncio.sleep(0)
        assert await registry.is_current(lease)
        assert not socket.cancelled.is_set()
        socket.release.set()
        if trigger == "barge_in":
            await eventually(lambda: active.state == "visitor_speaking")
            terminal_type = "conversation.audio.clear"
        else:
            await eventually(lambda: not coordinator._active and not coordinator._tasks)
            terminal_type = (
                "conversation.audio.clear" if "overflow" in trigger else "conversation.ended"
            )
        terminal_index = next(
            i
            for i, write in enumerate(socket.writes)
            if isinstance(write, dict) and write["type"] == terminal_type
        )
        assert [write for write in socket.writes[:terminal_index] if isinstance(write, bytes)]
        assert not any(isinstance(write, bytes) for write in socket.writes[terminal_index:])
        assert len([write for write in socket.writes if isinstance(write, bytes)]) == 1
        assert socket.closes == []
        assert await registry.is_current(lease)
        if trigger == "barge_in":
            await session.emit(AudioDelta(b"late" * 240))
            await session.emit(ResponseFinished())
            await session.emit(ResponseStarted())
            await session.emit(AudioDelta(b"d" * 960))
            await eventually(lambda: len([w for w in socket.writes if isinstance(w, bytes)]) == 2)
            newest = decode_audio_frame(
                socket.writes[-1], expected_direction=AudioDirection.SERVER_TO_DEVICE
            )
            assert newest.payload == b"d" * 960
            await coordinator.receive_audio(device.id, lease, input_frame(active))
            await eventually(lambda: bool(session.received_audio))
            await coordinator.stop(device.id, lease, "visitor_finished")
        else:
            (row,) = await rows(db)
            assert row.status == ConversationStatus.CLOSED
            assert row.outcome == (
                ConversationOutcome.FAILED
                if "overflow" in trigger
                else ConversationOutcome.REGISTERED
            )
        await eventually(lambda: not coordinator._io_tasks and not coordinator._control_tasks)
    finally:
        socket.release.set()
        if stopping is not None:
            await stopping


@pytest.mark.parametrize("failure", ["exception", "registry_timeout", "coordinator_timeout"])
@pytest.mark.parametrize("interrupted", [False, True])
async def test_real_socket_audio_failure_still_retires_lease_and_cleans_conversation(
    setup, db, monkeypatch, failure, interrupted
):
    coordinator, device, _, provider, _, registry, _ = setup
    if failure == "coordinator_timeout":
        monkeypatch.setattr("app.ai.conversation_coordinator._AUDIO_SEND_TIMEOUT_SECONDS", 0.03)
    else:
        monkeypatch.setattr(registry, "_send_timeout_seconds", 0.03)
    socket = GatedAudioSocket(fail_audio=failure == "exception")
    lease = await registry.claim(device.id, socket)
    transport = DeviceConversationTransport(registry, lease)
    await coordinator.start(device.id, lease, transport)
    await provider.sessions[0].emit(ResponseStarted())
    await provider.sessions[0].emit(AudioDelta(b"a" * 960))
    await asyncio.wait_for(socket.entered.wait(), 1)
    if interrupted:
        await provider.sessions[0].emit(SpeechStarted())
        await eventually(lambda: provider.sessions[0].cancel_count == 1)
    if failure == "exception":
        socket.release.set()
    await eventually(lambda: not coordinator._active and not coordinator._tasks)
    assert not await registry.is_current(lease)
    assert socket.closes == [(4500, "INTERNAL_ERROR")]
    assert not any(isinstance(write, bytes) for write in socket.writes)
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == (
        ConversationOutcome.ABANDONED if interrupted else ConversationOutcome.FAILED
    )
    assert provider.close_count == 1
    assert not coordinator._io_tasks


@pytest.mark.parametrize("key", [None, "", "  ", "sk-example-not-a-real-key"])
async def test_unconfigured_key_leaves_no_row_or_reservation(setup, db, settings, key):
    coordinator, device, _, provider, transport, _, lease = setup
    settings.openai_api_key = SecretStr(key) if key is not None else None
    await coordinator.start(device.id, lease, transport)
    (error,) = transport.controls
    assert isinstance(error, ConversationError)
    assert error.code == "AI_UNCONFIGURED"
    assert error.correlation_id
    assert await rows(db) == []
    assert provider.sessions == []
    settings.openai_api_key = SecretStr("sk-test-coordinator")
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-2].type == "conversation.started"
    assert transport.controls[-1].state == "listening"


@pytest.mark.parametrize("invalid", ["disabled_agent", "missing_agent", "disabled_device"])
async def test_invalid_configuration_prevents_open(setup, db, invalid):
    from app.models import DeviceStatus

    coordinator, device, config, provider, transport, _, lease = setup
    if invalid == "missing_agent":
        await db.delete(config)
    elif invalid == "disabled_agent":
        config.enabled = False
    else:
        device.status = DeviceStatus.DISABLED
    await db.commit()
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-1].code == "AI_UNCONFIGURED"
    assert provider.sessions == []
    assert await rows(db) == []


async def test_failed_open_is_safe_and_can_retry(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    provider.fail_open = True
    await coordinator.start(device.id, lease, transport)
    (error,) = transport.controls
    assert error.code == "AI_UNAVAILABLE"
    assert set(error.model_dump()) == {"type", "version", "seq", "code", "correlation_id"}
    assert await rows(db) == []
    provider.fail_open = False
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-2].type == "conversation.started"
    assert transport.controls[-1].state == "listening"


async def test_second_start_while_opening_is_rejected(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    provider.release.clear()
    starting = asyncio.create_task(coordinator.start(device.id, lease, transport))
    await provider.entered.wait()
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-1].code == "CONVERSATION_ALREADY_ACTIVE"
    assert await rows(db) == []
    provider.release.set()
    await starting
    assert len(await rows(db)) == 1
    assert len(provider.sessions) == 1


async def test_wrong_lease_cannot_stop_or_disconnect_owner(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    for wrong in [
        replace(lease, generation=lease.generation + 1),
        replace(lease, socket=Transport(transport.factory)),
        replace(lease, device_id=uuid4()),
    ]:
        await coordinator.stop(device.id, wrong, "visitor_finished")
        await coordinator.disconnect(device.id, wrong)
    (row,) = await rows(db)
    assert row.status == ConversationStatus.OPEN
    assert provider.close_count == 0


@pytest.mark.parametrize(
    "operation,outcome",
    [
        ("stop", ConversationOutcome.REGISTERED),
        ("disconnect", ConversationOutcome.ABANDONED),
        ("shutdown", ConversationOutcome.ABANDONED),
    ],
)
async def test_close_is_terminal_exactly_once(setup, db, operation, outcome):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)

    async def close():
        if operation == "stop":
            await coordinator.stop(device.id, lease, "visitor_finished")
        elif operation == "disconnect":
            await coordinator.disconnect(device.id, lease)
        else:
            await coordinator.shutdown()

    await asyncio.gather(close(), close())
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == outcome
    closed_at = row.closed_at
    await close()
    (row,) = await rows(db)
    assert row.closed_at == closed_at
    assert provider.close_count == 1
    ended = [message for message in transport.controls if message.type == "conversation.ended"]
    assert len(ended) == 1
    assert ended[0].outcome == outcome.value


async def test_stale_start_and_disconnect_do_not_touch_replacement(setup, db):
    coordinator, device, _, provider, transport, registry, lease = setup
    replacement = Transport(transport.factory)
    new_lease = await registry.claim(device.id, replacement)
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-1].code == "STREAM_NOT_OWNED"
    assert provider.sessions == []
    await coordinator.start(device.id, new_lease, replacement)
    await coordinator.disconnect(device.id, lease)
    (row,) = await rows(db)
    assert row.status == ConversationStatus.OPEN


async def test_disconnect_during_open_prevents_started_and_releases(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    provider.release.clear()
    starting = asyncio.create_task(coordinator.start(device.id, lease, transport))
    await provider.entered.wait()
    closing = asyncio.create_task(coordinator.disconnect(device.id, lease))
    await asyncio.sleep(0)
    provider.release.set()
    await asyncio.gather(starting, closing)
    assert not any(message.type == "conversation.started" for message in transport.controls)
    assert await rows(db) == []
    assert provider.close_count == 1
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-2].type == "conversation.started"
    assert transport.controls[-1].state == "listening"


async def test_started_send_failure_closes_failed_before_retry(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    transport.fail_started = True
    await coordinator.start(device.id, lease, transport)
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.FAILED
    assert provider.close_count == 1
    assert transport.controls[-1].code == "AI_UNAVAILABLE"
    transport.fail_started = False
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-2].type == "conversation.started"
    assert transport.controls[-1].state == "listening"
    assert len(await rows(db)) == 2


async def test_persistence_failure_closes_provider_and_releases(setup, db, monkeypatch):
    coordinator, device, _, provider, transport, _, lease = setup
    original = AsyncSession.commit
    fail = True

    async def commit(session):
        nonlocal fail
        if fail:
            fail = False
            raise RuntimeError("private database failure")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    await coordinator.start(device.id, lease, transport)
    assert await rows(db) == []
    assert provider.close_count == 1
    assert transport.controls[-1].code == "AI_UNAVAILABLE"
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-2].type == "conversation.started"
    assert transport.controls[-1].state == "listening"


async def test_failed_close_is_sanitized_and_retains_reservation_until_retry(
    setup, db, monkeypatch
):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    original = AsyncSession.commit
    fail = True

    async def commit(session):
        nonlocal fail
        if fail:
            fail = False
            raise RuntimeError("private close database failure")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    await coordinator.stop(device.id, lease, "visitor_finished")
    assert transport.controls[-1].code == "AI_UNAVAILABLE"
    (row,) = await rows(db)
    assert row.status == ConversationStatus.OPEN
    assert device.id in coordinator._active
    await coordinator.stop(device.id, lease, "visitor_finished")
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.REGISTERED
    assert provider.close_count == 1


async def test_uncertain_start_commit_is_reconciled_as_failed(setup, db, monkeypatch):
    coordinator, device, _, provider, transport, _, lease = setup
    original = AsyncSession.commit
    fail = True

    async def commit(session):
        nonlocal fail
        await original(session)
        if fail:
            fail = False
            raise RuntimeError("private commit acknowledgement failure")

    monkeypatch.setattr(AsyncSession, "commit", commit)
    await coordinator.start(device.id, lease, transport)
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.FAILED
    assert provider.close_count == 1
    assert transport.controls[-1].code == "AI_UNAVAILABLE"


async def test_stale_lease_during_open_cannot_publish_a_conversation(setup, db):
    coordinator, device, _, provider, transport, registry, lease = setup
    provider.release.clear()
    starting = asyncio.create_task(coordinator.start(device.id, lease, transport))
    await provider.entered.wait()
    await registry.claim(device.id, Transport(transport.factory))
    provider.release.set()
    await starting
    assert await rows(db) == []
    assert provider.close_count == 1
    assert not any(message.type == "conversation.started" for message in transport.controls)


async def test_cancelled_start_during_commit_finishes_cleanup(setup, db, monkeypatch):
    coordinator, device, _, provider, transport, _, lease = setup
    original = AsyncSession.commit
    entered = asyncio.Event()
    release = asyncio.Event()

    async def commit(session):
        entered.set()
        await release.wait()
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    starting = asyncio.create_task(coordinator.start(device.id, lease, transport))
    await entered.wait()
    starting.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await starting
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.ABANDONED
    assert provider.close_count == 1
    assert not any(message.type == "conversation.started" for message in transport.controls)


@pytest.mark.parametrize("socket_callback", [False, True])
async def test_cancelled_standalone_start_preserves_terminal_notification_for_same_lease_restart(
    setup, db, monkeypatch, socket_callback
):
    coordinator, device, _, provider, transport, registry, lease = setup
    terminal_notifications = []

    async def socket_disconnected(ownership):
        terminal_notifications.append(ownership.generation)
        await coordinator.disconnect(ownership.device_id, ownership)

    if socket_callback:
        lease = await registry.claim(device.id, transport, on_disconnect=socket_disconnected)
    original_open = provider.open_session
    session_closes = []

    async def open_session(config):
        session = await original_open(config)
        index = len(session_closes)
        session_closes.append(0)
        original_close = session.close

        async def close():
            session_closes[index] += 1
            await original_close()

        session.close = close
        return session

    monkeypatch.setattr(provider, "open_session", open_session)
    provider.release.clear()
    starting = asyncio.create_task(coordinator.start(device.id, lease, transport))
    await asyncio.wait_for(provider.entered.wait(), 1)
    attempt = coordinator._active[device.id]
    starting.cancel()
    await eventually(lambda: attempt.stop_reason is not None)
    provider.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(starting, 1)
    assert await registry.is_current(lease)
    assert session_closes == [1]
    assert await rows(db) == []
    notifications_after_cancel = list(terminal_notifications)

    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-2].type == "conversation.started"
    assert transport.controls[-1].state == "listening"
    await provider.sessions[1].emit(ResponseStarted())
    await eventually(lambda: transport.controls[-1].state == "assistant_speaking")
    successor_socket = Transport(transport.factory)
    successor = await registry.claim(device.id, successor_socket)
    # Both provider workers may discover retirement before close_replaced notifies it.
    await provider.sessions[1].emit(AudioDelta(b"\x03\x04" * 480))
    await provider.sessions[1].emit(SpeechStopped())
    await registry.close_replaced(successor)
    await eventually(lambda: session_closes == [1, 1])
    await eventually(lambda: not coordinator._active and not coordinator._tasks)
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.ABANDONED
    assert provider.close_count == 2
    assert notifications_after_cancel == []
    if socket_callback:
        assert terminal_notifications == [lease.generation]
    assert await registry.is_current(successor)
    assert transport.audio == []
    await registry.close_replaced(successor)
    await registry.unregister(lease)
    assert session_closes == [1, 1]


@pytest.mark.parametrize("socket_callback", [False, True])
@pytest.mark.parametrize("stage", ["open", "committed"])
async def test_cancelled_start_and_concurrent_retirement_invoke_public_disconnect_once(
    setup, db, monkeypatch, socket_callback, stage
):
    coordinator, device, _, provider, transport, registry, lease = setup
    public_calls = []
    callback_entered = asyncio.Event()
    original_disconnect = coordinator.disconnect

    async def disconnect(device_id, ownership):
        public_calls.append(ownership.generation)
        await original_disconnect(device_id, ownership)

    async def notify(ownership):
        callback_entered.set()
        await coordinator.disconnect(ownership.device_id, ownership)

    monkeypatch.setattr(coordinator, "disconnect", disconnect)
    if socket_callback:
        lease = await registry.claim(device.id, transport, on_disconnect=notify)
    if stage == "open":
        entered, release = provider.entered, provider.release
        release.clear()
    else:
        entered, release = asyncio.Event(), asyncio.Event()
        original_commit = AsyncSession.commit

        async def committed(session):
            await original_commit(session)
            entered.set()
            await release.wait()

        monkeypatch.setattr(AsyncSession, "commit", committed)
    starting = asyncio.create_task(coordinator.start(device.id, lease, transport))
    await asyncio.wait_for(entered.wait(), 1)
    attempt = coordinator._active[device.id]
    starting.cancel()
    # The attempt's cancel branch is now waiting for initialization to finish.
    await eventually(lambda: attempt.stop_reason is not None)
    successor_socket = Transport(transport.factory)
    successor = await registry.claim(device.id, successor_socket)
    notifying = asyncio.create_task(registry.notify_disconnect(lease, fallback=notify))
    replacing = asyncio.create_task(registry.close_replaced(successor))
    try:
        await asyncio.wait_for(callback_entered.wait(), 1)
        calls_while_cleanup_blocked = list(public_calls)
    finally:
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(starting, notifying, replacing, return_exceptions=True), 2
        )
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1:] == [None, None]
    await eventually(lambda: not coordinator._active and not coordinator._tasks)
    assert calls_while_cleanup_blocked == [lease.generation]
    assert public_calls == [lease.generation]
    assert provider.close_count == 1
    assert await registry.is_current(successor)
    persisted = await rows(db)
    if stage == "committed":
        assert len(persisted) == 1
        assert persisted[0].status == ConversationStatus.CLOSED
        assert persisted[0].outcome == ConversationOutcome.ABANDONED
    else:
        assert persisted == []
    assert not any(message.type == "conversation.started" for message in transport.controls)


async def test_delayed_start_attempt_cleanup_cannot_stop_new_conversation_on_same_lease(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    old_attempt = coordinator._active[device.id]
    await coordinator.stop(device.id, lease, "visitor_finished")
    await coordinator.start(device.id, lease, transport)
    current = coordinator._active[device.id]
    await coordinator._cancel_start(old_attempt)
    await coordinator._cancel_start(old_attempt)
    assert coordinator._active[device.id] is current
    assert not current.closing
    assert provider.close_count == 1
    persisted = await rows(db)
    assert len(persisted) == 2
    assert sum(row.status == ConversationStatus.OPEN for row in persisted) == 1


async def test_database_sessions_are_closed_during_network_io(setup, monkeypatch):
    coordinator, device, _, provider, transport, _, lease = setup
    entered = 0
    original_enter = AsyncSession.__aenter__
    original_exit = AsyncSession.__aexit__
    original_open = provider.open_session
    original_send = transport.send_control

    async def enter(session):
        nonlocal entered
        result = await original_enter(session)
        entered += 1
        return result

    async def exit_session(session, *args):
        nonlocal entered
        await original_exit(session, *args)
        entered -= 1

    async def open_session(config):
        assert entered == 0
        return await original_open(config)

    async def send_control(message):
        assert entered == 0
        await original_send(message)

    monkeypatch.setattr(AsyncSession, "__aenter__", enter)
    monkeypatch.setattr(AsyncSession, "__aexit__", exit_session)
    monkeypatch.setattr(provider, "open_session", open_session)
    monkeypatch.setattr(transport, "send_control", send_control)
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-2].type == "conversation.started"
    assert transport.controls[-1].state == "listening"
    await coordinator.stop(device.id, lease, "visitor_finished")
    assert transport.controls[-1].type == "conversation.ended"
    assert entered == 0


async def test_explicit_home_model_wins_over_environment(setup, db):
    coordinator, device, config, provider, transport, _, lease = setup
    config.realtime_model = "gpt-home-explicit"
    await db.commit()
    await coordinator.start(device.id, lease, transport)
    assert provider.sessions[0].config.model == "gpt-home-explicit"


async def test_shutdown_during_open_prevents_start_and_new_reservations(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    provider.release.clear()
    starting = asyncio.create_task(coordinator.start(device.id, lease, transport))
    await provider.entered.wait()
    closing = asyncio.create_task(coordinator.shutdown())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    provider.release.set()
    await asyncio.gather(starting, closing)
    assert await rows(db) == []
    assert provider.close_count == 1
    await coordinator.start(device.id, lease, transport)
    assert transport.controls[-1].code == "AI_UNAVAILABLE"
    assert len(provider.sessions) == 1


@pytest.mark.parametrize("operation", ["disconnect", "shutdown", "cancel_handler"])
async def test_blocked_started_has_bounded_terminal_cleanup(setup, db, monkeypatch, operation):
    coordinator, device, _, provider, transport, _, lease = setup
    monkeypatch.setattr(
        "app.ai.conversation_coordinator._CONTROL_SEND_TIMEOUT_SECONDS", 0.05, raising=False
    )
    blocked = BlockedTransport(transport.factory, "conversation.started")
    starting = asyncio.create_task(coordinator.start(device.id, lease, blocked))
    await asyncio.wait_for(blocked.entered.wait(), timeout=1)
    if operation == "cancel_handler":
        starting.cancel()
    closing = asyncio.create_task(
        coordinator.disconnect(device.id, lease)
        if operation == "disconnect"
        else coordinator.shutdown()
    )
    try:
        _, pending = await asyncio.wait({starting, closing}, timeout=0.8)
        assert not pending, "control backpressure must not block terminal cleanup"
        (row,) = await rows(db)
        assert row.status == ConversationStatus.CLOSED
        assert row.outcome == ConversationOutcome.FAILED
        assert provider.close_count == 1
        assert device.id not in coordinator._active
        assert blocked.exited.is_set()
    finally:
        blocked.release.set()
        await asyncio.gather(starting, closing, return_exceptions=True)


async def test_cancelled_started_send_closes_failed_without_stranding_reservation(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    blocked = BlockedTransport(transport.factory, "conversation.started", cancel_send=True)
    blocked.release.set()
    result = await asyncio.gather(
        coordinator.start(device.id, lease, blocked), return_exceptions=True
    )
    assert result == [None]
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.FAILED
    assert provider.close_count == 1
    assert device.id not in coordinator._active


@pytest.mark.parametrize("cancel_handler", [False, True])
async def test_blocked_ended_releases_before_control_and_shutdown_is_bounded(
    setup, db, monkeypatch, cancel_handler
):
    coordinator, device, _, provider, transport, _, lease = setup
    monkeypatch.setattr(
        "app.ai.conversation_coordinator._CONTROL_SEND_TIMEOUT_SECONDS", 0.05, raising=False
    )
    blocked = BlockedTransport(transport.factory, "conversation.ended")
    await coordinator.start(device.id, lease, blocked)
    stopping = asyncio.create_task(coordinator.stop(device.id, lease, "visitor_finished"))
    await asyncio.wait_for(blocked.entered.wait(), timeout=1)
    if cancel_handler:
        stopping.cancel()
    shutdown = None
    try:
        assert device.id not in coordinator._active, "delivery must follow reservation release"
        (row,) = await rows(db)
        assert row.status == ConversationStatus.CLOSED
        assert row.outcome == ConversationOutcome.REGISTERED
        assert provider.close_count == 1
        shutdown = asyncio.create_task(coordinator.shutdown())
        _, pending = await asyncio.wait({stopping, shutdown}, timeout=0.8)
        assert not pending
        assert blocked.exited.is_set(), "shutdown must join pending terminal notification"
    finally:
        blocked.release.set()
        await asyncio.gather(stopping, *([shutdown] if shutdown else []), return_exceptions=True)


async def test_cancelled_ended_send_does_not_retain_terminal_reservation(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    blocked = BlockedTransport(transport.factory, "conversation.ended", cancel_send=True)
    blocked.release.set()
    await coordinator.start(device.id, lease, blocked)
    result = await asyncio.gather(
        coordinator.stop(device.id, lease, "visitor_finished"), return_exceptions=True
    )
    assert result == [None]
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.REGISTERED
    assert provider.close_count == 1
    assert device.id not in coordinator._active


@pytest.mark.parametrize("reason", ["visitor_finished", "device_disconnected"])
async def test_replacement_start_reconciles_closing_without_old_lease(
    setup, db, monkeypatch, reason
):
    coordinator, device, _, provider, transport, registry, lease = setup
    await coordinator.start(device.id, lease, transport)
    original = AsyncSession.commit
    unavailable = True

    async def commit(session):
        if unavailable:
            raise RuntimeError("private database outage")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    if reason == "visitor_finished":
        await coordinator.stop(device.id, lease, reason)
    replacement = Transport(transport.factory)
    new_lease = await registry.claim(device.id, replacement)
    # A replaced owner's disconnect begins (or joins) only its own prior closure.
    await coordinator.disconnect(device.id, lease)
    assert provider.close_count == 1
    unavailable = False
    await coordinator.start(device.id, new_lease, replacement)
    assert replacement.controls[-2].type == "conversation.started"
    assert replacement.controls[-1].state == "listening"
    conversations = await rows(db)
    assert len(conversations) == 2
    closed = next(row for row in conversations if row.status == ConversationStatus.CLOSED)
    assert closed.outcome == (
        ConversationOutcome.REGISTERED
        if reason == "visitor_finished"
        else ConversationOutcome.ABANDONED
    )
    assert provider.close_count == 1
    assert len(provider.sessions) == 2


async def test_replacement_cannot_initiate_close_of_active_predecessor(setup, db):
    coordinator, device, _, provider, transport, registry, lease = setup
    await coordinator.start(device.id, lease, transport)
    replacement = Transport(transport.factory)
    new_lease = await registry.claim(device.id, replacement)
    await coordinator.stop(device.id, new_lease, "visitor_finished")
    await coordinator.disconnect(device.id, new_lease)
    await coordinator.start(device.id, new_lease, replacement)
    assert replacement.controls[-1].code == "CONVERSATION_ALREADY_ACTIVE"
    (row,) = await rows(db)
    assert row.status == ConversationStatus.OPEN
    assert provider.close_count == 0


async def test_shutdown_retries_transient_closure_without_socket(setup, db, monkeypatch):
    coordinator, device, _, provider, transport, registry, lease = setup
    await coordinator.start(device.id, lease, transport)
    await registry.unregister(lease)
    original = AsyncSession.commit
    attempts = 0

    async def commit(session):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private temporary outage")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    await coordinator.shutdown()
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.ABANDONED
    assert provider.close_count == 1
    assert attempts == 2
    assert not coordinator._active
    assert not coordinator._tasks


async def test_persistent_shutdown_failure_is_explicit_safe_and_retryable(setup, db, monkeypatch):
    coordinator, device, _, provider, transport, registry, lease = setup
    await coordinator.start(device.id, lease, transport)
    await registry.unregister(lease)
    original = AsyncSession.commit
    unavailable = True
    attempts = 0

    async def commit(session):
        nonlocal attempts
        attempts += 1
        if unavailable:
            raise RuntimeError("private persistent outage")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    try:
        with pytest.raises(RuntimeError, match="^AI_UNAVAILABLE$") as raised:
            await coordinator.shutdown()
        assert raised.value.correlation_id
        assert raised.value.__context__ is None
        assert attempts == 2
        assert provider.close_count == 1
        (row,) = await rows(db)
        assert row.status == ConversationStatus.OPEN
        assert device.id in coordinator._active
        assert not coordinator._tasks, "failed reconciliation must leave no running retry jobs"
    finally:
        unavailable = False
    await coordinator.shutdown()
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.ABANDONED
    assert provider.close_count == 1
    assert not coordinator._active
    assert not coordinator._tasks


@pytest.mark.parametrize("message_type", ["conversation.started", "conversation.ended"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_delayed_control_cancellation_does_not_delay_durable_cleanup(
    setup, db, monkeypatch, message_type, cleanup_fails
):
    coordinator, device, _, provider, transport, _, lease = setup
    monkeypatch.setattr("app.ai.conversation_coordinator._CONTROL_SEND_TIMEOUT_SECONDS", 0.03)
    slow = SlowCancellationTransport(transport.factory, message_type, cleanup_fails=cleanup_fails)
    if message_type == "conversation.ended":
        await coordinator.start(device.id, lease, slow)
        operation = asyncio.create_task(coordinator.stop(device.id, lease, "visitor_finished"))
    else:
        operation = asyncio.create_task(coordinator.start(device.id, lease, slow))
    await asyncio.wait_for(slow.entered.wait(), timeout=1)
    shutdown = asyncio.create_task(coordinator.shutdown())
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    try:
        await asyncio.wait_for(slow.cancel_received.wait(), timeout=1)
        _, pending = await asyncio.wait({operation, shutdown}, timeout=0.4)
        assert not pending, "durable cleanup must not await delayed transport cancellation"
        assert operation.result() is None
        assert shutdown.result() is None
        (row,) = await rows(db)
        assert row.status == ConversationStatus.CLOSED
        assert row.outcome == (
            ConversationOutcome.FAILED
            if message_type == "conversation.started"
            else ConversationOutcome.REGISTERED
        )
        assert provider.close_count == 1
        assert not coordinator._active
        assert not coordinator._tasks
        child = slow.send_task
        assert child is not None and not child.done()
        assert child in coordinator._control_tasks
        assert not any(message.type == message_type for message in slow.controls)

        slow.release_cleanup.set()
        # asyncio.wait observes completion without consuming a failed task's exception.
        await asyncio.wait({child}, timeout=1)
        assert child.done()
        if not cleanup_fails:
            assert child.cancelled()
        assert not coordinator._control_tasks
        assert not any(message.type == message_type for message in slow.controls)
        reference = weakref.ref(child)
        slow.send_task = None
        del child
        gc.collect()
        await asyncio.sleep(0)
        assert reference() is None
        assert not unhandled, "the coordinator must consume eventual child failures"
    finally:
        slow.release_cleanup.set()
        await asyncio.gather(operation, shutdown, return_exceptions=True)
        if slow.send_task is not None:
            await asyncio.gather(slow.send_task, return_exceptions=True)
        loop.set_exception_handler(old_handler)


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def input_frame(active, seq=1, payload=b"\x00\x00"):
    return AudioFrame(active.stream_id, seq, AudioDirection.DEVICE_TO_SERVER, payload)


async def assert_audio_closed(coordinator, active, provider, transport, db, code=None):
    await eventually(lambda: not coordinator._active and not coordinator._tasks)
    assert all(task.done() for task in active.workers)
    assert not coordinator._control_tasks
    assert active.input_queue.empty() and active.output_queue.empty()
    assert not active.residual
    assert provider.close_count == 1
    ended = [m for m in transport.controls if m.type == "conversation.ended"]
    assert len(ended) == 1
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED
    if code:
        assert [m.code for m in transport.controls if m.type == "conversation.error"] == [code]
        assert row.outcome == ConversationOutcome.FAILED
        assert ended[0].outcome == "failed"


async def test_audio_input_forwarding_and_named_worker_cleanup(setup, db, settings):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    before = active.last_activity
    assert active.input_queue.maxsize == settings.audio_input_queue_frames == 50
    assert active.output_queue.maxsize == settings.audio_output_queue_frames
    assert {t.get_name().rsplit(":", 1)[-1] for t in active.workers} == {
        "input-pump",
        "provider-events",
        "output-sender",
        "max-timer",
        "idle-timer",
    }
    await coordinator.receive_audio(device.id, lease, input_frame(active))
    await coordinator.receive_audio(device.id, lease, input_frame(active, 3, b"\x12\x34"))
    await eventually(lambda: len(provider.sessions[0].received_audio) == 2)
    assert provider.sessions[0].received_audio == [b"\x00\x00", b"\x12\x34"]
    assert active.last_activity == before
    await coordinator.stop(device.id, lease, "visitor_finished")
    await assert_audio_closed(coordinator, active, provider, transport, db)


@pytest.mark.parametrize(
    "change,code",
    [
        ({"stream_id": uuid4()}, "STREAM_NOT_FOUND"),
        ({"direction": AudioDirection.SERVER_TO_DEVICE}, "INVALID_AUDIO_FRAME"),
        ({"direction": 1}, "INVALID_AUDIO_FRAME"),
        ({"seq": True}, "INVALID_SEQUENCE"),
        ({"seq": 1.0}, "INVALID_SEQUENCE"),
        ({"seq": 0}, "INVALID_SEQUENCE"),
        ({"seq": 2**64}, "INVALID_SEQUENCE"),
        ({"payload": b""}, "INVALID_AUDIO_FRAME"),
        ({"payload": b"a"}, "INVALID_AUDIO_FRAME"),
        ({"payload": b"a" * 962}, "INVALID_AUDIO_FRAME"),
    ],
)
async def test_audio_rejects_invalid_frame_without_advancing_sequence(setup, change, code):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    with pytest.raises(AudioFrameError) as raised:
        await coordinator.receive_audio(device.id, lease, replace(input_frame(active), **change))
    assert raised.value.code == code
    await coordinator.receive_audio(device.id, lease, input_frame(active))
    await eventually(lambda: bool(provider.sessions[0].received_audio))
    assert provider.sessions[0].received_audio == [b"\x00\x00"]
    for seq in [1, 0]:
        with pytest.raises(AudioFrameError, match="INVALID_SEQUENCE"):
            await coordinator.receive_audio(device.id, lease, input_frame(active, seq))


async def test_audio_ownership_and_inactive_stream(setup):
    coordinator, device, _, provider, transport, registry, lease = setup
    with pytest.raises(AudioFrameError, match="STREAM_NOT_FOUND"):
        await coordinator.receive_audio(
            device.id, lease, AudioFrame(uuid4(), 1, AudioDirection.DEVICE_TO_SERVER, b"aa")
        )
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    for wrong in [
        replace(lease, generation=lease.generation + 1),
        replace(lease, socket=Transport(transport.factory)),
        replace(lease, device_id=uuid4()),
    ]:
        with pytest.raises(AudioFrameError, match="STREAM_NOT_OWNED"):
            await coordinator.receive_audio(device.id, wrong, input_frame(active))
    await registry.claim(device.id, Transport(transport.factory))
    with pytest.raises(AudioFrameError, match="STREAM_NOT_OWNED"):
        await coordinator.receive_audio(device.id, lease, input_frame(active))
    assert not provider.sessions[0].received_audio


async def test_arbitrary_deltas_packetize_even_residual_and_do_not_mix_responses(setup):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    session = provider.sessions[0]
    await session.emit(ResponseStarted())
    for delta in [b"a" * 301, b"b" * 700, b"c" * 1000]:
        await session.emit(AudioDelta(delta))
    await session.emit(ResponseFinished())
    await session.emit(ResponseStarted())
    await session.emit(AudioDelta(b"d" * 4))
    await session.emit(ResponseFinished())
    await eventually(lambda: len(transport.audio) == 4)
    frames = [
        decode_audio_frame(data, expected_direction=AudioDirection.SERVER_TO_DEVICE)
        for data in transport.audio
    ]
    assert [frame.seq for frame in frames] == [1, 2, 3, 4]
    assert [len(frame.payload) for frame in frames] == [960, 960, 80, 4]
    assert (
        b"".join(frame.payload for frame in frames)
        == b"a" * 301 + b"b" * 700 + b"c" * 999 + b"d" * 4
    )
    assert {f.stream_id for f in frames} == {coordinator._active[device.id].stream_id}


async def test_final_transcripts_are_normalized_deduped_and_network_outside_transaction(
    setup, db, monkeypatch
):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    original_send = transport.send_control
    original_enter, original_exit = AsyncSession.__aenter__, AsyncSession.__aexit__
    entered = 0

    async def enter(session):
        nonlocal entered
        result = await original_enter(session)
        entered += 1
        return result

    async def exit_session(session, *args):
        nonlocal entered
        await original_exit(session, *args)
        entered -= 1

    async def send(message):
        if message.type == "conversation.transcript":
            assert entered == 0
        await original_send(message)

    monkeypatch.setattr(AsyncSession, "__aenter__", enter)
    monkeypatch.setattr(AsyncSession, "__aexit__", exit_session)
    monkeypatch.setattr(transport, "send_control", send)
    session = provider.sessions[0]
    for event in [
        TranscriptFinal(MessageRole.USER, "  Hola  ", "u1"),
        TranscriptFinal(MessageRole.USER, "duplicado", "u1"),
        TranscriptFinal(MessageRole.ASSISTANT, "x" * 9000, "a1"),
        TranscriptFinal(MessageRole.USER, "  ", "blank"),
    ]:
        await session.emit(event)
    await session.emit(SpeechStopped())
    await eventually(
        lambda: len([m for m in transport.controls if m.type == "conversation.state"]) == 2
    )
    transcripts = [m for m in transport.controls if m.type == "conversation.transcript"]
    assert [(m.role, m.text, m.final) for m in transcripts] == [
        ("user", "Hola", True),
        ("assistant", "x" * 8000, True),
    ]
    messages = (
        await db.scalars(select(ConversationMessage).order_by(ConversationMessage.created_at))
    ).all()
    assert [(m.content, m.metadata_) for m in messages] == [
        ("Hola", {"provider_item_id": "u1"}),
        ("x" * 8000, {"provider_item_id": "a1"}),
    ]


async def test_vad_states_and_semantic_activity(setup):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    for event, state in [
        (SpeechStarted(), "visitor_speaking"),
        (SpeechStopped(), "listening"),
        (ResponseStarted(), "assistant_speaking"),
        (ResponseFinished(), "listening"),
    ]:
        before = active.last_activity
        await provider.sessions[0].emit(event)
        await eventually(
            lambda before=before, state=state: (
                active.last_activity > before and active.state == state
            )
        )
        await eventually(
            lambda state=state: (
                transport.controls[-1].type == "conversation.state"
                and transport.controls[-1].state == state
            )
        )


async def test_barge_in_cancels_inflight_output_drains_residual_and_suppresses_late_audio(setup):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    session = provider.sessions[0]
    transport.audio_release.clear()
    await session.emit(ResponseStarted())
    await session.emit(AudioDelta(b"a" * 2001))
    await asyncio.wait_for(transport.audio_entered.wait(), 2)
    await session.emit(SpeechStarted())
    await eventually(
        lambda: any(
            m.type == "conversation.state" and m.state == "visitor_speaking"
            for m in transport.controls
        )
    )
    assert session.cancel_count == 1
    assert not transport.pending_playback
    assert not active.residual and active.output_queue.empty()
    clear_index = next(
        i for i, m in enumerate(transport.controls) if m.type == "conversation.audio.clear"
    )
    assert transport.controls[clear_index].reason == "barge_in"
    assert transport.controls[clear_index + 1].state == "visitor_speaking"
    transport.audio_release.set()
    await session.emit(AudioDelta(b"late" * 240))
    await session.emit(ResponseFinished())
    await session.emit(ResponseStarted())
    await session.emit(AudioDelta(b"b" * 960))
    await eventually(lambda: len(transport.audio) == 1)
    frame = decode_audio_frame(
        transport.audio[0], expected_direction=AudioDirection.SERVER_TO_DEVICE
    )
    assert frame.payload == b"b" * 960
    assert frame.seq > 1  # canceled in-flight frames must never reuse sequence numbers


async def test_input_overflow_fails_fast_with_queue_one(setup, db, settings, monkeypatch):
    settings.audio_input_queue_frames = 1
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    entered = asyncio.Event()

    async def blocked_send(data):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(provider.sessions[0], "send_audio", blocked_send)
    await coordinator.receive_audio(device.id, lease, input_frame(active))
    await asyncio.wait_for(entered.wait(), 2)
    await coordinator.receive_audio(device.id, lease, input_frame(active, 2))
    await asyncio.wait_for(coordinator.receive_audio(device.id, lease, input_frame(active, 3)), 1)
    await assert_audio_closed(coordinator, active, provider, transport, db, "AUDIO_INPUT_OVERFLOW")


async def test_output_overflow_cancels_then_clears_then_ends_with_queue_one(
    setup, db, settings, monkeypatch
):
    settings.audio_output_queue_frames = 1
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    session = provider.sessions[0]
    order = []
    original_cancel, original_send = session.cancel_response, transport.send_control

    async def cancel():
        order.append("cancel")
        await original_cancel()

    async def send(message):
        if message.type == "conversation.audio.clear":
            assert active.output_queue.empty() and not active.residual
            order.append("clear")
        if message.type == "conversation.ended":
            order.append("ended")
        await original_send(message)

    monkeypatch.setattr(session, "cancel_response", cancel)
    monkeypatch.setattr(transport, "send_control", send)
    transport.audio_release.clear()
    await session.emit(ResponseStarted())
    await session.emit(AudioDelta(b"a" * 960))
    await asyncio.wait_for(transport.audio_entered.wait(), 2)
    await session.emit(AudioDelta(b"a" * 1920))
    await assert_audio_closed(coordinator, active, provider, transport, db, "AUDIO_OUTPUT_OVERFLOW")
    assert order == ["cancel", "clear", "ended"]
    transport.audio_release.set()
    assert not transport.audio and not transport.pending_playback


async def test_local_output_overflow_repeated_and_racing_stop_has_one_durable_failure(setup, db):
    from app.models import Event

    coordinator, device, _, provider, transport, registry, _ = setup
    lease = await registry.claim(device.id, transport, capabilities=["audio_pcm16_v1"])
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    await asyncio.gather(
        coordinator.report_output_overflow(
            device.id, lease, active.conversation_id, active.stream_id
        ),
        coordinator.report_output_overflow(
            device.id, lease, active.conversation_id, active.stream_id
        ),
        coordinator.stop(device.id, lease, "visitor_finished"),
    )
    await assert_audio_closed(coordinator, active, provider, transport, db, "AUDIO_OUTPUT_OVERFLOW")
    assert provider.sessions[0].cancel_count == 1
    assert len([m for m in transport.controls if m.type == "conversation.audio.clear"]) == 1
    assert await registry.is_current(lease)
    failures = (
        await db.scalars(select(Event).where(Event.event_type == "conversation_failed"))
    ).all()
    assert [failure.payload for failure in failures] == [
        {"conversation_id": str(active.conversation_id), "code": "AUDIO_OUTPUT_OVERFLOW"}
    ]


async def test_revoked_lease_cannot_report_playback_overflow_for_successor(setup):
    coordinator, device, _, _, transport, registry, old_lease = setup
    lease = await registry.claim(device.id, transport, capabilities=["audio_pcm16_v1"])
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    with pytest.raises(AudioFrameError) as caught:
        await coordinator.report_output_overflow(
            device.id, old_lease, active.conversation_id, active.stream_id
        )
    assert caught.value.code == "STREAM_NOT_OWNED"
    assert not active.closing
    assert await registry.is_current(lease)


@pytest.mark.parametrize(
    "timer,code,reason",
    [
        ("max", "CONVERSATION_TIMEOUT", "conversation_timeout"),
        ("idle", "CONVERSATION_IDLE_TIMEOUT", "conversation_idle_timeout"),
    ],
)
async def test_audio_timers_fail_and_silent_input_cannot_extend_idle(
    setup, db, settings, timer, code, reason
):
    coordinator, device, _, provider, transport, _, lease = setup
    # Settings stay production validated; tests use a short configurable deadline.
    if timer == "max":
        settings.conversation_max_seconds = 1
        settings.conversation_idle_seconds = 10
    else:
        settings.conversation_idle_seconds = 1
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    before = active.last_activity
    for seq in range(1, 7):
        await coordinator.receive_audio(device.id, lease, input_frame(active, seq))
        await asyncio.sleep(0.1)
    assert active.last_activity == before
    await assert_audio_closed(coordinator, active, provider, transport, db, code)
    assert transport.controls[-1].reason == reason


@pytest.mark.parametrize("source", ["failure", "eof", "input", "output"])
async def test_provider_or_transport_failure_closes_once_without_worker_leaks(
    setup, db, monkeypatch, source
):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    session = provider.sessions[0]

    async def fail(*args):
        raise RuntimeError("private provider raw payload")

    if source == "failure":
        await session.emit(ProviderFailure("AI_UNAVAILABLE"))
    elif source == "eof":
        await session.close()
        provider.close_count = 0  # the coordinator still must call close once
    elif source == "input":
        monkeypatch.setattr(session, "send_audio", fail)
        await coordinator.receive_audio(device.id, lease, input_frame(active))
    else:
        monkeypatch.setattr(transport, "send_audio", fail)
        await session.emit(ResponseStarted())
        await session.emit(AudioDelta(b"x" * 960))
    await assert_audio_closed(coordinator, active, provider, transport, db, "AI_UNAVAILABLE")
    assert "private" not in str(transport.controls)


async def test_terminal_race_stop_disconnect_failure_is_exactly_once(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    await provider.sessions[0].emit(ProviderFailure("AI_UNAVAILABLE"))
    await asyncio.gather(
        coordinator.stop(device.id, lease, "visitor_finished"),
        coordinator.disconnect(device.id, lease),
        coordinator.shutdown(),
    )
    await assert_audio_closed(coordinator, active, provider, transport, db)
    assert len([m for m in transport.controls if m.type == "conversation.error"]) <= 1


async def test_retired_ownership_cleans_durably_without_late_controls_or_audio(setup, db):
    coordinator, device, _, provider, transport, registry, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    await registry.claim(device.id, Transport(transport.factory))
    await provider.sessions[0].emit(SpeechStarted())
    await coordinator.disconnect(device.id, lease)
    await eventually(lambda: not coordinator._tasks)
    assert [m.type for m in transport.controls] == ["conversation.started", "conversation.state"]
    assert not transport.audio
    assert all(t.done() for t in active.workers)
    (row,) = await rows(db)
    assert row.status == ConversationStatus.CLOSED and row.outcome == ConversationOutcome.ABANDONED


@pytest.mark.parametrize("source", ["input", "output"])
async def test_cancelled_dependency_is_terminal_not_a_silent_worker_exit(
    setup, db, monkeypatch, source
):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]

    async def cancelled(*args):
        raise asyncio.CancelledError

    if source == "input":
        monkeypatch.setattr(provider.sessions[0], "send_audio", cancelled)
        await coordinator.receive_audio(device.id, lease, input_frame(active))
    else:
        monkeypatch.setattr(transport, "send_audio", cancelled)
        await provider.sessions[0].emit(ResponseStarted())
        await provider.sessions[0].emit(AudioDelta(b"x" * 960))
    await assert_audio_closed(coordinator, active, provider, transport, db, "AI_UNAVAILABLE")


async def test_barge_in_clears_device_playback_after_provider_response_finished(setup):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    session = provider.sessions[0]
    await session.emit(ResponseStarted())
    await session.emit(AudioDelta(b"a" * 960))
    await session.emit(ResponseFinished())
    await eventually(lambda: bool(transport.pending_playback))
    await session.emit(SpeechStarted())
    await eventually(
        lambda: any(
            m.type == "conversation.state" and m.state == "visitor_speaking"
            for m in transport.controls
        )
    )
    assert not transport.pending_playback
    assert session.cancel_count == 0, "the provider response is already known to be inactive"


async def test_output_overflow_cancel_backpressure_cannot_prevent_terminal_cleanup(
    setup, db, settings, monkeypatch
):
    settings.audio_output_queue_frames = 1
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    session = provider.sessions[0]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_cancel():
        entered.set()
        await release.wait()

    monkeypatch.setattr(session, "cancel_response", blocked_cancel)
    await session.emit(ResponseStarted())
    await session.emit(AudioDelta(b"a" * 2880))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        await assert_audio_closed(
            coordinator, active, provider, transport, db, "AUDIO_OUTPUT_OVERFLOW"
        )
    finally:
        release.set()


async def test_stop_during_barge_in_cancels_workers_and_does_not_restart_output(
    setup, db, monkeypatch
):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    session = provider.sessions[0]
    entered = asyncio.Event()

    async def blocked_cancel():
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(session, "cancel_response", blocked_cancel)
    transport.audio_release.clear()
    await session.emit(ResponseStarted())
    await session.emit(AudioDelta(b"a" * 961))
    await asyncio.wait_for(transport.audio_entered.wait(), 2)
    await session.emit(SpeechStarted())
    await asyncio.wait_for(entered.wait(), 2)
    await coordinator.stop(device.id, lease, "visitor_finished")
    await assert_audio_closed(coordinator, active, provider, transport, db)
    transport.audio_release.set()
    assert not transport.audio


async def test_semantic_event_moves_idle_deadline_but_not_absolute_deadline(setup, db, settings):
    settings.conversation_max_seconds = 2
    settings.conversation_idle_seconds = 1
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    await asyncio.sleep(0.65)
    await provider.sessions[0].emit(SpeechStarted())
    await asyncio.sleep(0.65)
    assert not active.closing
    await provider.sessions[0].emit(SpeechStopped())
    await asyncio.sleep(0.3)
    await provider.sessions[0].emit(ResponseStarted())
    await assert_audio_closed(coordinator, active, provider, transport, db, "CONVERSATION_TIMEOUT")


async def test_worker_terminal_database_failure_reports_once_and_remains_retryable(
    setup, db, monkeypatch
):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    original = AsyncSession.commit
    attempts = 0

    async def commit(session):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private database failure")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    await provider.sessions[0].emit(ProviderFailure("AI_UNAVAILABLE"))
    await eventually(lambda: attempts == 1 and not coordinator._tasks)
    assert [m.code for m in transport.controls if m.type == "conversation.error"] == [
        "AI_UNAVAILABLE"
    ]
    assert device.id in coordinator._active
    assert not active.workers
    await coordinator.shutdown()
    await assert_audio_closed(coordinator, active, provider, transport, db, "AI_UNAVAILABLE")


async def test_transcript_dedupe_uses_existing_durable_item(setup, db):
    coordinator, device, _, provider, transport, _, lease = setup
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    db.add(
        ConversationMessage(
            conversation_id=active.conversation_id,
            role=MessageRole.USER,
            content="original",
            metadata_={"provider_item_id": "existing"},
        )
    )
    await db.commit()
    await provider.sessions[0].emit(TranscriptFinal(MessageRole.USER, "duplicate", "existing"))
    await provider.sessions[0].emit(SpeechStopped())
    await eventually(lambda: transport.controls[-1].type == "conversation.state")
    assert not any(m.type == "conversation.transcript" for m in transport.controls)
    messages = (await db.scalars(select(ConversationMessage))).all()
    assert len(messages) == 1 and messages[0].content == "original"


@pytest.mark.parametrize("message_type", ["conversation.state", "conversation.transcript"])
async def test_stop_during_provider_control_never_requests_another_event(setup, db, message_type):
    coordinator, device, _, provider, transport, _, lease = setup
    gated = BlockedTransport(transport.factory, message_type)
    gated.release.set()  # Allow the initial listening state before gating a provider event.
    await coordinator.start(device.id, lease, gated)
    gated.release.clear()
    gated.entered.clear()
    before = len(gated.controls)
    active = coordinator._active[device.id]
    session = provider.sessions[0]
    event = (
        SpeechStarted()
        if message_type == "conversation.state"
        else TranscriptFinal(MessageRole.USER, "Hola", "gated")
    )
    await session.emit(event)
    await asyncio.wait_for(gated.entered.wait(), 1)
    stopping = asyncio.create_task(coordinator.stop(device.id, lease, "visitor_finished"))
    try:
        _, pending = await asyncio.wait({stopping}, timeout=0.5)
        assert not pending, "stop must finish without receiving another provider event"
        await assert_audio_closed(coordinator, active, provider, gated, db)
        assert not any(m.type == message_type for m in gated.controls[before:])
    finally:
        gated.release.set()
        if not stopping.done():
            await session.emit(SpeechStopped())  # release only the RED deadlock
        await asyncio.wait_for(stopping, 2)


class DeferredCancellation:
    """Canceled network work forbids delivery but can finish internal cleanup later."""

    def __init__(self, *, cleanup_fails=False):
        self.entered = asyncio.Event()
        self.cancel_received = asyncio.Event()
        self.release = asyncio.Event()
        self.task = None
        self.cleanup_fails = cleanup_fails

    async def __call__(self, *args):
        self.task = asyncio.current_task()
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancel_received.set()
            await self.release.wait()
            if self.cleanup_fails:
                raise RuntimeError("private delayed I/O cleanup failure") from None
            raise


async def assert_deferred_child_collected(coordinator, deferred):
    child = deferred.task
    assert child is not None and not child.done()
    assert child in coordinator._io_tasks
    reference = weakref.ref(child)
    deferred.release.set()
    await asyncio.wait({child}, timeout=1)
    assert child.done()
    await eventually(lambda: not coordinator._io_tasks)
    deferred.task = None
    del child
    gc.collect()
    await asyncio.sleep(0)
    assert reference() is None


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("source", ["input", "output"])
async def test_stop_detaches_slow_audio_send_cleanup_without_late_audio(
    setup, db, monkeypatch, cleanup_fails, source
):
    coordinator, device, _, provider, transport, _, lease = setup
    monkeypatch.setattr("app.ai.conversation_coordinator._AUDIO_SEND_TIMEOUT_SECONDS", 0.03)
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    deferred = DeferredCancellation(cleanup_fails=cleanup_fails)
    session = provider.sessions[0]
    target = transport if source == "output" else session
    original_send = target.send_audio

    async def send(data):
        await deferred(data)
        await original_send(data)

    monkeypatch.setattr(target, "send_audio", send)
    if source == "output":
        await session.emit(ResponseStarted())
        await session.emit(AudioDelta(b"a" * 960))
    else:
        await coordinator.receive_audio(device.id, lease, input_frame(active))
    await asyncio.wait_for(deferred.entered.wait(), 1)
    stopping = asyncio.create_task(coordinator.stop(device.id, lease, "visitor_finished"))
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    try:
        await asyncio.wait_for(deferred.cancel_received.wait(), 1)
        _, pending = await asyncio.wait({stopping}, timeout=0.5)
        assert not pending, "durable closure must not await canceled socket cleanup"
        await assert_audio_closed(coordinator, active, provider, transport, db)
        await assert_deferred_child_collected(coordinator, deferred)
        assert not transport.audio
        assert not session.received_audio
        assert not unhandled
    finally:
        deferred.release.set()
        await asyncio.wait_for(stopping, 2)
        loop.set_exception_handler(old_handler)


@pytest.mark.parametrize("trigger", ["overflow", "barge_in"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_cancel_response_slow_cleanup_cannot_delay_clear_or_durable_close(
    setup, db, settings, monkeypatch, trigger, cleanup_fails
):
    settings.audio_output_queue_frames = 1
    coordinator, device, _, provider, transport, _, lease = setup
    monkeypatch.setattr("app.ai.conversation_coordinator._PROVIDER_CANCEL_TIMEOUT_SECONDS", 0.03)
    await coordinator.start(device.id, lease, transport)
    active = coordinator._active[device.id]
    deferred = DeferredCancellation(cleanup_fails=cleanup_fails)
    session = provider.sessions[0]
    monkeypatch.setattr(session, "cancel_response", deferred)
    await session.emit(ResponseStarted())
    if trigger == "overflow":
        await session.emit(AudioDelta(b"a" * 2880))
    else:
        await session.emit(SpeechStarted())
    await asyncio.wait_for(deferred.entered.wait(), 1)
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    try:
        await asyncio.wait_for(deferred.cancel_received.wait(), 1)
        async with asyncio.timeout(0.5):
            await eventually(lambda: provider.close_count == 1 and not coordinator._tasks)
        code = "AUDIO_OUTPUT_OVERFLOW" if trigger == "overflow" else "AI_UNAVAILABLE"
        await assert_audio_closed(coordinator, active, provider, transport, db, code)
        clear = [m for m in transport.controls if m.type == "conversation.audio.clear"]
        assert len(clear) == 1
        assert clear[0].reason == ("audio_output_overflow" if trigger == "overflow" else "barge_in")
        await assert_deferred_child_collected(coordinator, deferred)
        assert not unhandled
    finally:
        deferred.release.set()
        if deferred.task is not None:
            await asyncio.gather(deferred.task, return_exceptions=True)
        loop.set_exception_handler(old_handler)


@pytest.mark.parametrize("correlated", [True, False])
async def test_real_adapter_cancel_completion_race_preserves_only_correlated_session(
    setup, db, monkeypatch, correlated
):
    from app.ai.openai_realtime import OpenAIRealtimeSession

    coordinator, device, _, provider, transport, _, lease = setup

    class Wire:
        def __init__(self):
            self.incoming = asyncio.Queue()
            self.sent = []
            self.close_count = 0

        def emit(self, event):
            self.incoming.put_nowait(json.dumps(event))

        async def recv(self):
            return await self.incoming.get()

        async def send(self, raw):
            event = json.loads(raw)
            self.sent.append(event)
            if event["type"] == "response.cancel":
                self.emit({"type": "response.done", "response": {"status": "completed"}})
                self.emit(
                    {
                        "type": "error",
                        "event_id": "server-error",
                        "error": {
                            "type": "invalid_request_error",
                            "code": "response_cancel_not_active",
                            "message": "No active response found",
                            "param": None,
                            "event_id": event.get("event_id") if correlated else "unrelated-client",
                        },
                    }
                )
                self.emit({"type": "input_audio_buffer.speech_stopped"})

        async def close(self):
            self.close_count += 1

    wire = Wire()
    session = OpenAIRealtimeSession(wire, lambda: None)

    async def open_session(config):
        return session

    monkeypatch.setattr(provider, "open_session", open_session)
    await coordinator.start(device.id, lease, transport)
    wire.emit({"type": "response.created"})
    wire.emit(
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"a" * 960).decode()}
    )
    await asyncio.wait_for(transport.audio_entered.wait(), 1)
    controls_before_barge_in = len(transport.controls)
    wire.emit({"type": "input_audio_buffer.speech_started"})
    if correlated:
        await eventually(
            lambda: (
                any(
                    m.type == "conversation.state" and m.state == "listening"
                    for m in transport.controls[controls_before_barge_in:]
                )
                or any(m.type == "conversation.error" for m in transport.controls)
            )
        )
        assert not any(m.type == "conversation.error" for m in transport.controls)
        assert wire.close_count == 0
        assert not transport.pending_playback
        (row,) = await rows(db)
        assert row.status == ConversationStatus.OPEN
        await coordinator.stop(device.id, lease, "visitor_finished")
    else:
        await eventually(lambda: not coordinator._active and not coordinator._tasks)
        assert [m.code for m in transport.controls if m.type == "conversation.error"] == [
            "AI_UNAVAILABLE"
        ]
        (row,) = await rows(db)
        assert row.outcome == ConversationOutcome.FAILED
    assert wire.close_count == 1
