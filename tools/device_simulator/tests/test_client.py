import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from portero_simulator.cli import configure_logging, settings_from_args
from portero_simulator.client import DeviceSimulator
from portero_simulator.config import SimulatorSettings

COMMAND_ID = "3029e584-27ad-4b15-bae6-b470b6ea14ac"
CHALLENGE = {
    "type": "auth.challenge",
    "algorithm": "HMAC-SHA256",
    "nonce": "nonce-456-nonce-456-nonce-456-12",
    "expires_at": "2026-09-05T12:00:00+00:00",
}
AUTH_OK = {
    "type": "auth.ok",
    "upload_token": "upload-token",
    "upload_expires_at": "2099-09-05T12:05:00+00:00",
    "heartbeat_interval_seconds": 15,
}


class ScriptedSocket:
    def __init__(
        self,
        commands: list[dict[str, object]] | None = None,
        *,
        command_gate: asyncio.Event | None = None,
    ) -> None:
        self.sent: list[dict[str, object]] = []
        self._handshake = iter((CHALLENGE, AUTH_OK))
        command_list = commands or []
        self._commands = iter(command_list)
        self._pending_commands = {str(command["command_id"]) for command in command_list}
        self._commands_complete = asyncio.Event()
        if not self._pending_commands:
            self._commands_complete.set()
        self._command_gate = command_gate

    async def send(self, payload: str) -> None:
        decoded = json.loads(payload)
        assert isinstance(decoded, dict)
        self.sent.append(decoded)
        if decoded.get("type") == "command.result" or (
            decoded.get("type") == "command.ack" and decoded.get("status") == "rejected"
        ):
            self._pending_commands.discard(str(decoded["command_id"]))
            if not self._pending_commands:
                self._commands_complete.set()

    async def recv(self) -> str:
        return json.dumps(next(self._handshake))

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self._command_gate is not None:
            await self._command_gate.wait()
        try:
            return json.dumps(next(self._commands))
        except StopIteration as error:
            await self._commands_complete.wait()
            raise StopAsyncIteration from error


def settings(tmp_path: Path, **overrides: Any) -> SimulatorSettings:
    values: dict[str, object] = {
        "backend_url": "http://backend:8000/",
        "device_id": "PI-000001",
        "device_secret": SecretStr("secret"),
        "image": tmp_path / "capture.jpg",
        "firmware_version": "1.0.0-sim",
        "hardware_model": "SIMULATOR-V1",
        "ethernet": "online",
        "camera": "ready",
        "microphone": "unavailable",
        "speaker": "unavailable",
        "free_heap_bytes": 262_144,
        "reconnect_jitter": False,
    }
    values.update(overrides)
    return SimulatorSettings(_env_file=None, **values)


async def never_sleep(_seconds: float) -> None:
    await asyncio.Event().wait()


def test_settings_derive_ws_url_and_mask_secret(tmp_path: Path) -> None:
    """A configured HTTP origin must map to the device route without exposing credentials."""

    configured = settings(tmp_path)

    assert configured.websocket_url == "ws://backend:8000/ws/device"
    assert "device_secret=SecretStr('**********')" in repr(configured)
    assert "SecretStr('secret')" not in repr(configured)


def test_settings_reject_invalid_device_id(tmp_path: Path) -> None:
    """An identifier the backend schema rejects must be rejected before connecting."""

    with pytest.raises(ValidationError):
        settings(tmp_path, device_id="doorbell-1")


