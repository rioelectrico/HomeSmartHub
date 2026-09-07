"""Authenticated device WebSocket contracts."""

import asyncio
import hmac
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.conversation_coordinator import ConversationCoordinator
from app.ai.fake_realtime import FakeRealtimeProvider
from app.ai.realtime import AudioDelta, ResponseStarted, SpeechStopped
from app.devices.audio_protocol import (
    AudioDirection,
    AudioFrame,
    decode_audio_frame,
    encode_audio_frame,
)
from app.devices.challenges import canonical_hmac_message
from app.devices.connections import DeviceConnectionRegistry
from app.devices.credentials import provision_device, rotate_device_secret
from app.devices.presence import mark_online
from app.models import (
    AgentConfig,
    CommandStatus,
    Conversation,
    ConversationOutcome,
    ConversationStatus,
    Device,
    DeviceCommand,
    DeviceStatus,
    Event,
    HomeUser,
    Permission,
    RolePermission,
)
from app.websocket.device import handle_device_socket, router


class ScriptedWebSocket:
    """A deterministic ASGI WebSocket peer that reacts to server messages."""

    def __init__(
        self,
        first_text: str | None,
        *,
        secret: str | None = None,
        auth_digest: str | None = None,
        after_auth: list[dict[str, object] | bytes] | None = None,
        on_auth_ok: Callable[[dict[str, object]], Awaitable[None]] | None = None,
        disconnect_after_auth: bool = True,
        binary_auth: bool = False,
    ) -> None:
        self.scope = {"client": ("203.0.113.44", 4567)}
        self.secret = secret
        self.auth_digest = auth_digest
        self.after_auth = after_auth or []
        self.on_auth_ok = on_auth_ok
        self.disconnect_after_auth = disconnect_after_auth
        self.binary_auth = binary_auth
        self.auth_ok_sent = asyncio.Event()
        self.accepted = False
        self.sent: list[dict[str, object]] = []
        self.binary_sent: list[bytes] = []
        self.closed: list[tuple[int, str | None]] = []
        self._inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if first_text is not None:
            self._inbound.put_nowait({"type": "websocket.receive", "text": first_text})

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, Any]:
        return await self._inbound.get()

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)
        if payload["type"] == "auth.challenge":
            if self.binary_auth:
                self._inbound.put_nowait({"type": "websocket.receive", "bytes": b"PAUD"})
                return
            nonce = str(payload["nonce"])
            digest = self.auth_digest
            if digest is None and self.secret is not None:
                digest = hmac.new(
                    self.secret.encode(),
                    canonical_hmac_message("PI-000001", "boot-a", nonce),
                    sha256,
                ).hexdigest()
            response = {
                "type": "auth.response",
                "boot_id": "boot-a",
                "seq": 1,
                "nonce": nonce,
                "digest": digest or "0" * 64,
            }
            self._inbound.put_nowait({"type": "websocket.receive", "text": json.dumps(response)})
        elif payload["type"] == "auth.ok":
            if self.on_auth_ok is not None:
                await self.on_auth_ok(payload)
            for message in self.after_auth:
                self.feed(message)
            if self.disconnect_after_auth:
                self._inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
            self.auth_ok_sent.set()

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed.append((code, reason))
        self._inbound.put_nowait({"type": "websocket.disconnect", "code": code})

    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise RuntimeError("closed")
        self.binary_sent.append(data)

    def feed(self, message: dict[str, object] | bytes) -> None:
        frame = {"type": "websocket.receive"}
        frame["bytes" if isinstance(message, bytes) else "text"] = (
            message if isinstance(message, bytes) else json.dumps(message)
        )
        self._inbound.put_nowait(frame)


def _hello(*, version: str = "v1") -> str:
    return json.dumps(
        {
            "type": "device.hello",
            "version": version,
            "device_id": "PI-000001",
            "boot_id": "boot-a",
            "seq": 0,
        }
    )


async def _provision(db, home, settings):
    provisioned = await provision_device(db, home.id, "PI-000001", "Entrada", settings)
    await db.commit()
    return provisioned


def test_router_exposes_device_websocket_path() -> None:
    """Losing the route registration would make the device protocol unreachable."""

    assert any(getattr(route, "path", None) == "/ws/device" for route in router.routes)


async def test_valid_hello_authenticates_and_issues_one_upload_token(db, home, settings) -> None:
    """A valid HMAC handshake must issue one connection-bound upload credential."""

    provisioned = await _provision(db, home, settings)
    registry = DeviceConnectionRegistry()
    token_was_active = False

    async def inspect_auth(payload: dict[str, object]) -> None:
        nonlocal token_was_active
        token_was_active = await registry.verify_upload_token(
            provisioned.device.id, str(payload["upload_token"])
        )

    socket = ScriptedWebSocket(_hello(), secret=provisioned.secret, on_auth_ok=inspect_auth)

    await handle_device_socket(socket, db, settings, registry)

    assert socket.accepted
    assert [message["type"] for message in socket.sent].count("auth.challenge") == 1
    assert [message["type"] for message in socket.sent].count("auth.ok") == 1
    assert token_was_active
    assert registry.get(provisioned.device.id) is None


async def test_authenticated_ack_and_result_advance_durable_command(db, home, settings) -> None:
    """Validated command replies must reach the locked lifecycle handlers."""

    provisioned = await _provision(db, home, settings)
    command = DeviceCommand(
        device_id=provisioned.device.id,
        command="camera.capture",
        payload={},
        status=CommandStatus.SENT,
        sent_at=datetime.now(UTC),
    )
    db.add(command)
    await db.commit()
    socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        after_auth=[
            {
                "type": "command.ack",
                "boot_id": "boot-a",
                "seq": 2,
                "command_id": str(command.id),
                "status": "accepted",
            },
            {
                "type": "command.result",
                "boot_id": "boot-a",
                "seq": 3,
                "command_id": str(command.id),
                "status": "completed",
                "result": {"image_id": "image-1", "token": "redact-me"},
            },
        ],
    )

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    await db.refresh(command)
    assert command.status == CommandStatus.COMPLETED
    assert command.result == {"image_id": "image-1"}


async def test_out_of_order_result_closes_with_stable_protocol_error(db, home, settings) -> None:
    """A RESULT before ACK must be rejected rather than silently discarded."""

    provisioned = await _provision(db, home, settings)
    command = DeviceCommand(
        device_id=provisioned.device.id,
        command="camera.capture",
        payload={},
        status=CommandStatus.SENT,
        sent_at=datetime.now(UTC),
    )
    db.add(command)
    await db.commit()
    socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        after_auth=[
            {
                "type": "command.result",
                "boot_id": "boot-a",
                "seq": 2,
                "command_id": str(command.id),
                "status": "completed",
                "result": {"image_id": "too-early"},
            }
        ],
    )

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    await db.refresh(command)
    assert command.status == CommandStatus.SENT
    assert socket.closed == [(4000, "PROTOCOL_ERROR")]


