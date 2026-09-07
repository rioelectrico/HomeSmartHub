"""In-memory device connection lifecycle contracts."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.devices.connections import DeviceConnectionRegistry, DeviceUploadSession
from app.schemas.conversations import ConversationError


def test_upload_session_stores_only_hash_and_compares_tokens() -> None:
    """Keeping plaintext upload credentials in memory would widen credential exposure."""

    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    session = DeviceUploadSession.from_token("one-time-token", expires_at)

    assert b"one-time-token" not in session.token_hash
    assert session.matches("one-time-token")
    assert not session.matches("wrong-token")


async def test_registry_rejects_upload_token_without_expiry() -> None:
    """A token without expiry would violate the ephemeral upload-session contract."""

    registry = DeviceConnectionRegistry()

    with pytest.raises(ValueError, match="together"):
        await registry.register(uuid4(), AsyncMock(), upload_token="unsafe-token")


async def test_revocation_invalidates_auth_epoch_and_never_closes_aba_successor() -> None:
    """A commit fences old authentication even without a socket; cleanup owns one generation."""
    registry = DeviceConnectionRegistry()
    device_id = uuid4()
    socket = AsyncMock()
    notifications = []

    async def disconnected(lease):
        notifications.append(lease.generation)

    old_epoch = registry.authorization_epoch(device_id)
    lease = await registry.claim(device_id, socket, on_disconnect=disconnected)
    registry.revoke_access(device_id)
    assert not await registry.is_current(lease)
    with pytest.raises(ConnectionError, match="AUTH_FAILED"):
        await registry.claim(device_id, AsyncMock(), authorization_epoch=old_epoch)
    # Republish exactly the same object before old close starts (ABA).
    successor = await registry.claim(
        device_id, socket, authorization_epoch=registry.authorization_epoch(device_id)
    )
    await registry.wait_revocations()
    await registry.unregister(lease)
    assert await registry.is_current(successor)
    assert notifications == [lease.generation]
    socket.close.assert_not_awaited()
    registry.revoke_access(device_id)
    await registry.wait_revocations()
    socket.close.assert_awaited_once_with(code=4002, reason="AUTH_FAILED")


async def test_revocation_fences_authentication_when_no_socket_was_published() -> None:
    registry = DeviceConnectionRegistry()
    device_id = uuid4()
    epoch = registry.authorization_epoch(device_id)
    registry.revoke_access(device_id)
    with pytest.raises(ConnectionError, match="AUTH_FAILED"):
        await registry.claim(device_id, AsyncMock(), authorization_epoch=epoch)


async def test_waiting_for_revocation_is_device_scoped_and_cancellation_safe() -> None:
    """One slow peer cannot hold another home's admin request or lose cleanup on cancellation."""
    registry = DeviceConnectionRegistry()
    first_id, second_id = uuid4(), uuid4()
    first, second = AsyncMock(), AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_close(**kwargs):
        entered.set()
        await release.wait()

    first.close.side_effect = held_close
    await registry.claim(first_id, first)
    await registry.claim(second_id, second)
    registry.revoke_access(first_id)
    await entered.wait()
    registry.revoke_access(second_id)
    await asyncio.wait_for(registry.wait_revocations(second_id), timeout=0.2)
    second.close.assert_awaited_once_with(code=4002, reason="AUTH_FAILED")
    waiting = asyncio.create_task(registry.wait_revocations(first_id))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    release.set()
    await registry.wait_revocations(first_id)
    first.close.assert_awaited_once_with(code=4002, reason="AUTH_FAILED")


async def test_new_connection_replaces_old_socket_without_holding_lock() -> None:
    """Keeping two sockets, or doing close I/O under the lock, can deadlock dispatch."""

    registry = DeviceConnectionRegistry()
    old_socket = AsyncMock()
    new_socket = AsyncMock()
    device_id = uuid4()

    async def assert_unlocked(**_: object) -> None:
        assert not registry._lock.locked()  # noqa: SLF001 - concurrency contract

    old_socket.close.side_effect = assert_unlocked
    await registry.register(device_id, old_socket)
    await registry.register(device_id, new_socket)

    assert registry.get(device_id) is new_socket
    old_socket.close.assert_awaited_once_with(code=4001, reason="replaced")