async def test_authentication_sends_exact_monotonic_frames_without_logging_secret(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Handshake drift, duplicate sequence values, or secret logging must fail this test."""

    socket = ScriptedSocket()
    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=never_sleep,
    )
    with caplog.at_level(logging.DEBUG):
        await simulator.run_connection(socket)

    assert socket.sent == [
        {
            "type": "device.hello",
            "boot_id": "boot-123",
            "seq": 0,
            "version": "v1",
            "device_id": "PI-000001",
        },
        {
            "type": "auth.response",
            "boot_id": "boot-123",
            "seq": 1,
            "nonce": CHALLENGE["nonce"],
            "digest": "4053652584a6c7834baa073c197c900a5177b59548491ef2294a0405dea399a0",
        },
        {
            "type": "device.status",
            "boot_id": "boot-123",
            "seq": 2,
            "firmware_version": "1.0.0-sim",
            "hardware_model": "SIMULATOR-V1",
            "uptime_seconds": 0,
            "ethernet": "online",
            "camera": "ready",
            "microphone": "unavailable",
            "speaker": "unavailable",
            "free_heap_bytes": 262_144,
        },
    ]
    assert "secret" not in caplog.text
    assert "upload-token" not in caplog.text


async def test_heartbeat_uses_server_interval_and_next_sequence(tmp_path: Path) -> None:
    """Ignoring the authenticated interval or bypassing sequence allocation must fail."""

    heartbeat_ready = asyncio.Event()
    calls = 0

    async def release_one_heartbeat(seconds: float) -> None:
        nonlocal calls
        assert seconds == 7
        calls += 1
        if calls == 1:
            return
        await asyncio.Event().wait()

    socket = ScriptedSocket(command_gate=heartbeat_ready)
    auth_ok = dict(AUTH_OK)
    auth_ok["heartbeat_interval_seconds"] = 7
    socket._handshake = iter((CHALLENGE, auth_ok))
    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=release_one_heartbeat,
    )

    task = asyncio.create_task(simulator.run_connection(socket))
    while len(socket.sent) < 4:
        await asyncio.sleep(0)
    heartbeat_ready.set()
    await task

    assert socket.sent[3] == {
        "type": "device.heartbeat",
        "boot_id": "boot-123",
        "seq": 3,
        "uptime_seconds": 0,
    }


async def test_concurrent_outbound_frames_preserve_sequence_order(tmp_path: Path) -> None:
    """A heartbeat racing a command reply must never reach the backend out of sequence."""

    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class DelayedSocket:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []

        async def send(self, payload: str) -> None:
            frame = json.loads(payload)
            if frame["seq"] == 0:
                first_started.set()
                await release_first.wait()
            self.sent.append(frame)

    socket = DelayedSocket()
    simulator = DeviceSimulator(settings(tmp_path), boot_id="boot-123")
    first = asyncio.create_task(simulator._send(socket, "device.heartbeat", uptime_seconds=0))
    await first_started.wait()
    second = asyncio.create_task(simulator._send(socket, "device.heartbeat", uptime_seconds=1))
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(first, second)

    assert [frame["seq"] for frame in socket.sent] == [0, 1]


async def test_camera_command_acks_before_raw_upload_then_returns_backend_metadata(
    tmp_path: Path,
) -> None:
    """Capture must ACK first and correlate the backend's real media response in RESULT."""

    jpeg = b"\xff\xd8camera-payload\xff\xd9"
    (tmp_path / "capture.jpg").write_bytes(jpeg)
    command = {
        "type": "command.request",
        "command_id": COMMAND_ID,
        "command": "camera.capture",
        "payload": {},
    }
    socket = ScriptedSocket([command])
    response = {
        "id": "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
        "home_id": "b73e6650-9f82-4822-b7b3-bc17a4e0da94",
        "device_id": "d5af9bf4-86f7-468f-b062-f62592561b42",
        "command_id": COMMAND_ID,
        "content_type": "image/jpeg",
        "size_bytes": len(jpeg),
        "url": "/api/homes/b73e6650-9f82-4822-b7b3-bc17a4e0da94/media/"
        "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
    }

    async def uploader(
        _client: httpx.AsyncClient,
        *,
        backend_url: str,
        command_id: UUID,
        upload_token: str,
        jpeg: bytes,
    ) -> dict[str, object]:
        assert socket.sent[-1]["type"] == "command.ack"
        assert backend_url == "http://backend:8000/"
        assert command_id == UUID(COMMAND_ID)
        assert upload_token == "upload-token"
        assert jpeg == b"\xff\xd8camera-payload\xff\xd9"
        return response

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=never_sleep,
        media_uploader=uploader,
    )
    await simulator.run_connection(socket)

    assert socket.sent[3:] == [
        {
            "type": "command.ack",
            "boot_id": "boot-123",
            "seq": 3,
            "command_id": COMMAND_ID,
            "status": "accepted",
        },
        {
            "type": "command.result",
            "boot_id": "boot-123",
            "seq": 4,
            "command_id": COMMAND_ID,
            "status": "completed",
            "result": response,
        },
    ]