async def test_cancellation_during_replacement_close_leaves_no_ghost_connection(
    db, home, settings, monkeypatch
) -> None:
    """Cancellation after publication must revoke both replacement ownership and its token."""

    provisioned = await _provision(db, home, settings)
    device_id = provisioned.device.id
    registry = DeviceConnectionRegistry()
    old_socket = AsyncMock()
    close_started = asyncio.Event()
    never_close = asyncio.Event()

    async def block_old_close(**_: object) -> None:
        close_started.set()
        await never_close.wait()

    old_socket.close.side_effect = block_old_close
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    await registry.register(
        device_id,
        old_socket,
        upload_token="old-token",
        upload_token_expires_at=expires_at,
    )
    monkeypatch.setattr("app.websocket.device.token_urlsafe", lambda _: "new-token")
    new_socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        disconnect_after_auth=False,
    )

    handler = asyncio.create_task(handle_device_socket(new_socket, db, settings, registry))
    await close_started.wait()
    handler.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handler

    assert registry.get(device_id) is None
    assert not await registry.verify_upload_token(device_id, "old-token")
    assert not await registry.verify_upload_token(device_id, "new-token")
    assert all(message["type"] != "auth.ok" for message in new_socket.sent)


async def test_replacement_in_old_cleanup_gap_remains_durably_online(
    db, async_engine, home, settings
) -> None:
    """Retired cleanup must not mark a replacement generation offline."""

    provisioned = await _provision(db, home, settings)
    device_id = provisioned.device.id
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    old_unregistered = asyncio.Event()
    release_old_cleanup = asyncio.Event()

    class GapRegistry(DeviceConnectionRegistry):
        old_socket: ScriptedWebSocket | None = None

        async def unregister(self, ownership, socket=None) -> bool:
            removed = await super().unregister(ownership, socket)
            retired_socket = getattr(ownership, "socket", socket)
            if removed and retired_socket is self.old_socket:
                old_unregistered.set()
                await release_old_cleanup.wait()
            return removed

    registry = GapRegistry()
    old_socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        disconnect_after_auth=False,
    )
    registry.old_socket = old_socket

    async def run(socket: ScriptedWebSocket) -> None:
        async with factory() as session:
            await handle_device_socket(socket, session, settings, registry)

    old_handler = asyncio.create_task(run(old_socket))
    await old_socket.auth_ok_sent.wait()
    old_socket._inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
    await old_unregistered.wait()

    new_socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        disconnect_after_auth=False,
    )
    new_handler = asyncio.create_task(run(new_socket))
    await new_socket.auth_ok_sent.wait()
    release_old_cleanup.set()
    await old_handler

    try:
        async with factory() as observer:
            stored = await observer.get(Device, device_id)
            assert stored is not None
            offline_count = await observer.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "device_offline")
            )
        assert registry.get(device_id) is new_socket
        assert stored.status == DeviceStatus.ONLINE
        assert offline_count == 0
    finally:
        new_socket._inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await new_handler


async def test_handler_drops_plaintext_upload_token_after_sending(db, home, settings) -> None:
    """A live handler must not retain the one-time plaintext upload credential."""

    provisioned = await _provision(db, home, settings)
    registry = DeviceConnectionRegistry()
    socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        disconnect_after_auth=False,
    )
    task = asyncio.create_task(handle_device_socket(socket, db, settings, registry))
    await socket.auth_ok_sent.wait()

    auth_ok = next(message for message in socket.sent if message["type"] == "auth.ok")
    plaintext_token = auth_ok["upload_token"]
    frame = task.get_coro().cr_frame
    assert frame is not None
    assert plaintext_token not in frame.f_locals.values()

    socket._inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
    await task


async def test_unsupported_version_uses_stable_private_close_code(db, settings) -> None:
    """Treating a future protocol version as generic auth would hide incompatibility."""

    socket = ScriptedWebSocket(_hello(version="v2"))

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    assert socket.closed == [(4003, "PROTOCOL_VERSION_UNSUPPORTED")]


async def test_missing_version_is_a_protocol_error_not_an_unsupported_version(db, settings) -> None:
    """A malformed hello must not be misclassified as an explicit version mismatch."""

    hello = json.loads(_hello())
    del hello["version"]
    socket = ScriptedWebSocket(json.dumps(hello))

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    assert socket.closed == [(4000, "PROTOCOL_ERROR")]


async def test_bad_hmac_is_rejected_without_registering(db, home, settings) -> None:
    """An incorrect digest must never create an authenticated connection."""

    provisioned = await _provision(db, home, settings)
    registry = DeviceConnectionRegistry()
    socket = ScriptedWebSocket(_hello(), auth_digest="0" * 64)

    await handle_device_socket(socket, db, settings, registry)

    assert socket.closed == [(4002, "AUTH_FAILED")]
    assert registry.get(provisioned.device.id) is None


async def test_disabled_device_is_rejected_before_challenge(db, home, settings) -> None:
    """A disabled credential must not receive a usable challenge."""

    provisioned = await _provision(db, home, settings)
    provisioned.device.status = DeviceStatus.DISABLED
    await db.commit()
    socket = ScriptedWebSocket(_hello(), secret=provisioned.secret)

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    assert socket.closed == [(4002, "AUTH_FAILED")]
    assert socket.sent == []


async def test_device_disabled_after_challenge_verification_never_receives_auth_ok(
    db, async_engine, home, settings
) -> None:
    """A concurrent disable must win before Task 6 exposes an authenticated session."""

    provisioned = await _provision(db, home, settings)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    class DisablingRegistry(DeviceConnectionRegistry):
        async def claim(self, *args, **kwargs):
            async with factory() as admin:
                device = await admin.get(Device, provisioned.device.id)
                assert device is not None
                device.status = DeviceStatus.DISABLED
                await admin.commit()
            return await super().claim(*args, **kwargs)

    registry = DisablingRegistry()
    socket = ScriptedWebSocket(_hello(), secret=provisioned.secret)

    await handle_device_socket(socket, db, settings, registry)

    assert socket.closed == [(4002, "AUTH_FAILED")]
    assert all(message["type"] != "auth.ok" for message in socket.sent)
    assert registry.get(provisioned.device.id) is None


async def test_device_disabled_after_online_commit_never_receives_auth_ok(
    db, async_engine, home, settings, monkeypatch
) -> None:
    """The final authorization boundary must refresh disablement after ONLINE commits."""

    provisioned = await _provision(db, home, settings)
    device_id = provisioned.device.id
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    online_committed = asyncio.Event()
    resume_authentication = asyncio.Event()
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    real_mark_online = mark_online
    call_count = 0

    class BlockingCloseSocket(ScriptedWebSocket):
        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            close_started.set()
            await release_close.wait()
            await super().close(code=code, reason=reason)

    async def pause_after_first_online(*args, **kwargs) -> bool:
        nonlocal call_count
        transitioned = await real_mark_online(*args, **kwargs)
        call_count += 1
        if call_count == 1:
            online_committed.set()
            await resume_authentication.wait()
        return transitioned

    monkeypatch.setattr("app.websocket.device.mark_online", pause_after_first_online)
    monkeypatch.setattr("app.websocket.device.token_urlsafe", lambda _: "race-token")
    registry = DeviceConnectionRegistry()
    socket = BlockingCloseSocket(_hello(), secret=provisioned.secret)
    handler = asyncio.create_task(handle_device_socket(socket, db, settings, registry))
    await online_committed.wait()

    async with factory() as admin:
        device = await admin.get(Device, device_id)
        assert device is not None
        device.status = DeviceStatus.DISABLED
        await admin.commit()
    resume_authentication.set()
    await close_started.wait()
    try:
        assert registry.get(device_id) is None
        assert not await registry.verify_upload_token(device_id, "race-token")
    finally:
        release_close.set()
        await handler

    assert socket.closed == [(4002, "AUTH_FAILED")]
    assert all(message["type"] != "auth.ok" for message in socket.sent)
    assert registry.get(device_id) is None
    assert not await registry.verify_upload_token(device_id, "race-token")