async def test_replacement_close_is_bounded() -> None:
    """A predecessor that never finishes closing must not stall registration forever."""

    registry = DeviceConnectionRegistry(close_timeout_seconds=0.01)
    old_socket = AsyncMock()
    new_socket = AsyncMock()
    device_id = uuid4()

    async def never_close(**_: object) -> None:
        await asyncio.Event().wait()

    old_socket.close.side_effect = never_close

    await registry.register(device_id, old_socket)
    await asyncio.wait_for(registry.register(device_id, new_socket), timeout=0.2)

    assert registry.get(device_id) is new_socket


async def test_unregister_is_conditional_and_revokes_upload_token() -> None:
    """A stale socket cleanup must not remove its replacement or leave its token valid."""

    registry = DeviceConnectionRegistry()
    old_socket = AsyncMock()
    new_socket = AsyncMock()
    device_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    await registry.register(
        device_id,
        old_socket,
        upload_token="old-token",
        upload_token_expires_at=expires_at,
    )
    await registry.register(
        device_id,
        new_socket,
        upload_token="new-token",
        upload_token_expires_at=expires_at,
    )
    await registry.unregister(device_id, old_socket)

    assert registry.get(device_id) is new_socket
    assert not await registry.verify_upload_token(device_id, "old-token")
    assert await registry.verify_upload_token(device_id, "new-token")

    await registry.unregister(device_id, new_socket)
    assert registry.get(device_id) is None
    assert not await registry.verify_upload_token(device_id, "new-token")


async def test_send_json_does_network_io_without_holding_lock() -> None:
    """A blocked network send must not block registry mutation."""

    registry = DeviceConnectionRegistry()
    socket = AsyncMock()
    device_id = uuid4()

    async def assert_unlocked(_: object) -> None:
        assert not registry._lock.locked()  # noqa: SLF001 - concurrency contract

    socket.send_json.side_effect = assert_unlocked
    await registry.register(device_id, socket)

    assert await registry.send_json(device_id, {"type": "device.status.request"})
    socket.send_json.assert_awaited_once_with({"type": "device.status.request"})