async def test_blocked_capture_does_not_delay_following_command_ack(tmp_path: Path) -> None:
    """A slow media upload must not prevent the receive loop from ACKing later commands."""

    (tmp_path / "capture.jpg").write_bytes(b"\xff\xd8camera-payload\xff\xd9")
    upload_started = asyncio.Event()
    release_upload = asyncio.Event()
    both_acks = asyncio.Event()
    both_results = asyncio.Event()
    close_connection = asyncio.Event()
    commands = [
        {
            "type": "command.request",
            "command_id": COMMAND_ID,
            "command": "camera.capture",
            "payload": {},
        },
        {
            "type": "command.request",
            "command_id": "7293ea94-120f-4567-973d-21aacc19a64c",
            "command": "device.status.request",
            "payload": {},
        },
    ]

    class HoldingCommandSocket(ScriptedSocket):
        async def send(self, payload: str) -> None:
            await super().send(payload)
            if sum(frame["type"] == "command.ack" for frame in self.sent) == 2:
                both_acks.set()
            if sum(frame["type"] == "command.result" for frame in self.sent) == 2:
                both_results.set()

        async def __anext__(self) -> str:
            try:
                return json.dumps(next(self._commands))
            except StopIteration:
                await close_connection.wait()
                raise StopAsyncIteration from None

    socket = HoldingCommandSocket(commands)

    async def blocked_uploader(
        _client: httpx.AsyncClient,
        **_kwargs: object,
    ) -> dict[str, object]:
        upload_started.set()
        await release_upload.wait()
        return {
            "id": "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
            "home_id": "b73e6650-9f82-4822-b7b3-bc17a4e0da94",
            "device_id": "d5af9bf4-86f7-468f-b062-f62592561b42",
            "command_id": COMMAND_ID,
            "content_type": "image/jpeg",
            "size_bytes": 20,
            "url": "/api/homes/b73e6650-9f82-4822-b7b3-bc17a4e0da94/media/"
            "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
        }

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=never_sleep,
        media_uploader=blocked_uploader,
    )
    session = asyncio.create_task(simulator.run_connection(socket))
    try:
        await asyncio.wait_for(upload_started.wait(), timeout=1)
        await asyncio.wait_for(both_acks.wait(), timeout=0.1)
        acked_ids = {
            frame["command_id"]
            for frame in socket.sent
            if frame["type"] == "command.ack" and frame["status"] == "accepted"
        }
        assert acked_ids == {COMMAND_ID, "7293ea94-120f-4567-973d-21aacc19a64c"}

        release_upload.set()
        await asyncio.wait_for(both_results.wait(), timeout=1)
        close_connection.set()
        await asyncio.wait_for(session, timeout=1)
        assert simulator._command_tasks == set()
    finally:
        release_upload.set()
        close_connection.set()
        if not session.done():
            session.cancel()
        with suppress(asyncio.CancelledError):
            await session


async def test_capture_before_refresh_window_uses_current_upload_token(tmp_path: Path) -> None:
    """A capture safely before expiry must use the current authenticated upload token."""

    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    (tmp_path / "capture.jpg").write_bytes(b"\xff\xd8camera-payload\xff\xd9")
    command = {
        "type": "command.request",
        "command_id": COMMAND_ID,
        "command": "camera.capture",
        "payload": {},
    }
    socket = ScriptedSocket([command])
    auth_ok = dict(AUTH_OK)
    auth_ok["upload_expires_at"] = (now + timedelta(seconds=60)).isoformat()
    socket._handshake = iter((CHALLENGE, auth_ok))
    used_tokens: list[str] = []

    async def uploader(
        _client: httpx.AsyncClient,
        **kwargs: object,
    ) -> dict[str, object]:
        used_tokens.append(str(kwargs["upload_token"]))
        return {
            "id": "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
            "home_id": "b73e6650-9f82-4822-b7b3-bc17a4e0da94",
            "device_id": "d5af9bf4-86f7-468f-b062-f62592561b42",
            "command_id": COMMAND_ID,
            "content_type": "image/jpeg",
            "size_bytes": 20,
            "url": "/api/homes/b73e6650-9f82-4822-b7b3-bc17a4e0da94/media/"
            "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
        }

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=never_sleep,
        now=lambda: now,
        media_uploader=uploader,
    )
    await simulator.run_connection(socket)

    assert used_tokens == ["upload-token"]
    assert socket.sent[-1]["type"] == "command.result"
    assert socket.sent[-1]["status"] == "completed"