async def test_disable_cannot_commit_during_final_auth_ok_send(
    db, async_engine, home, settings, monkeypatch
) -> None:
    """The final enabled check and auth success send must share one row lock."""

    provisioned = await _provision(db, home, settings)
    device_id = provisioned.device.id
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    auth_send_started = asyncio.Event()
    release_auth_send = asyncio.Event()

    async def pause_auth_send(_: dict[str, object]) -> None:
        auth_send_started.set()
        await release_auth_send.wait()

    monkeypatch.setattr("app.websocket.device.token_urlsafe", lambda _: "final-race-token")
    registry = DeviceConnectionRegistry()
    socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        on_auth_ok=pause_auth_send,
    )
    handler = asyncio.create_task(handle_device_socket(socket, db, settings, registry))
    await auth_send_started.wait()

    try:
        async with factory() as admin:
            await admin.execute(text("SET LOCAL lock_timeout = '200ms'"))
            device = await admin.get(Device, device_id)
            assert device is not None
            device.status = DeviceStatus.DISABLED
            with pytest.raises(OperationalError) as blocked:
                await admin.commit()
            assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
            await admin.rollback()
    finally:
        release_auth_send.set()
        await handler

    async with factory() as admin:
        device = await admin.get(Device, device_id)
        assert device is not None
        device.status = DeviceStatus.DISABLED
        await admin.commit()

    assert [message["type"] for message in socket.sent].count("auth.ok") == 1
    assert registry.get(device_id) is None
    assert not await registry.verify_upload_token(device_id, "final-race-token")


async def test_oversized_handshake_payload_is_rejected(db, settings) -> None:
    """Parsing before enforcing the byte cap would permit unbounded handshake allocation."""

    settings.max_websocket_message_bytes = 128
    socket = ScriptedWebSocket("x" * 129)

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    assert socket.closed == [(4004, "MESSAGE_TOO_LARGE")]


async def test_duplicate_sequence_closes_authenticated_socket(db, home, settings) -> None:
    """Accepting a repeated sequence would permit replay within one connection."""

    provisioned = await _provision(db, home, settings)
    registry = DeviceConnectionRegistry()
    socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        after_auth=[
            {
                "type": "device.heartbeat",
                "boot_id": "boot-a",
                "seq": 1,
                "uptime_seconds": 5,
            }
        ],
    )

    await handle_device_socket(socket, db, settings, registry)

    assert socket.closed == [(4005, "SEQUENCE_INVALID")]
    auth_ok = next(message for message in socket.sent if message["type"] == "auth.ok")
    assert not await registry.verify_upload_token(
        provisioned.device.id, str(auth_ok["upload_token"])
    )


async def test_status_uses_scope_ip_and_disconnect_revokes_session(db, home, settings) -> None:
    """Status must trust only transport IP and disconnect must revoke upload authorization."""

    provisioned = await _provision(db, home, settings)
    registry = DeviceConnectionRegistry()
    socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        after_auth=[
            {
                "type": "device.status",
                "boot_id": "boot-a",
                "seq": 2,
                "firmware_version": "1.0.0",
                "hardware_model": "ESP32-P4",
                "uptime_seconds": 7,
                "ethernet": "online",
                "camera": "ready",
                "microphone": "ready",
                "speaker": "ready",
                "free_heap_bytes": 8192,
            }
        ],
    )

    await handle_device_socket(socket, db, settings, registry)

    status_event = await db.scalar(select(Event).where(Event.event_type == "device_status"))
    assert status_event is not None
    assert status_event.payload["ip_address"] == "203.0.113.44"
    auth_ok = next(message for message in socket.sent if message["type"] == "auth.ok")
    assert not await registry.verify_upload_token(
        provisioned.device.id, str(auth_ok["upload_token"])
    )
    assert registry.get(provisioned.device.id) is None


async def test_device_disabled_while_connected_is_closed_and_revoked(
    db, async_engine, home, settings
) -> None:
    """An authenticated socket must stop being usable after its device is disabled."""

    provisioned = await _provision(db, home, settings)
    device_id = provisioned.device.id
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    registry = DeviceConnectionRegistry()
    socket = ScriptedWebSocket(
        _hello(),
        secret=provisioned.secret,
        disconnect_after_auth=False,
    )
    handler = asyncio.create_task(handle_device_socket(socket, db, settings, registry))
    await socket.auth_ok_sent.wait()

    async with factory() as admin:
        device = await admin.get(Device, device_id)
        assert device is not None
        device.status = DeviceStatus.DISABLED
        await admin.commit()
    socket._inbound.put_nowait(
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "type": "device.heartbeat",
                    "boot_id": "boot-a",
                    "seq": 2,
                    "uptime_seconds": 5,
                }
            ),
        }
    )

    await handler

    assert socket.closed == [(4002, "AUTH_FAILED")]
    auth_ok = next(message for message in socket.sent if message["type"] == "auth.ok")
    assert not await registry.verify_upload_token(device_id, str(auth_ok["upload_token"]))


async def test_binary_handshake_frame_is_rejected(db, settings) -> None:
    """Binary frames before authentication must never enter JSON parsing."""

    socket = ScriptedWebSocket(None)
    socket._inbound.put_nowait({"type": "websocket.receive", "bytes": b"{}"})

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    assert socket.closed == [(4000, "PROTOCOL_ERROR")]


async def test_deeply_nested_handshake_json_is_a_protocol_error(db, settings) -> None:
    """Decoder recursion limits describe malformed input, not a server failure."""

    payload = "[" * 5_000 + "0" + "]" * 5_000
    socket = ScriptedWebSocket(payload)

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    assert socket.closed == [(4000, "PROTOCOL_ERROR")]


async def test_handshake_timeout_has_stable_close_code(db, settings, monkeypatch) -> None:
    """A silent peer must not hold a handshake session indefinitely."""

    monkeypatch.setattr("app.websocket.device.HANDSHAKE_TIMEOUT_SECONDS", 0.01)
    socket = ScriptedWebSocket(None)

    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())

    assert socket.closed == [(4006, "HANDSHAKE_TIMEOUT")]


async def _eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
async def voice(db, home, settings, async_engine):
    provisioned = await _provision(db, home, settings)
    db.add(AgentConfig(home_id=home.id, enabled=True, voice="marin", language="es-AR"))
    await db.commit()
    settings.openai_api_key = SecretStr("sk-test-socket")

    class ObservedRegistry(DeviceConnectionRegistry):
        def __init__(self):
            super().__init__()
            self.leases = []

        async def claim(self, *args, **kwargs):
            lease = await super().claim(*args, **kwargs)
            self.leases.append(lease)
            return lease

    registry = ObservedRegistry()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    provider = FakeRealtimeProvider()

    class ObservedCoordinator(ConversationCoordinator):
        def __init__(self):
            super().__init__(factory, provider, settings, registry)
            self.disconnected = []
            self.disconnect_calls = []

        async def disconnect(self, device_id, ownership):
            self.disconnect_calls.append(ownership)
            await super().disconnect(device_id, ownership)
            self.disconnected.append(ownership)

    coordinator = ObservedCoordinator()
    handlers = []

    async def connect(*, capable=True, socket_class=ScriptedWebSocket):
        hello = json.loads(_hello())
        if capable:
            hello["capabilities"] = ["audio_pcm16_v1"]
        socket = socket_class(
            json.dumps(hello), secret=provisioned.secret, disconnect_after_auth=False
        )

        async def run():
            async with factory() as session:
                await handle_device_socket(socket, session, settings, registry, coordinator)

        handlers.append(asyncio.create_task(run()))
        socket.handler = handlers[-1]
        await _eventually(lambda: socket.auth_ok_sent.is_set() or handlers[-1].done())
        if handlers[-1].done():
            handlers[-1].result()
        return socket

    yield connect, provider, coordinator, registry, provisioned.device.id
    for handler in handlers:
        handler.cancel()
    await asyncio.gather(*handlers, return_exceptions=True)
    await coordinator.shutdown()
    await registry.close_all()