async def test_expired_upload_token_is_rejected() -> None:
    """An upload token must become unusable at its exact expiry boundary."""

    registry = DeviceConnectionRegistry()
    socket = AsyncMock()
    device_id = uuid4()
    await registry.register(
        device_id,
        socket,
        upload_token="expired-token",
        upload_token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert not await registry.verify_upload_token(device_id, "expired-token")


async def test_close_all_revokes_connections_before_network_io() -> None:
    """Shutdown must revoke sessions even when a socket close fails."""

    registry = DeviceConnectionRegistry()
    first, second = AsyncMock(), AsyncMock()
    first.close.side_effect = RuntimeError("transport gone")
    await registry.register(uuid4(), first)
    await registry.register(uuid4(), second)

    await registry.close_all()

    assert registry.connection_count == 0
    first.close.assert_awaited_once_with(code=1001, reason="shutdown")
    second.close.assert_awaited_once_with(code=1001, reason="shutdown")


class GatedSocket:
    """Model a send suspended in transport I/O and independently closable socket."""

    def __init__(self, *, fail=False, slow_cleanup=False):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.closed = False
        self.delivered = []
        self.fail = fail
        self.slow_cleanup = slow_cleanup

    async def send_json(self, data):
        await self._send(data)

    async def send_bytes(self, data):
        await self._send(data)

    async def _send(self, data):
        self.send_task = asyncio.current_task()
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            if self.slow_cleanup:
                await self.cleanup_release.wait()
            raise
        if self.closed:
            raise RuntimeError("closed")
        if self.fail:
            raise RuntimeError("private socket credential")
        self.delivered.append(data)

    async def close(self, code=1000, reason=None):
        self.closed = True


def _control():
    return ConversationError(type="conversation.error", version=1, seq=999, code="AI_UNAVAILABLE")


async def test_lease_serializes_controls_binary_and_commands_with_local_sequence():
    from app.devices.connections import DeviceConversationTransport

    registry = DeviceConnectionRegistry()
    socket = GatedSocket()
    lease = await registry.claim(uuid4(), socket, capabilities=["audio_pcm16_v1"])
    assert lease.capabilities == frozenset({"audio_pcm16_v1"})
    transport = DeviceConversationTransport(registry, lease)
    first = asyncio.create_task(transport.send_control(_control()))
    await socket.entered.wait()
    audio = asyncio.create_task(transport.send_audio(b"pcm"))
    command = asyncio.create_task(registry.send_json(lease.device_id, {"type": "legacy"}))
    last = asyncio.create_task(transport.send_control(_control()))
    await asyncio.sleep(0)
    assert not socket.delivered
    socket.release.set()
    await asyncio.gather(first, audio, command, last)
    assert [socket.delivered[0]["seq"], socket.delivered[3]["seq"]] == [0, 1]
    assert socket.delivered[1:3] == [b"pcm", {"type": "legacy"}]
    assert socket.delivered[0]["version"] == 1
    assert isinstance(socket.delivered[0]["correlation_id"], str)
    assert set(socket.delivered[0]) == {"type", "version", "seq", "code", "correlation_id"}
    await registry.close_all()


@pytest.mark.parametrize("operation", ["cancel", "timeout", "failure", "queued_cancel"])
async def test_failed_send_is_terminal_before_slow_cleanup_and_never_delivers_late(operation):
    from app.devices.connections import DeviceConversationTransport

    registry = DeviceConnectionRegistry(send_timeout_seconds=0.02)
    socket = GatedSocket(fail=operation == "failure", slow_cleanup=True)
    lease = await registry.claim(uuid4(), socket)
    transport = DeviceConversationTransport(registry, lease)
    sending = asyncio.create_task(transport.send_audio(b"must-not-arrive"))
    await socket.entered.wait()
    queued = None
    if operation == "cancel":
        sending.cancel()
    elif operation == "failure":
        socket.release.set()
    elif operation == "queued_cancel":
        queued = asyncio.create_task(transport.send_control(_control()))
        await asyncio.sleep(0)
        queued.cancel()
    try:
        results = await asyncio.wait_for(
            asyncio.gather(sending, *([queued] if queued else []), return_exceptions=True), 0.3
        )
        assert all(isinstance(result, BaseException) for result in results)
        if operation == "cancel":
            assert isinstance(results[0], asyncio.CancelledError)
        assert socket.closed
        assert not await registry.is_current(lease)
        with pytest.raises(ConnectionError, match="DEVICE_DISCONNECTED"):
            await transport.send_control(_control())
        assert not await registry.send_json(lease.device_id, {"type": "legacy"})
        assert socket.delivered == []
    finally:
        socket.cleanup_release.set()
        socket.release.set()
        await asyncio.sleep(0)
        await registry.close_all()
    assert socket.delivered == []


async def test_replacement_retires_pending_sender_without_touching_successor():
    from app.devices.connections import DeviceConversationTransport

    registry = DeviceConnectionRegistry()
    socket = GatedSocket(slow_cleanup=True)
    old = await registry.claim(uuid4(), socket)
    transport = DeviceConversationTransport(registry, old)
    sending = asyncio.create_task(transport.send_audio(b"stale"))
    await socket.entered.wait()
    replacement = GatedSocket()
    replacement.release.set()
    new = await registry.register(old.device_id, replacement)
    try:
        await asyncio.wait_for(asyncio.gather(sending, return_exceptions=True), 0.3)
        assert socket.closed
        assert await registry.is_current(new)
        with pytest.raises(ConnectionError):
            await transport.send_audio(b"stale-again")
        assert await registry.send_json(new.device_id, {"type": "new-command"})
        assert replacement.delivered == [{"type": "new-command"}]
    finally:
        socket.cleanup_release.set()
        socket.release.set()
        await asyncio.sleep(0)
        await registry.close_all()
    assert socket.delivered == []


async def test_auth_ok_and_binary_share_lease_writer_and_cancel_revokes_upload():
    registry = DeviceConnectionRegistry()
    socket = GatedSocket()
    lease = await registry.claim(
        uuid4(),
        socket,
        upload_token="auth-upload",
        upload_token_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    auth = asyncio.create_task(registry.send_json_to(lease, {"type": "auth.ok"}))
    await socket.entered.wait()
    binary = asyncio.create_task(registry.send_bytes(lease, b"pcm"))
    await asyncio.sleep(0)
    auth.cancel()
    results = await asyncio.gather(auth, binary, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert isinstance(results[1], ConnectionError)
    assert not await registry.verify_upload_token(lease.device_id, "auth-upload")
    assert socket.closed and not socket.delivered
    await registry.close_all()


async def test_writer_admission_is_bounded_and_saturation_closes_socket():
    from app.devices.connections import DeviceConversationTransport

    registry = DeviceConnectionRegistry()
    socket = GatedSocket()
    lease = await registry.claim(uuid4(), socket)
    transport = DeviceConversationTransport(registry, lease)
    first = asyncio.create_task(transport.send_audio(b"first"))
    await socket.entered.wait()
    queued = [asyncio.create_task(transport.send_audio(b"queued")) for _ in range(8)]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(first, *queued, return_exceptions=True), 0.3
        )
        assert all(isinstance(item, BaseException) for item in results)
        assert socket.closed and not socket.delivered
    finally:
        await registry.close_all()


async def test_aborted_replacement_still_closes_predecessor_and_notifies_each_lease_once():
    registry = DeviceConnectionRegistry()
    retired = []

    async def disconnected(lease):
        retired.append(lease.generation)

    old_socket, new_socket = GatedSocket(), GatedSocket()
    old = await registry.claim(uuid4(), old_socket, on_disconnect=disconnected)
    new = await registry.claim(old.device_id, new_socket, on_disconnect=disconnected)
    # The new handler can fail/cancel before it reaches close_replaced.
    assert await registry.unregister(new)
    assert old_socket.closed
    assert retired == [old.generation, new.generation]
    await registry.close_replaced(new)
    await registry.unregister(old)
    assert retired == [old.generation, new.generation]


async def test_repeated_retirement_preserves_inflight_cancellation_cleanup():
    from app.devices.connections import DeviceConversationTransport

    registry = DeviceConnectionRegistry()
    socket = GatedSocket(slow_cleanup=True)
    lease = await registry.claim(uuid4(), socket)
    transport = DeviceConversationTransport(registry, lease)
    sending = asyncio.create_task(transport.send_audio(b"pcm"))
    await socket.entered.wait()
    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending
    await socket.cancelled.wait()
    try:
        await registry.close_all()
        await asyncio.sleep(0)
        assert not socket.send_task.done(), "repeated retirement must not interrupt local cleanup"
        assert socket.closed and not socket.delivered
    finally:
        socket.cleanup_release.set()
        await asyncio.gather(socket.send_task, return_exceptions=True)


async def test_concurrent_disconnect_notifications_are_per_generation_and_aba_safe():
    registry = DeviceConnectionRegistry()
    device_id = uuid4()
    socket_a, socket_b = GatedSocket(), GatedSocket()
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def disconnected(lease):
        calls.append(lease.generation)
        entered.set()
        await release.wait()

    old = await registry.claim(device_id, socket_a, on_disconnect=disconnected)
    middle = await registry.claim(device_id, socket_b, on_disconnect=disconnected)
    current = await registry.claim(device_id, socket_a, on_disconnect=disconnected)
    first = asyncio.create_task(registry.notify_disconnect(old))
    await asyncio.wait_for(entered.wait(), 1)
    racing = [
        asyncio.create_task(registry.notify_disconnect(replace(old))),
        asyncio.create_task(registry.close_replaced(middle)),
        asyncio.create_task(registry.unregister(old)),
        asyncio.create_task(registry.close_owned(old)),
    ]
    await asyncio.sleep(0.01)
    first.cancel()
    try:
        assert calls == [old.generation]
        assert not socket_a.closed
        assert await registry.is_current(current)
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, *racing, return_exceptions=True), 1)
        assert isinstance(results[0], asyncio.CancelledError)
        assert all(not isinstance(item, BaseException) for item in results[1:])
    await registry.close_replaced(current)
    assert calls == [old.generation, middle.generation]
    assert socket_b.closed and not socket_a.closed
    await registry.close_owned(current)
    await registry.unregister(current)
    assert calls == [old.generation, middle.generation, current.generation]