async def test_expired_upload_session_rejects_capture_without_using_stale_token(
    tmp_path: Path,
) -> None:
    """A capture at expiry must receive a terminal reply without calling the uploader."""

    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    (tmp_path / "capture.jpg").write_bytes(b"\xff\xd8camera-payload\xff\xd9")
    socket = ScriptedSocket(
        [
            {
                "type": "command.request",
                "command_id": COMMAND_ID,
                "command": "camera.capture",
                "payload": {},
            }
        ]
    )
    auth_ok = dict(AUTH_OK)
    auth_ok["upload_expires_at"] = now.isoformat()
    socket._handshake = iter((CHALLENGE, auth_ok))
    uploader_called = False

    async def forbidden_uploader(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal uploader_called
        uploader_called = True
        return {}

    async def expiry_aware_sleep(seconds: float) -> None:
        if seconds <= 0:
            return
        await asyncio.Event().wait()

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=expiry_aware_sleep,
        now=lambda: now,
        media_uploader=forbidden_uploader,
    )
    await asyncio.wait_for(simulator.run_connection(socket), timeout=1)

    assert uploader_called is False
    assert socket.sent[-1] == {
        "type": "command.ack",
        "boot_id": "boot-123",
        "seq": 3,
        "command_id": COMMAND_ID,
        "status": "rejected",
        "error_code": "AUTH_FAILED",
    }


async def test_capture_does_not_use_token_that_expires_after_ack(tmp_path: Path) -> None:
    """Clock advance between accepted ACK and execution must fail RESULT without stale upload."""

    before_expiry = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    expires_at = before_expiry + timedelta(seconds=60)
    current_time = before_expiry
    (tmp_path / "capture.jpg").write_bytes(b"\xff\xd8camera-payload\xff\xd9")

    class ExpiringAfterAckSocket(ScriptedSocket):
        async def send(self, payload: str) -> None:
            nonlocal current_time
            await super().send(payload)
            frame = self.sent[-1]
            if frame["type"] == "command.ack" and frame["status"] == "accepted":
                current_time = expires_at

    socket = ExpiringAfterAckSocket(
        [
            {
                "type": "command.request",
                "command_id": COMMAND_ID,
                "command": "camera.capture",
                "payload": {},
            }
        ]
    )
    auth_ok = dict(AUTH_OK)
    auth_ok["upload_expires_at"] = expires_at.isoformat()
    socket._handshake = iter((CHALLENGE, auth_ok))
    uploader_called = False

    async def forbidden_uploader(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal uploader_called
        uploader_called = True
        return {}

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=never_sleep,
        now=lambda: current_time,
        media_uploader=forbidden_uploader,
    )
    await simulator.run_connection(socket)

    assert uploader_called is False
    assert socket.sent[-1] == {
        "type": "command.result",
        "boot_id": "boot-123",
        "seq": 4,
        "command_id": COMMAND_ID,
        "status": "failed",
        "result": {},
        "error_code": "AUTH_FAILED",
    }