def _start(seq=2):
    return {
        "type": "conversation.start",
        "version": 1,
        "seq": seq,
        "boot_id": "boot-a",
        "mode": "hands_free",
    }


def _stop(conversation_id, seq=3):
    return {
        "type": "conversation.stop",
        "version": 1,
        "seq": seq,
        "boot_id": "boot-a",
        "conversation_id": str(conversation_id),
        "reason": "visitor_finished",
    }


async def _started(socket):
    socket.feed(_start())
    await _eventually(lambda: any(item["type"] == "conversation.started" for item in socket.sent))
    return next(item for item in socket.sent if item["type"] == "conversation.started")


async def test_browser_output_overflow_closes_durably_once_and_preserves_socket_retry(voice, db):
    connect, provider, coordinator, registry, device_id = voice
    socket = await connect()
    started = await _started(socket)
    await provider.sessions[0].emit(ResponseStarted())
    await provider.sessions[0].emit(AudioDelta(b"a" * 960))
    await _eventually(lambda: bool(socket.binary_sent))
    overflow = {
        "type": "conversation.audio.output_overflow",
        "version": 1,
        "boot_id": "boot-a",
        "seq": 3,
        "conversation_id": started["conversation_id"],
        "stream_id": started["stream_id"],
    }
    socket.feed(overflow)
    await _eventually(
        lambda: socket.closed or any(m["type"] == "conversation.ended" for m in socket.sent)
    )
    assert not socket.closed
    await _eventually(lambda: not coordinator._active and not coordinator._tasks)
    assert not coordinator._io_tasks and not coordinator._control_tasks
    assert provider.sessions[0].cancel_count == 1
    terminal = [
        m
        for m in socket.sent
        if m["type"] in {"conversation.audio.clear", "conversation.error", "conversation.ended"}
    ]
    assert [m["type"] for m in terminal] == [
        "conversation.audio.clear",
        "conversation.error",
        "conversation.ended",
    ]
    assert terminal[0]["reason"] == "audio_output_overflow"
    assert terminal[1]["code"] == "AUDIO_OUTPUT_OVERFLOW"
    assert terminal[2]["outcome"] == "failed"
    assert terminal[2]["reason"] == "audio_output_overflow"
    row = await db.get(Conversation, UUID(started["conversation_id"]))
    assert row.status == ConversationStatus.CLOSED and row.outcome == ConversationOutcome.FAILED
    events = (
        await db.scalars(select(Event).where(Event.event_type == "conversation_failed"))
    ).all()
    assert [event.payload for event in events] == [
        {"conversation_id": started["conversation_id"], "code": "AUDIO_OUTPUT_OVERFLOW"}
    ]
    assert registry.get(device_id) is not None
    socket.feed(overflow | {"seq": 4})  # terminal duplicate cannot notify again
    socket.feed({"type": "device.heartbeat", "boot_id": "boot-a", "seq": 5, "uptime_seconds": 5})
    socket.feed(_start(seq=6))
    await _eventually(lambda: len(provider.sessions) == 2)
    await _eventually(
        lambda: len([m for m in socket.sent if m["type"] == "conversation.started"]) == 2
    )
    successor = [m for m in socket.sent if m["type"] == "conversation.started"][-1]
    assert successor["conversation_id"] != started["conversation_id"]
    socket.feed(overflow | {"seq": 7})  # old identity cannot close the successor
    socket.feed(_stop(successor["conversation_id"], seq=8))
    await _eventually(
        lambda: len([m for m in socket.sent if m["type"] == "conversation.ended"]) == 2
    )
    assert not socket.closed
    assert len([m for m in socket.sent if m.get("code") == "AUDIO_OUTPUT_OVERFLOW"]) == 1
    assert len([m for m in socket.sent if m["type"] == "conversation.audio.clear"]) == 1
    successor_row = await db.get(Conversation, UUID(successor["conversation_id"]))
    assert successor_row.outcome == ConversationOutcome.REGISTERED


@pytest.mark.parametrize("changed", ["conversation_id", "stream_id"])
async def test_browser_overflow_wrong_identity_cannot_close_owned_visit(voice, changed):
    connect, _, coordinator, registry, device_id = voice
    socket = await connect()
    started = await _started(socket)
    socket.feed(
        {
            "type": "conversation.audio.output_overflow",
            "version": 1,
            "seq": 3,
            "boot_id": "boot-a",
            "conversation_id": started["conversation_id"],
            "stream_id": started["stream_id"],
            changed: str(uuid4()),
        }
    )
    await _eventually(
        lambda: socket.closed or any(m["type"] == "conversation.error" for m in socket.sent)
    )
    assert not socket.closed
    assert coordinator._active[device_id].conversation_id == UUID(started["conversation_id"])
    assert registry.get(device_id) is not None
    assert not any(m["type"] == "conversation.ended" for m in socket.sent)


@pytest.mark.parametrize("operation", ["rotate", "rotate-secret", "disable"])
async def test_admin_access_revocation_closes_voice_without_device_cooperation(
    voice, client, db, home, admin, roles, operation
):
    """A successful admin commit must stop active binary audio and durable voice immediately."""
    connect, provider, coordinator, registry, device_id = voice
    client._transport.app.state.device_connections = registry
    permission = Permission(name="devices.manage")
    db.add_all([permission, HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id)])
    await db.flush()
    db.add(RolePermission(role_id=roles["owner"].id, permission_id=permission.id))
    await db.commit()
    login = await client.post(
        "/api/auth/login", json={"identifier": admin.username, "password": "ValidPass!42"}
    )
    assert login.status_code == 204
    socket = await connect()
    started = await _started(socket)
    socket.feed(_audio(started["stream_id"]))
    await _eventually(lambda: provider.sessions[0].received_audio)
    lease = registry.leases[-1]
    response = await client.post(f"/api/homes/{home.id}/devices/{device_id}/{operation}")
    assert response.status_code == 200
    assert not await registry.is_current(lease)
    assert socket.closed == [(4002, "AUTH_FAILED")]
    row = await db.get(Conversation, UUID(started["conversation_id"]))
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.ABANDONED
    socket.feed(_audio(started["stream_id"], seq=2))
    socket.feed({"type": "device.heartbeat", "boot_id": "boot-a", "seq": 3, "uptime_seconds": 42})
    await asyncio.wait_for(socket.handler, timeout=2)
    await db.refresh(row)
    device = await db.get(Device, device_id, populate_existing=True)
    assert device.status == (
        DeviceStatus.DISABLED if operation == "disable" else DeviceStatus.PROVISIONING
    )
    assert provider.sessions[0].received_audio == [b"\x01\x02" * 480]
    assert coordinator.disconnect_calls == [lease]