async def test_capture_rechecks_expiry_after_loading_before_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JPEG loading must not leave a now-expired token eligible for upload."""

    before_expiry = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    expires_at = before_expiry + timedelta(seconds=60)
    current_time = before_expiry
    socket = ScriptedSocket(
        [
            {
                "type": "command.request",
                "command_id": COMMAND_ID,
                "command": "camera.capture",
                "payload": {},
            }
        ]
    )
    auth_ok = dict(AUTH_OK)
    auth_ok["upload_expires_at"] = expires_at.isoformat()
    socket._handshake = iter((CHALLENGE, auth_ok))
    uploader_called = False

    def expire_while_loading(_path: Path | None) -> bytes:
        nonlocal current_time
        current_time = expires_at
        return b"\xff\xd8camera-payload\xff\xd9"

    async def forbidden_uploader(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal uploader_called
        uploader_called = True
        return {}

    monkeypatch.setattr("portero_simulator.client.load_jpeg", expire_while_loading)
    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=never_sleep,
        now=lambda: current_time,
        media_uploader=forbidden_uploader,
    )
    await simulator.run_connection(socket)

    assert uploader_called is False
    assert socket.sent[-1] == {
        "type": "command.result",
        "boot_id": "boot-123",
        "seq": 4,
        "command_id": COMMAND_ID,
        "status": "failed",
        "result": {},
        "error_code": "AUTH_FAILED",
    }


async def test_idle_connection_reauthenticates_before_upload_token_expiry(tmp_path: Path) -> None:
    """An idle long-lived socket must end early enough to obtain a fresh upload token."""

    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    reauth_delays: list[float] = []

    class IdleSocket(ScriptedSocket):
        async def __anext__(self) -> str:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    socket = IdleSocket()
    auth_ok = dict(AUTH_OK)
    auth_ok["upload_expires_at"] = (now + timedelta(seconds=60)).isoformat()
    socket._handshake = iter((CHALLENGE, auth_ok))

    async def controlled_sleep(seconds: float) -> None:
        if seconds == pytest.approx(55):
            reauth_delays.append(seconds)
            return
        await asyncio.Event().wait()

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=controlled_sleep,
        now=lambda: now,
    )
    await asyncio.wait_for(simulator.run_connection(socket), timeout=1)

    assert reauth_delays == [pytest.approx(55)]
    assert simulator._command_tasks == set()


async def test_reauthentication_drains_active_command_and_rejects_new_work(
    tmp_path: Path,
) -> None:
    """Refresh must finish accepted work and terminally reject commands arriving while draining."""

    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    (tmp_path / "capture.jpg").write_bytes(b"\xff\xd8camera-payload\xff\xd9")
    release_upload = asyncio.Event()
    upload_started = asyncio.Event()
    refresh_waiting = asyncio.Event()
    trigger_refresh = asyncio.Event()
    refresh_fired = asyncio.Event()
    allow_second_command = asyncio.Event()
    second_rejected = asyncio.Event()
    first_id = COMMAND_ID
    second_id = "7293ea94-120f-4567-973d-21aacc19a64c"
    command_index = 0

    class RefreshRaceSocket(ScriptedSocket):
        async def send(self, payload: str) -> None:
            await super().send(payload)
            frame = self.sent[-1]
            if (
                frame["type"] == "command.ack"
                and frame["command_id"] == second_id
                and frame["status"] == "rejected"
            ):
                second_rejected.set()

        async def __anext__(self) -> str:
            nonlocal command_index
            if command_index == 1:
                await allow_second_command.wait()
            try:
                command = next(self._commands)
            except StopIteration:
                await self._commands_complete.wait()
                raise StopAsyncIteration from None
            command_index += 1
            return json.dumps(command)

    socket = RefreshRaceSocket(
        [
            {
                "type": "command.request",
                "command_id": first_id,
                "command": "camera.capture",
                "payload": {},
            },
            {
                "type": "command.request",
                "command_id": second_id,
                "command": "device.status.request",
                "payload": {},
            },
        ]
    )
    auth_ok = dict(AUTH_OK)
    auth_ok["upload_expires_at"] = (now + timedelta(seconds=60)).isoformat()
    socket._handshake = iter((CHALLENGE, auth_ok))

    async def blocked_uploader(
        _client: httpx.AsyncClient,
        **_kwargs: object,
    ) -> dict[str, object]:
        upload_started.set()
        await release_upload.wait()
        return {
            "id": "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
            "home_id": "b73e6650-9f82-4822-b7b3-bc17a4e0da94",
            "device_id": "d5af9bf4-86f7-468f-b062-f62592561b42",
            "command_id": first_id,
            "content_type": "image/jpeg",
            "size_bytes": 20,
            "url": "/api/homes/b73e6650-9f82-4822-b7b3-bc17a4e0da94/media/"
            "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
        }

    async def controlled_sleep(seconds: float) -> None:
        if seconds == pytest.approx(55):
            refresh_waiting.set()
            await trigger_refresh.wait()
            refresh_fired.set()
            return
        await asyncio.Event().wait()

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=controlled_sleep,
        now=lambda: now,
        media_uploader=blocked_uploader,
    )
    session = asyncio.create_task(simulator.run_connection(socket))
    try:
        await asyncio.wait_for(upload_started.wait(), timeout=1)
        await asyncio.wait_for(refresh_waiting.wait(), timeout=1)
        trigger_refresh.set()
        await asyncio.wait_for(refresh_fired.wait(), timeout=1)
        allow_second_command.set()
        await asyncio.wait_for(second_rejected.wait(), timeout=1)

        second_ack = next(
            frame
            for frame in socket.sent
            if frame["type"] == "command.ack" and frame["command_id"] == second_id
        )
        assert second_ack["status"] == "rejected"
        assert second_ack["error_code"] == "AUTH_FAILED"
        assert sum(frame["type"] == "device.status" for frame in socket.sent) == 1

        release_upload.set()
        await asyncio.wait_for(session, timeout=1)
        assert any(
            frame["type"] == "command.result" and frame["command_id"] == first_id
            for frame in socket.sent
        )
        assert simulator._command_tasks == set()
    finally:
        release_upload.set()
        allow_second_command.set()
        trigger_refresh.set()
        if not session.done():
            session.cancel()
        with suppress(asyncio.CancelledError):
            await session


async def test_reauthentication_replies_to_receive_completed_at_drain_boundary(
    tmp_path: Path,
) -> None:
    """A command consumed while the last active task finishes must not disappear."""

    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    (tmp_path / "capture.jpg").write_bytes(b"\xff\xd8camera-payload\xff\xd9")
    first_id = COMMAND_ID
    second_id = "7293ea94-120f-4567-973d-21aacc19a64c"
    boundary_id = "b5f72cc4-0f72-47e7-87f2-09f222781872"
    release_upload = asyncio.Event()
    upload_started = asyncio.Event()
    refresh_waiting = asyncio.Event()
    trigger_refresh = asyncio.Event()
    allow_second_command = asyncio.Event()
    second_command_consumed = asyncio.Event()
    first_result_send_started = asyncio.Event()
    release_first_result_send = asyncio.Event()
    second_rejection_send_started = asyncio.Event()
    boundary_command_consumed = asyncio.Event()
    command_index = 0
    consumed_command_ids: list[str] = []

    commands = [
        {
            "type": "command.request",
            "command_id": first_id,
            "command": "camera.capture",
            "payload": {},
        },
        {
            "type": "command.request",
            "command_id": second_id,
            "command": "device.status.request",
            "payload": {},
        },
        {
            "type": "command.request",
            "command_id": boundary_id,
            "command": "device.status.request",
            "payload": {},
        },
    ]

    class DrainBoundarySocket(ScriptedSocket):
        async def send(self, payload: str) -> None:
            frame = json.loads(payload)
            await super().send(payload)
            if frame["type"] == "command.result" and frame["command_id"] == first_id:
                first_result_send_started.set()
                await release_first_result_send.wait()
            if (
                frame["type"] == "command.ack"
                and frame["command_id"] == second_id
                and frame["status"] == "rejected"
            ):
                second_rejection_send_started.set()
                await asyncio.sleep(0)

        async def __anext__(self) -> str:
            nonlocal command_index
            if command_index == 1:
                await allow_second_command.wait()
            if command_index >= len(commands):
                await asyncio.Event().wait()
                raise AssertionError("unreachable")
            command = commands[command_index]
            command_index += 1
            command_id = str(command["command_id"])
            consumed_command_ids.append(command_id)
            if command_id == second_id:
                second_command_consumed.set()
            if command_id == boundary_id:
                boundary_command_consumed.set()
            return json.dumps(command)

    socket = DrainBoundarySocket(commands)
    auth_ok = dict(AUTH_OK)
    auth_ok["upload_expires_at"] = (now + timedelta(seconds=60)).isoformat()
    socket._handshake = iter((CHALLENGE, auth_ok))

    async def blocked_uploader(
        _client: httpx.AsyncClient,
        **_kwargs: object,
    ) -> dict[str, object]:
        upload_started.set()
        await release_upload.wait()
        return {
            "id": "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
            "home_id": "b73e6650-9f82-4822-b7b3-bc17a4e0da94",
            "device_id": "d5af9bf4-86f7-468f-b062-f62592561b42",
            "command_id": first_id,
            "content_type": "image/jpeg",
            "size_bytes": 20,
            "url": "/api/homes/b73e6650-9f82-4822-b7b3-bc17a4e0da94/media/"
            "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
        }

    async def controlled_sleep(seconds: float) -> None:
        if seconds == pytest.approx(55):
            refresh_waiting.set()
            await trigger_refresh.wait()
            return
        await asyncio.Event().wait()

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=controlled_sleep,
        now=lambda: now,
        media_uploader=blocked_uploader,
    )
    session = asyncio.create_task(simulator.run_connection(socket))
    try:
        await asyncio.wait_for(upload_started.wait(), timeout=1)
        await asyncio.wait_for(refresh_waiting.wait(), timeout=1)
        trigger_refresh.set()
        release_upload.set()
        await asyncio.wait_for(first_result_send_started.wait(), timeout=1)
        allow_second_command.set()
        await asyncio.wait_for(second_command_consumed.wait(), timeout=1)
        release_first_result_send.set()
        await asyncio.wait_for(second_rejection_send_started.wait(), timeout=1)
        await asyncio.wait_for(boundary_command_consumed.wait(), timeout=1)
        await asyncio.wait_for(session, timeout=1)

        acked_ids = {
            str(frame["command_id"]) for frame in socket.sent if frame["type"] == "command.ack"
        }
        terminal_ids = {
            str(frame["command_id"])
            for frame in socket.sent
            if frame["type"] == "command.result"
            or (frame["type"] == "command.ack" and frame["status"] == "rejected")
        }
        assert consumed_command_ids == [first_id, second_id, boundary_id]
        assert acked_ids == set(consumed_command_ids)
        assert terminal_ids == set(consumed_command_ids)
        assert simulator._command_tasks == set()
    finally:
        release_upload.set()
        allow_second_command.set()
        release_first_result_send.set()
        trigger_refresh.set()
        if not session.done():
            session.cancel()
        with suppress(asyncio.CancelledError):
            await session


async def test_status_command_acks_sends_snapshot_then_completes(tmp_path: Path) -> None:
    """Status requests must return the configured wire snapshot between ACK and RESULT."""

    socket = ScriptedSocket(
        [
            {
                "type": "command.request",
                "command_id": COMMAND_ID,
                "command": "device.status.request",
                "payload": {},
            }
        ]
    )
    simulator = DeviceSimulator(
        settings(tmp_path, camera="error", free_heap_bytes=4096),
        boot_id="boot-123",
        sleep=never_sleep,
    )
    await simulator.run_connection(socket)

    assert [frame["type"] for frame in socket.sent[3:]] == [
        "command.ack",
        "device.status",
        "command.result",
    ]
    assert socket.sent[4]["camera"] == "error"
    assert socket.sent[4]["free_heap_bytes"] == 4096
    assert socket.sent[5]["status"] == "completed"
    assert socket.sent[5]["result"] == {}


async def test_unknown_command_returns_only_rejected_ack(tmp_path: Path) -> None:
    """Sending RESULT after a rejected ACK would violate the backend lifecycle."""

    socket = ScriptedSocket(
        [
            {
                "type": "command.request",
                "command_id": COMMAND_ID,
                "command": "access.unlock",
                "payload": {},
            }
        ]
    )
    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=never_sleep,
    )
    await simulator.run_connection(socket)

    assert socket.sent[3:] == [
        {
            "type": "command.ack",
            "boot_id": "boot-123",
            "seq": 3,
            "command_id": COMMAND_ID,
            "status": "rejected",
            "error_code": "INVALID_COMMAND",
        }
    ]


async def test_capture_failure_uses_backend_error_semantics(tmp_path: Path) -> None:
    """A failed capture after acceptance must emit the canonical failed RESULT."""

    (tmp_path / "capture.jpg").write_bytes(b"\xff\xd8payload\xff\xd9")
    socket = ScriptedSocket(
        [
            {
                "type": "command.request",
                "command_id": COMMAND_ID,
                "command": "camera.capture",
                "payload": {},
            }
        ]
    )

    async def failed_upload(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise httpx.ConnectError("backend unavailable")

    simulator = DeviceSimulator(
        settings(tmp_path),
        boot_id="boot-123",
        sleep=never_sleep,
        media_uploader=failed_upload,
    )
    await simulator.run_connection(socket)

    assert socket.sent[-1] == {
        "type": "command.result",
        "boot_id": "boot-123",
        "seq": 4,
        "command_id": COMMAND_ID,
        "status": "failed",
        "result": {},
        "error_code": "CAMERA_CAPTURE_FAILED",
    }


async def test_reconnect_uses_configured_jitter_free_schedule(tmp_path: Path) -> None:
    """Reconnect orchestration must expose the deterministic configured schedule."""

    delays: list[float] = []

    async def stop_after_three_delays(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 3:
            raise asyncio.CancelledError

    def unavailable_connector(*_args: object, **_kwargs: object) -> object:
        raise ConnectionError("offline")

    simulator = DeviceSimulator(
        settings(tmp_path, reconnect_max_seconds=30, reconnect_jitter=False),
        connect=unavailable_connector,
        sleep=stop_after_three_delays,
    )

    with pytest.raises(asyncio.CancelledError):
        await simulator.run_forever()

    assert delays == [1, 2, 4]


async def test_clean_disconnect_waits_before_reconnecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean close must not spin in an immediate reconnect loop."""

    attempts = 0
    delays: list[float] = []

    async def clean_then_forbid_immediate_retry() -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise asyncio.CancelledError

    async def stop_at_first_delay(seconds: float) -> None:
        delays.append(seconds)
        raise asyncio.CancelledError

    simulator = DeviceSimulator(
        settings(tmp_path, reconnect_jitter=False),
        sleep=stop_at_first_delay,
    )
    monkeypatch.setattr(simulator, "run_once", clean_then_forbid_immediate_retry)

    with pytest.raises(asyncio.CancelledError):
        await simulator.run_forever()

    assert attempts == 1
    assert delays == [1]


async def test_authenticated_session_resets_reconnect_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovered session must not retain delays accumulated by earlier failures."""

    attempts = 0
    delays: list[float] = []

    async def fail_twice_then_disconnect_cleanly() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("offline")

    async def record_until_recovery(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 3:
            raise asyncio.CancelledError

    simulator = DeviceSimulator(
        settings(tmp_path, reconnect_jitter=False),
        sleep=record_until_recovery,
    )
    monkeypatch.setattr(simulator, "run_once", fail_twice_then_disconnect_cleanly)

    with pytest.raises(asyncio.CancelledError):
        await simulator.run_forever()

    assert delays == [1, 2, 1]


def test_cli_builds_configurable_runtime_from_flags_and_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI flags must override runtime defaults while the secret remains environment-only."""

    image = tmp_path / "chosen.jpg"
    monkeypatch.setenv("DEVICE_SECRET", "environment-only-secret")
    configured = settings_from_args(
        [
            "--backend-url",
            "https://portero.example",
            "--device-id",
            "PI-000042",
            "--image",
            str(image),
            "--firmware-version",
            "test-fw",
            "--hardware-model",
            "test-hw",
            "--camera",
            "error",
            "--free-heap-bytes",
            "8192",
            "--reconnect-max-seconds",
            "12",
            "--no-reconnect-jitter",
        ]
    )

    assert configured.websocket_url == "wss://portero.example/ws/device"
    assert configured.device_id == "PI-000042"
    assert configured.device_secret.get_secret_value() == "environment-only-secret"
    assert configured.image == image
    assert configured.firmware_version == "test-fw"
    assert configured.hardware_model == "test-hw"
    assert configured.camera == "error"
    assert configured.free_heap_bytes == 8192
    assert configured.reconnect_max_seconds == 12
    assert configured.reconnect_jitter is False