async def test_rotation_after_hmac_before_claim_cannot_authenticate_old_secret(
    db, home, settings, async_engine, monkeypatch
):
    """Successful HMAC verification is invalid once its credential is committed revoked."""
    provisioned = await _provision(db, home, settings)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    registry = DeviceConnectionRegistry()
    from app.websocket import device as endpoint

    original_verify = endpoint.verify_challenge

    async def rotate_after_verify(*args, **kwargs):
        result = await original_verify(*args, **kwargs)
        async with factory() as session:
            device = await session.get(Device, provisioned.device.id)
            await rotate_device_secret(session, device, settings, registry=registry)
            await session.commit()
        return result

    monkeypatch.setattr(endpoint, "verify_challenge", rotate_after_verify)
    socket = ScriptedWebSocket(_hello(), secret=provisioned.secret)
    await handle_device_socket(socket, db, settings, registry)
    assert socket.closed == [(4002, "AUTH_FAILED")]
    assert not any(message["type"] == "auth.ok" for message in socket.sent)
    device = await db.get(Device, provisioned.device.id, populate_existing=True)
    assert device.status == DeviceStatus.PROVISIONING


@pytest.mark.parametrize("operation", ["rotate", "disable"])
async def test_revocation_cancels_inflight_input_and_discards_queued_audio(
    voice, db, settings, operation
):
    """Retirement must discard admitted input instead of draining it into the provider."""
    from app.devices.credentials import set_device_enabled

    connect, provider, _, registry, device_id = voice
    socket = await connect()
    started = await _started(socket)
    session = provider.sessions[0]
    entered, release = asyncio.Event(), asyncio.Event()
    real_send = session.send_audio

    async def held_audio(pcm):
        entered.set()
        await release.wait()
        await real_send(pcm)

    session.send_audio = held_audio
    socket.feed(_audio(started["stream_id"]))
    socket.feed(_audio(started["stream_id"], seq=2))
    await asyncio.wait_for(entered.wait(), timeout=2)
    device = await db.get(Device, device_id)
    if operation == "rotate":
        await rotate_device_secret(db, device, settings, registry=registry)
    else:
        await set_device_enabled(db, device, enabled=False, registry=registry)
    await db.commit()
    await registry.wait_revocations()
    release.set()
    await asyncio.wait_for(socket.handler, timeout=2)
    assert session.received_audio == []
    row = await db.get(Conversation, UUID(started["conversation_id"]))
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.ABANDONED


async def test_revocation_during_output_send_closes_once_with_public_auth_reason(
    voice, db, settings
):
    """A retired binary writer cannot win the close race with an internal-error reason."""
    connect, provider, _, registry, device_id = voice
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldOutputSocket(ScriptedWebSocket):
        async def send_bytes(self, payload):
            entered.set()
            await release.wait()
            await super().send_bytes(payload)

    socket = await connect(socket_class=HeldOutputSocket)
    started = await _started(socket)
    await provider.sessions[0].emit(ResponseStarted())
    await provider.sessions[0].emit(AudioDelta(b"\x01\x02" * 480))
    await asyncio.wait_for(entered.wait(), timeout=2)
    device = await db.get(Device, device_id)
    await rotate_device_secret(db, device, settings, registry=registry)
    await db.commit()
    await registry.wait_revocations()
    release.set()
    await asyncio.wait_for(socket.handler, timeout=2)
    assert socket.closed == [(4002, "AUTH_FAILED")]
    assert socket.binary_sent == []
    row = await db.get(Conversation, UUID(started["conversation_id"]))
    assert row.status == ConversationStatus.CLOSED
    assert row.outcome == ConversationOutcome.ABANDONED


async def test_revocation_while_provider_opens_closes_partial_session_without_durable_row(
    voice, db, settings
):
    """Revocation during external opening cannot create a durable or resurrected visit."""
    connect, provider, coordinator, registry, device_id = voice
    entered, release = asyncio.Event(), asyncio.Event()
    real_open = provider.open_session

    async def held_open(config):
        entered.set()
        await release.wait()
        return await real_open(config)

    provider.open_session = held_open
    socket = await connect()
    socket.feed(_start())
    await asyncio.wait_for(entered.wait(), timeout=2)
    device = await db.get(Device, device_id)
    await rotate_device_secret(db, device, settings, registry=registry)
    await db.commit()
    assert not await registry.is_current(registry.leases[-1])
    await _eventually(lambda: socket.closed)
    release.set()
    await registry.wait_revocations()
    await asyncio.wait_for(socket.handler, timeout=2)
    assert (await db.scalars(select(Conversation))).all() == []
    assert not any(item["type"] == "conversation.started" for item in socket.sent)
    with pytest.raises(RuntimeError, match="closed"):
        await provider.sessions[0].send_audio(b"\x00\x00")
    assert coordinator.disconnect_calls == registry.leases


@pytest.mark.parametrize("operation", ["rotate", "disable"])
async def test_old_heartbeat_waiting_for_row_lock_cannot_restore_online_after_rotation(
    voice, db, settings, monkeypatch, operation
):
    """A heartbeat that passed receive-time ownership must recheck after its durable lock."""
    from app.devices import presence
    from app.devices.credentials import set_device_enabled

    connect, _, _, registry, device_id = voice
    socket = await connect()
    entered, release = asyncio.Event(), asyncio.Event()
    original_lock = presence._locked_device

    async def held_lock(session, device):
        if asyncio.current_task() is socket.handler:
            entered.set()
            await release.wait()
        return await original_lock(session, device)

    monkeypatch.setattr(presence, "_locked_device", held_lock)
    socket.feed({"type": "device.heartbeat", "boot_id": "boot-a", "seq": 2, "uptime_seconds": 42})
    await asyncio.wait_for(entered.wait(), timeout=2)
    device = await db.get(Device, device_id)
    if operation == "rotate":
        await rotate_device_secret(db, device, settings, registry=registry)
    else:
        await set_device_enabled(db, device, enabled=False, registry=registry)
    await db.commit()
    await registry.wait_revocations()
    release.set()
    await asyncio.wait_for(socket.handler, timeout=2)
    await db.refresh(device)
    assert device.status == (
        DeviceStatus.PROVISIONING if operation == "rotate" else DeviceStatus.DISABLED
    )
    assert socket.closed == [(4002, "AUTH_FAILED")]


async def test_revocation_at_final_auth_boundary_closes_exact_lease_once(
    db, home, settings, async_engine, monkeypatch
):
    """The auth handler must join an already-revoked lease's close instead of closing twice."""
    from app.websocket import device as endpoint

    provisioned = await _provision(db, home, settings)
    device_id = provisioned.device.id
    registry = DeviceConnectionRegistry()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()
    original_stage = endpoint.stage_online_for_auth

    async def held_stage(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_stage(*args, **kwargs)

    monkeypatch.setattr(endpoint, "stage_online_for_auth", held_stage)
    socket = ScriptedWebSocket(_hello(), secret=provisioned.secret)

    async def run():
        async with factory() as session:
            await handle_device_socket(socket, session, settings, registry)

    handler = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), timeout=2)
    await rotate_device_secret(db, provisioned.device, settings, registry=registry)
    await db.commit()
    await registry.wait_revocations()
    release.set()
    await asyncio.wait_for(handler, timeout=2)
    assert socket.closed == [(4002, "AUTH_FAILED")]
    assert not any(item["type"] == "auth.ok" for item in socket.sent)
    device = await db.get(Device, device_id, populate_existing=True)
    assert device.status == DeviceStatus.PROVISIONING


async def test_revocation_cleanup_cannot_close_newly_authenticated_successor(voice, db, settings):
    """Delayed old socket cleanup cannot retire a successor authenticated with the new secret."""
    connect, _, coordinator, registry, device_id = voice
    close_entered, release_close = asyncio.Event(), asyncio.Event()

    class HeldCloseSocket(ScriptedWebSocket):
        async def close(self, code=1000, reason=None):
            close_entered.set()
            await release_close.wait()
            await super().close(code=code, reason=reason)

    old = await connect(socket_class=HeldCloseSocket)
    old_started = await _started(old)
    old_lease = registry.leases[-1]
    device = await db.get(Device, device_id)
    rotated = await rotate_device_secret(db, device, settings, registry=registry)
    await db.commit()
    await asyncio.wait_for(close_entered.wait(), timeout=2)
    await _eventually(lambda: coordinator.disconnected)

    class SuccessorSocket(ScriptedWebSocket):
        def __init__(self, *args, **kwargs):
            kwargs["secret"] = rotated.secret
            super().__init__(*args, **kwargs)

    try:
        successor = await connect(socket_class=SuccessorSocket)
        started = await _started(successor)
    finally:
        release_close.set()
    await registry.wait_revocations()
    await asyncio.wait_for(old.handler, timeout=2)
    await registry.unregister(old_lease)
    assert successor.closed == []
    assert await registry.is_current(registry.leases[-1])
    assert started["conversation_id"] != old_started["conversation_id"]
    old_row = await db.get(Conversation, UUID(old_started["conversation_id"]))
    new_row = await db.get(Conversation, UUID(started["conversation_id"]))
    assert old_row.status == ConversationStatus.CLOSED
    assert new_row.status == ConversationStatus.OPEN
    assert coordinator.disconnect_calls == [old_lease]


@pytest.mark.parametrize("operation", ["rotate", "disable"])
async def test_admin_revocation_requires_permission_and_own_home(
    voice, client, db, home, admin, roles, operation
):
    """Authorization must fail before it can invalidate another household's active voice."""
    from app.models import Home

    connect, _, _, registry, device_id = voice
    client._transport.app.state.device_connections = registry
    permission = Permission(name="devices.read")
    other_home = Home(name="Otro hogar")
    db.add_all(
        [
            permission,
            other_home,
            HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id),
        ]
    )
    await db.flush()
    db.add(RolePermission(role_id=roles["owner"].id, permission_id=permission.id))
    await db.commit()
    await client.post(
        "/api/auth/login", json={"identifier": admin.username, "password": "ValidPass!42"}
    )
    socket = await connect()
    await _started(socket)
    denied = await client.post(f"/api/homes/{home.id}/devices/{device_id}/{operation}")
    assert denied.status_code == 403
    permission.name = "devices.manage"
    db.add(HomeUser(home_id=other_home.id, user_id=admin.id, role_id=roles["owner"].id))
    await db.commit()
    wrong_home = await client.post(f"/api/homes/{other_home.id}/devices/{device_id}/{operation}")
    assert wrong_home.status_code == 404
    assert await registry.is_current(registry.leases[-1])
    assert socket.closed == []


def _audio(stream_id, seq=1, payload=b"\x01\x02" * 480):
    return encode_audio_frame(
        AudioFrame(UUID(str(stream_id)), seq, AudioDirection.DEVICE_TO_SERVER, payload)
    )


async def test_binary_second_handshake_read_still_closes_4000(db, home, settings):
    provisioned = await _provision(db, home, settings)
    socket = ScriptedWebSocket(_hello(), secret=provisioned.secret, binary_auth=True)
    await handle_device_socket(socket, db, settings, DeviceConnectionRegistry())
    assert socket.closed == [(4000, "PROTOCOL_ERROR")]
    assert [item["type"] for item in socket.sent] == ["auth.challenge"]


async def test_realtime_start_audio_output_and_stop_persist_on_same_authenticated_socket(voice, db):
    connect, provider, _, _, _ = voice
    socket = await connect()
    started = await _started(socket)
    socket.feed(_audio(started["stream_id"]))
    await _eventually(lambda: provider.sessions[0].received_audio)
    assert provider.sessions[0].received_audio == [b"\x01\x02" * 480]
    await provider.sessions[0].emit(ResponseStarted())
    await provider.sessions[0].emit(AudioDelta(b"\x03\x04" * 480))
    await _eventually(lambda: socket.binary_sent)
    decoded = decode_audio_frame(
        socket.binary_sent[0], expected_direction=AudioDirection.SERVER_TO_DEVICE
    )
    assert decoded.stream_id == UUID(started["stream_id"])
    assert decoded.seq == 1 and decoded.payload == b"\x03\x04" * 480
    socket.feed(_stop(started["conversation_id"]))
    await _eventually(lambda: any(item["type"] == "conversation.ended" for item in socket.sent))
    stored = await db.get(Conversation, UUID(started["conversation_id"]))
    assert stored.status == ConversationStatus.CLOSED
    assert stored.outcome == ConversationOutcome.REGISTERED
    controls = [item for item in socket.sent if str(item["type"]).startswith("conversation.")]
    assert [item["seq"] for item in controls] == list(range(len(controls)))
    assert all(item["version"] == 1 and "boot_id" not in item for item in controls)
    assert socket.closed == []


@pytest.mark.parametrize(
    "incoming",
    [
        b"PAUD",
        _start(),
        {
            "type": "conversation.audio.output_overflow",
            "version": 1,
            "seq": 2,
            "boot_id": "boot-a",
            "conversation_id": str(uuid4()),
            "stream_id": str(uuid4()),
        },
    ],
)
async def test_missing_audio_capability_returns_public_error_and_legacy_heartbeat_survives(
    voice, db, incoming
):
    connect, provider, _, registry, device_id = voice
    socket = await connect(capable=False)
    socket.feed(incoming)
    await _eventually(lambda: any(item["type"] == "conversation.error" for item in socket.sent))
    error = socket.sent[-1]
    assert error["code"] == "INVALID_AUDIO_FRAME"
    assert set(error) == {"type", "version", "seq", "code", "correlation_id"}
    socket.feed({"type": "device.heartbeat", "boot_id": "boot-a", "seq": 3, "uptime_seconds": 7})
    await asyncio.sleep(0.02)
    assert provider.sessions == []
    assert (await db.scalars(select(Conversation))).all() == []
    assert registry.get(device_id) is socket and not socket.closed


@pytest.mark.parametrize(
    "bad_kind,code",
    [
        ("stream", "STREAM_NOT_OWNED"),
        ("duplicate", "INVALID_SEQUENCE"),
        ("decreasing", "INVALID_SEQUENCE"),
        ("stop_id", "CONVERSATION_NOT_ACTIVE"),
    ],
)
async def test_rejected_stream_sequence_or_stop_does_not_end_owned_conversation(
    voice, bad_kind, code
):
    connect, provider, _, _, _ = voice
    socket = await connect()
    started = await _started(socket)
    socket.feed(_audio(started["stream_id"], seq=2))
    await _eventually(lambda: provider.sessions[0].received_audio)
    if bad_kind == "stream":
        socket.feed(_audio(uuid4(), seq=3))
    elif bad_kind == "stop_id":
        socket.feed(_stop(uuid4()))
    else:
        socket.feed(_audio(started["stream_id"], seq=2 if bad_kind == "duplicate" else 1))
    await _eventually(lambda: any(item["type"] == "conversation.error" for item in socket.sent))
    assert socket.sent[-1]["code"] == code
    socket.feed(_audio(started["stream_id"], seq=3))
    await _eventually(lambda: len(provider.sessions[0].received_audio) == 2)
    assert not any(item["type"] == "conversation.ended" for item in socket.sent)
    assert not socket.closed