def test_verbose_logging_suppresses_websocket_authentication_frames() -> None:
    """Verbose simulator diagnostics must not enable dependency frame dumps with credentials."""

    root = logging.getLogger()
    simulator_logger = logging.getLogger("portero_simulator")
    websockets_logger = logging.getLogger("websockets")
    websockets_client_logger = logging.getLogger("websockets.client")
    original_levels = (
        root.level,
        simulator_logger.level,
        websockets_logger.level,
        websockets_client_logger.level,
    )
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    root.addHandler(handler)
    try:
        configure_logging(verbose=True)
        simulator_logger.debug("simulator diagnostic")
        logging.getLogger("websockets.client").debug("< TEXT auth.ok upload_token=SENTINEL_TOKEN")
        logging.getLogger("websockets.client").debug("> TEXT auth.response digest=SENTINEL_DIGEST")

        emitted = stream.getvalue()
        assert "simulator diagnostic" in emitted
        assert "SENTINEL_TOKEN" not in emitted
        assert "SENTINEL_DIGEST" not in emitted
    finally:
        root.setLevel(original_levels[0])
        simulator_logger.setLevel(original_levels[1])
        websockets_logger.setLevel(original_levels[2])
        websockets_client_logger.setLevel(original_levels[3])
        root.removeHandler(handler)