@pytest.mark.parametrize("bad_audio", [b"private-invalid-frame", b"PAUD" + b"x" * 2000])
async def test_malformed_audio_fails_only_started_conversation(voice, db, bad_audio):
    connect, _, _, registry, device_id = voice
    socket = await connect()
    started = await _started(socket)
    socket.feed(bad_audio)
    await _eventually(lambda: any(item["type"] == "conversation.ended" for item in socket.sent))
    error = next(item for item in socket.sent if item["type"] == "conversation.error")
    assert error["code"] == "INVALID_AUDIO_FRAME"
    assert "private" not in json.dumps(socket.sent)
    stored = await db.get(Conversation, UUID(started["conversation_id"]))
    assert stored.outcome == ConversationOutcome.FAILED
    assert registry.get(device_id) is socket and not socket.closed


async def test_replacement_and_disconnect_clean_correct_lease_exactly_once(voice, db):
    connect, provider, coordinator, registry, device_id = voice
    old = await connect()
    first = await _started(old)
    new = await connect()
    await _eventually(lambda: len(coordinator.disconnected) == 1)
    assert coordinator.disconnected[0].socket is old
    assert old.closed == [(4001, "replaced")]
    assert registry.get(device_id) is new
    second = await _started(new)
    old.feed(_audio(first["stream_id"]))
    new._inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
    await _eventually(lambda: len(coordinator.disconnected) == 2)
    assert coordinator.disconnected[1].socket is new
    await _eventually(lambda: registry.get(device_id) is None)
    for identifier in [first["conversation_id"], second["conversation_id"]]:
        stored = await db.get(Conversation, UUID(identifier))
        assert stored.status == ConversationStatus.CLOSED
        assert stored.outcome == ConversationOutcome.ABANDONED
    assert len(provider.sessions) == 2


async def test_application_composes_real_provider_and_cleans_sockets_after_safe_shutdown_failure(
    settings,
):
    from app.ai.conversation_coordinator import ConversationShutdownError
    from app.ai.openai_realtime import OpenAIRealtimeProvider
    from app.main import create_app

    application = create_app(settings)
    assert isinstance(application.state.ai_provider, OpenAIRealtimeProvider)
    assert isinstance(application.state.conversation_coordinator, ConversationCoordinator)
    registry = application.state.device_connections
    socket = ScriptedWebSocket(None)
    lease = await registry.claim(uuid4(), socket)

    async def fail_shutdown():
        assert await registry.is_current(lease)
        raise ConversationShutdownError()

    application.state.conversation_coordinator.shutdown = fail_shutdown
    pool = application.state.engine.pool
    with pytest.raises(ConversationShutdownError, match="^AI_UNAVAILABLE$"):
        async with application.router.lifespan_context(application):
            pass
    assert not await registry.is_current(lease)
    assert socket.closed == [(1001, "shutdown")]
    assert application.state.engine.pool is not pool


async def test_binary_without_capability_fails_inconsistent_started_conversation_only(voice, db):
    from app.devices.connections import DeviceConversationTransport

    connect, provider, coordinator, registry, device_id = voice
    socket = await connect(capable=False)
    lease = registry.leases[-1]
    transport = DeviceConversationTransport(registry, lease)
    await coordinator.start(device_id, lease, transport)
    identifier = transport.conversation_id
    assert identifier is not None
    socket.feed(_audio(transport.stream_id))
    await _eventually(lambda: any(item["type"] == "conversation.ended" for item in socket.sent))
    stored = await db.get(Conversation, identifier)
    assert stored.status == ConversationStatus.CLOSED
    assert stored.outcome == ConversationOutcome.FAILED
    assert provider.sessions[0].received_audio == []
    assert registry.get(device_id) is socket and not socket.closed
    errors = [item for item in socket.sent if item["type"] == "conversation.error"]
    assert len(errors) == 1 and errors[0]["code"] == "INVALID_AUDIO_FRAME"


async def test_reject_audio_on_retired_lease_cannot_fail_successor_conversation(voice, db):
    connect, _, coordinator, registry, device_id = voice
    old = await connect()
    await _started(old)
    retired = registry.leases[-1]
    new = await connect()
    started = await _started(new)
    assert not await coordinator.reject_audio(device_id, retired)
    stored = await db.get(Conversation, UUID(started["conversation_id"]))
    assert stored.status == ConversationStatus.OPEN
    assert not any(item["type"] == "conversation.error" for item in new.sent)


async def test_real_asgi_route_uses_application_coordinator_and_registry(db, home, settings):
    from app.main import create_app

    provisioned = await _provision(db, home, settings)
    db.add(AgentConfig(home_id=home.id, enabled=True, voice="marin", language="es-AR"))
    await db.commit()
    settings.openai_api_key = SecretStr("sk-test-asgi")
    application = create_app(settings)
    registry = DeviceConnectionRegistry()
    provider = FakeRealtimeProvider()
    coordinator = ConversationCoordinator(
        application.state.session_factory, provider, settings, registry
    )
    application.state.device_connections = registry
    application.state.conversation_coordinator = coordinator
    socket = ScriptedWebSocket(None, secret=provisioned.secret, disconnect_after_auth=False)
    socket._inbound.put_nowait({"type": "websocket.connect"})
    hello = json.loads(_hello())
    hello["capabilities"] = ["audio_pcm16_v1"]
    socket.feed(hello)

    async def send(message):
        if message["type"] == "websocket.accept":
            await socket.accept()
        elif message["type"] == "websocket.close":
            await socket.close(message["code"], message.get("reason"))
        elif message.get("text") is not None:
            await socket.send_json(json.loads(message["text"]))
        else:
            await socket.send_bytes(message["bytes"])

    scope = {
        "type": "websocket",
        "path": "/ws/device",
        "raw_path": b"/ws/device",
        "root_path": "",
        "scheme": "ws",
        "http_version": "1.1",
        "headers": [],
        "query_string": b"",
        "client": ("203.0.113.44", 4567),
        "server": ("test", 80),
        "subprotocols": [],
        "state": {},
    }
    handler = asyncio.create_task(application(scope, socket.receive, send))
    try:
        await _eventually(lambda: socket.auth_ok_sent.is_set())
        started = await _started(socket)
        socket.feed(_audio(started["stream_id"]))
        await _eventually(lambda: provider.sessions[0].received_audio)
        assert registry.connection_count == 1
        socket.feed(_stop(started["conversation_id"]))
        await _eventually(lambda: any(item["type"] == "conversation.ended" for item in socket.sent))
    finally:
        socket._inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(handler, 2)
        await coordinator.shutdown()
        await registry.close_all()
        await application.state.engine.dispose()
    assert provider.sessions[0].received_audio == [b"\x01\x02" * 480]


async def test_handler_cancel_during_provider_open_cleans_lease_once_and_cannot_publish(voice, db):
    connect, provider, coordinator, registry, device_id = voice
    socket = await connect()
    entered, release = asyncio.Event(), asyncio.Event()
    original = provider.open_session

    async def blocked_open(config):
        entered.set()
        await release.wait()
        return await original(config)

    provider.open_session = blocked_open
    socket.feed(_start())
    await asyncio.wait_for(entered.wait(), 2)
    socket.handler.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(socket.handler, 2)
    assert len(coordinator.disconnected) == 1
    assert coordinator.disconnected[0].socket is socket
    assert registry.get(device_id) is None
    assert not any(item["type"] == "conversation.started" for item in socket.sent)
    assert (await db.scalars(select(Conversation))).all() == []


@pytest.mark.parametrize("blocked_kind", ["control", "audio"])
async def test_coordinator_timeout_closes_real_transport_before_slow_child_cleanup(
    voice, db, monkeypatch, blocked_kind
):
    connect, provider, coordinator, registry, device_id = voice
    entered, cancelled, release, exited = (asyncio.Event() for _ in range(4))
    delivered_late = []

    class SlowSocket(ScriptedWebSocket):
        async def block(self, payload):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                # Deliberately ignore repeated cancellation while cleanup is gated.
                while not release.is_set():
                    with suppress(asyncio.CancelledError):
                        await release.wait()
                exited.set()
                if not self.closed:
                    delivered_late.append(payload)
                raise

        async def send_json(self, payload):
            if blocked_kind == "control" and payload["type"] == "conversation.started":
                await self.block(payload)
            await super().send_json(payload)

        async def send_bytes(self, payload):
            if blocked_kind == "audio":
                await self.block(payload)
            await super().send_bytes(payload)

    monkeypatch.setattr("app.ai.conversation_coordinator._CONTROL_SEND_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr("app.ai.conversation_coordinator._AUDIO_SEND_TIMEOUT_SECONDS", 0.02)
    socket = await connect(socket_class=SlowSocket)
    if blocked_kind == "control":
        socket.feed(_start())
    else:
        await _started(socket)
        await provider.sessions[0].emit(ResponseStarted())
        await provider.sessions[0].emit(AudioDelta(b"\x03\x04" * 480))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(cancelled.wait(), 2)
        await asyncio.wait_for(socket.handler, 2)
        assert socket.closed
        assert registry.get(device_id) is None
        assert not exited.is_set(), "durable cleanup must not await canceled socket children"
        rows = (await db.scalars(select(Conversation))).all()
        assert len(rows) == 1 and rows[0].status == ConversationStatus.CLOSED
        assert rows[0].outcome == ConversationOutcome.FAILED
        assert len(coordinator.disconnected) == 1
        assert not delivered_late and not socket.binary_sent
    finally:
        release.set()
        await asyncio.wait_for(exited.wait(), 2)
    assert delivered_late == []


@pytest.mark.parametrize("event_kind", ["state", "audio", "both"])
async def test_retired_provider_workers_share_disconnect_notification_with_replacement(
    voice, db, event_kind
):
    connect, provider, coordinator, registry, device_id = voice
    old = await connect()
    started = await _started(old)
    await provider.sessions[0].emit(ResponseStarted())
    await _eventually(lambda: any(item.get("state") == "assistant_speaking" for item in old.sent))
    replacement = ScriptedWebSocket(None)
    successor = await registry.claim(device_id, replacement)
    if event_kind in {"audio", "both"}:
        await provider.sessions[0].emit(AudioDelta(b"\x03\x04" * 480))
    if event_kind in {"state", "both"}:
        await provider.sessions[0].emit(SpeechStopped())
    await _eventually(lambda: coordinator.disconnect_calls)
    await asyncio.wait_for(registry.close_replaced(successor), 2)
    await asyncio.wait_for(old.handler, 2)
    assert len(coordinator.disconnect_calls) == 1
    assert coordinator.disconnect_calls[0].socket is old
    assert registry.get(device_id) is replacement
    assert not replacement.closed and not old.binary_sent
    stored = await db.get(Conversation, UUID(started["conversation_id"]))
    assert stored.status == ConversationStatus.CLOSED
    assert stored.outcome == ConversationOutcome.ABANDONED
    await registry.unregister(successor)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("total_bytes", [995, 4 * 1024 * 1024])
async def test_oversized_audio_rejected_before_decoder_or_payload_copy(
    voice, db, monkeypatch, active, total_bytes
):
    import app.websocket.device as device_route

    connect, provider, _, registry, device_id = voice
    socket = await connect()
    started = await _started(socket) if active else None
    decoded_sizes = []
    original = device_route.decode_audio_frame

    def observed_decode(data, **kwargs):
        decoded_sizes.append(len(data))
        return original(data, **kwargs)

    class NoSliceBytes(bytes):
        def __getitem__(self, index):
            if isinstance(index, slice):
                raise AssertionError("oversized input must not be sliced")
            return super().__getitem__(index)

    monkeypatch.setattr(device_route, "decode_audio_frame", observed_decode)
    socket.feed(NoSliceBytes(b"PAUD" + bytes(total_bytes - 4)))
    await _eventually(
        lambda: socket.closed or any(item["type"] == "conversation.error" for item in socket.sent)
    )
    assert decoded_sizes == [], "size admission must precede the codec"
    assert not socket.closed and registry.get(device_id) is socket
    error = next(item for item in socket.sent if item["type"] == "conversation.error")
    assert error["code"] == "INVALID_AUDIO_FRAME"
    if started is not None:
        await _eventually(lambda: any(item["type"] == "conversation.ended" for item in socket.sent))
        stored = await db.get(Conversation, UUID(started["conversation_id"]))
        assert stored.outcome == ConversationOutcome.FAILED
        assert not provider.sessions[0].received_audio


@pytest.mark.parametrize("stage", ["start", "loop"])
async def test_handler_cancellation_closes_owned_socket_before_disconnect_and_unregister(
    voice, stage
):
    connect, provider, coordinator, registry, _ = voice
    order = []
    open_entered, open_release = asyncio.Event(), asyncio.Event()

    class OrderedSocket(ScriptedWebSocket):
        async def close(self, code=1000, reason=None):
            order.append("close")
            await super().close(code, reason)

    socket = await connect(socket_class=OrderedSocket)
    original_disconnect = coordinator.disconnect
    original_unregister = registry.unregister

    async def disconnect(*args):
        order.append("disconnect")
        await original_disconnect(*args)

    async def unregister(*args, **kwargs):
        order.append("unregister")
        return await original_unregister(*args, **kwargs)

    coordinator.disconnect = disconnect
    registry.unregister = unregister
    original_open = provider.open_session

    async def blocked_open(config):
        open_entered.set()
        await open_release.wait()
        return await original_open(config)

    if stage == "start":
        provider.open_session = blocked_open
        socket.feed(_start())
        await asyncio.wait_for(open_entered.wait(), 2)
    try:
        socket.handler.cancel()
        await _eventually(lambda: order)
        assert order[0] == "close"
    finally:
        open_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(socket.handler, 2)
    assert order == ["close", "unregister", "disconnect"]
    assert len(coordinator.disconnect_calls) == 1


@pytest.mark.parametrize("close_times_out", [False, True])
async def test_repeated_handler_cancellation_joins_bounded_socket_close_before_cleanup(
    voice, monkeypatch, close_times_out
):
    connect, _, coordinator, registry, device_id = voice
    order = []
    entered, release = asyncio.Event(), asyncio.Event()

    class GatedCloseSocket(ScriptedWebSocket):
        async def close(self, code=1000, reason=None):
            self.close_task = asyncio.current_task()
            order.append("close")
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Socket transport may have additional local cleanup after its deadline.
                await release.wait()
            await super().close(code, reason)

    socket = await connect(socket_class=GatedCloseSocket)
    if close_times_out:
        monkeypatch.setattr(registry, "_close_timeout_seconds", 0.03)
    original_disconnect = coordinator.disconnect

    async def disconnect(*args):
        order.append("disconnect")
        await original_disconnect(*args)

    coordinator.disconnect = disconnect
    socket.handler.cancel()
    await asyncio.wait_for(entered.wait(), 1)
    socket.handler.cancel()
    await asyncio.sleep(0)
    try:
        assert order == ["close"]
        assert registry.get(device_id) is None
        if not close_times_out:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(socket.handler, 0.3)
        assert order == ["close", "disconnect"]
        assert len(coordinator.disconnect_calls) == 1
    finally:
        release.set()
        await asyncio.wait_for(socket.close_task, 1)
