"""Concurrency-safe registry for authenticated device WebSockets."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from app.schemas.conversations import (
    ConversationEnded,
    ConversationOutboundMessage,
    ConversationStarted,
)


class DeviceSocket(Protocol):
    """The WebSocket operations used by the connection registry."""

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...

    async def send_json(self, data: object) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class DeviceUploadSession:
    """Hashed, expiring upload authorization bound to one active socket."""

    token_hash: bytes
    expires_at: datetime

    @classmethod
    def from_token(cls, token: str, expires_at: datetime) -> DeviceUploadSession:
        """Create a session without retaining the one-time plaintext token."""

        return cls(token_hash=sha256(token.encode()).digest(), expires_at=expires_at)

    def matches(self, token: str, *, now: datetime | None = None) -> bool:
        """Constant-time compare a candidate and enforce the expiry boundary."""

        checked_at = now if now is not None else datetime.now(UTC)
        candidate_hash = sha256(token.encode()).digest()
        matches = hmac.compare_digest(candidate_hash, self.token_hash)
        return matches and self.expires_at > checked_at


@dataclass(slots=True)
class _ActiveConnection:
    socket: DeviceSocket
    generation: int
    upload_session: DeviceUploadSession | None = None
    capabilities: frozenset[str] = frozenset()
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    terminal: bool = False
    access_revoked: bool = False
    retired: asyncio.Event = field(default_factory=asyncio.Event)
    sequence: int = 0
    send_waiters: int = 0
    pending_send: asyncio.Task[None] | None = None
    close_task: asyncio.Task[None] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    on_disconnect: Callable[[DeviceConnectionLease], Coroutine[None, None, None]] | None = None
    revocation_fallback: Callable[[DeviceConnectionLease], Coroutine[None, None, None]] | None = (
        None
    )

    def revoke_upload(self) -> None:
        self.upload_session = None

    def retire(self) -> None:
        """Forbid further writes synchronously, before cancellation cleanup can await."""
        if self.terminal:
            return
        self.terminal = True
        self.retired.set()
        self.revoke_upload()
        if self.pending_send is not None and not self.pending_send.done():
            self.pending_send.cancel()


@dataclass(frozen=True, slots=True)
class DeviceConnectionLease:
    """Opaque ownership proof for one published connection generation."""

    device_id: UUID
    socket: DeviceSocket
    generation: int
    _replaced_socket: DeviceSocket | None
    _connection: _ActiveConnection
    _replaced_lease: DeviceConnectionLease | None = None

    @property
    def capabilities(self) -> frozenset[str]:
        return self._connection.capabilities


class DeviceConnectionRegistry:
    """Own active sockets and their connection-bound upload authorization."""

    def __init__(
        self, *, close_timeout_seconds: float = 1.0, send_timeout_seconds: float = 1.0
    ) -> None:
        if close_timeout_seconds <= 0 or send_timeout_seconds <= 0:
            raise ValueError("socket timeouts must be positive")
        self._lock = asyncio.Lock()
        self._connections: dict[UUID, _ActiveConnection] = {}
        self._generations: dict[UUID, int] = {}
        self._lifecycle_locks: dict[UUID, asyncio.Lock] = {}
        self._authorization_epochs: dict[UUID, int] = {}
        self._revocations: dict[asyncio.Task[None], UUID] = {}
        self._close_timeout_seconds = close_timeout_seconds
        self._send_timeout_seconds = send_timeout_seconds

    @property
    def connection_count(self) -> int:
        """Return a best-effort diagnostic snapshot of active connections."""

        return sum(not item.terminal for item in self._connections.values())

    def get(self, device_id: UUID) -> DeviceSocket | None:
        """Return the current socket as a non-blocking diagnostic snapshot."""

        connection = self._connections.get(device_id)
        return connection.socket if connection is not None and not connection.terminal else None

    def authorization_epoch(self, device_id: UUID) -> int:
        """Fence a handshake across a committed credential/enablement change."""
        return self._authorization_epochs.get(device_id, 0)

    def revoke_access(self, device_id: UUID) -> None:
        """Retire the exact current lease synchronously at the successful commit hook.

        Registry mutation blocks contain no awaits, so this event-loop callback
        cannot interleave with claim/is_current. Network and durable cleanup run
        independently of the committing request, even if that request is canceled.
        """
        self._authorization_epochs[device_id] = self.authorization_epoch(device_id) + 1
        connection = self._connections.pop(device_id, None)
        if connection is None:
            return
        connection.access_revoked = True
        connection.retire()
        lease = DeviceConnectionLease(
            device_id, connection.socket, connection.generation, None, connection
        )

        async def finish() -> None:
            # Start durable cleanup immediately, even when the peer stalls close.
            await asyncio.gather(
                self._close_connection(lease, code=4002, reason="AUTH_FAILED"),
                self.notify_disconnect(lease, fallback=connection.revocation_fallback),
            )

        task = asyncio.create_task(finish(), name=f"device-access-revoked:{device_id}")
        self._revocations[task] = device_id
        task.add_done_callback(self._observe)
        task.add_done_callback(self._revocations.pop)

    async def wait_revocations(self, device_id: UUID | None = None) -> None:
        """Join committed revocations without transferring cancellation to cleanup."""
        tasks = tuple(
            task
            for task, owner in self._revocations.items()
            if device_id is None or owner == device_id
        )
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))

    @asynccontextmanager
    async def lifecycle(self, device_id: UUID) -> AsyncIterator[None]:
        """Serialize ownership-dependent durable transitions for one device."""

        async with self._lock:
            lifecycle_lock = self._lifecycle_locks.setdefault(device_id, asyncio.Lock())
        async with lifecycle_lock:
            yield

    async def register(
        self,
        device_id: UUID,
        socket: DeviceSocket,
        *,
        upload_token: str | None = None,
        upload_token_expires_at: datetime | None = None,
        capabilities: Iterable[str] = (),
        on_disconnect: Callable[[DeviceConnectionLease], Coroutine[None, None, None]] | None = None,
    ) -> DeviceConnectionLease:
        """Publish a socket and finish bounded predecessor replacement safely."""

        ownership = await self.claim(
            device_id,
            socket,
            upload_token=upload_token,
            upload_token_expires_at=upload_token_expires_at,
            capabilities=capabilities,
            on_disconnect=on_disconnect,
        )
        try:
            await self.close_replaced(ownership)
        except asyncio.CancelledError:
            try:
                await self.close_owned(ownership)
            finally:
                await asyncio.shield(self.unregister(ownership))
            raise
        return ownership

    async def claim(
        self,
        device_id: UUID,
        socket: DeviceSocket,
        *,
        upload_token: str | None = None,
        upload_token_expires_at: datetime | None = None,
        capabilities: Iterable[str] = (),
        on_disconnect: Callable[[DeviceConnectionLease], Coroutine[None, None, None]] | None = None,
        authorization_epoch: int | None = None,
    ) -> DeviceConnectionLease:
        """Publish ownership without network I/O or a post-publication await."""

        if (upload_token is None) != (upload_token_expires_at is None):
            raise ValueError("upload_token and upload_token_expires_at must be provided together")
        upload_session = (
            DeviceUploadSession.from_token(upload_token, upload_token_expires_at)
            if upload_token is not None and upload_token_expires_at is not None
            else None
        )
        async with self._lock:
            if authorization_epoch is not None and authorization_epoch != self.authorization_epoch(
                device_id
            ):
                raise ConnectionError("AUTH_FAILED")
            previous = self._connections.get(device_id)
            if previous is not None:
                previous.retire()
            generation = self._generations.get(device_id, 0) + 1
            self._generations[device_id] = generation
            replacement = _ActiveConnection(
                socket,
                generation,
                upload_session,
                frozenset(capabilities),
                on_disconnect=on_disconnect,
            )
            self._connections[device_id] = replacement
        return DeviceConnectionLease(
            device_id=device_id,
            socket=socket,
            generation=generation,
            _replaced_socket=previous.socket if previous is not None else None,
            _connection=replacement,
            _replaced_lease=(
                DeviceConnectionLease(
                    device_id, previous.socket, previous.generation, None, previous
                )
                if previous is not None
                else None
            ),
        )

    async def close_replaced(self, ownership: DeviceConnectionLease) -> None:
        """Bound predecessor close without holding the registry mutation lock."""

        previous = ownership._replaced_lease
        if previous is None:
            return
        await self._close_connection(previous, code=4001, reason="replaced")
        await self.notify_disconnect(previous)

    @staticmethod
    def _observe(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    def bind_revocation_cleanup(
        self,
        ownership: DeviceConnectionLease,
        callback: Callable[[DeviceConnectionLease], Coroutine[None, None, None]],
    ) -> None:
        """Supply coordinator cleanup for revocation of a lease without a socket route."""
        ownership._connection.revocation_fallback = callback

    async def notify_disconnect(
        self,
        ownership: DeviceConnectionLease,
        *,
        fallback: Callable[[DeviceConnectionLease], Coroutine[None, None, None]] | None = None,
    ) -> None:
        """Join the single disconnect notification shared by every lease snapshot.

        Coordinator-only callers may supply their callback when no socket route
        installed one. Choosing and starting it has no intervening await, so
        worker ownership loss and socket retirement cannot invoke it twice.
        """
        connection = ownership._connection
        if connection.cleanup_task is None:
            callback = connection.on_disconnect or fallback
            if callback is None:
                return
            connection.cleanup_task = asyncio.create_task(callback(ownership))
            connection.cleanup_task.add_done_callback(self._observe)
        await asyncio.shield(connection.cleanup_task)

    async def close_owned(
        self,
        ownership: DeviceConnectionLease,
        *,
        code: int = 1001,
        reason: str = "device_disconnected",
    ) -> None:
        """Invalidate and bound close of this exact lease before caller cleanup.

        A repeated handler cancellation cannot skip the close attempt. It is
        propagated after the close finishes or reaches its deadline.
        """
        await self._close_connection(ownership, code=code, reason=reason, finish_on_cancel=True)

    async def _close_connection(
        self,
        ownership: DeviceConnectionLease,
        *,
        code: int,
        reason: str,
        finish_on_cancel: bool = False,
    ) -> None:
        connection = ownership._connection
        if connection.access_revoked:
            code, reason = 4002, "AUTH_FAILED"
        connection.retire()
        if connection.close_task is None:

            async def close_socket() -> None:
                current = self._connections.get(ownership.device_id)
                # The same socket object may have been republished with a newer
                # generation (ABA). Its retired lease must not close that owner.
                if (
                    current is not None
                    and current is not connection
                    and current.socket is ownership.socket
                ):
                    return
                await connection.socket.close(code=code, reason=reason)

            connection.close_task = asyncio.create_task(close_socket())
            connection.close_task.add_done_callback(self._observe)
        deadline = asyncio.get_running_loop().time() + self._close_timeout_seconds
        cancelled = False
        while not connection.close_task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait({connection.close_task}, timeout=remaining)
            except asyncio.CancelledError:
                if not finish_on_cancel:
                    raise
                cancelled = True
        if not connection.close_task.done():
            connection.close_task.cancel()
        if cancelled:
            raise asyncio.CancelledError

    async def unregister(
        self,
        ownership: DeviceConnectionLease | UUID,
        socket: DeviceSocket | None = None,
    ) -> bool:
        """Remove only the matching socket so stale cleanup cannot remove a replacement."""

        if isinstance(ownership, DeviceConnectionLease):
            device_id = ownership.device_id
            expected_socket = ownership.socket
            expected_generation: int | None = ownership.generation
        else:
            if socket is None:
                raise TypeError("socket is required when unregistering without an ownership lease")
            device_id = ownership
            expected_socket = socket
            expected_generation = None
        removed = False
        async with self._lock:
            connection = self._connections.get(device_id)
            if (
                connection is not None
                and connection.socket is expected_socket
                and (expected_generation is None or connection.generation == expected_generation)
            ):
                connection.retire()
                del self._connections[device_id]
                removed = True
        if isinstance(ownership, DeviceConnectionLease):
            lease = ownership
        elif removed and connection is not None:
            lease = DeviceConnectionLease(
                device_id, connection.socket, connection.generation, None, connection
            )
        else:
            return False
        try:
            # Publication may be followed by a canceled auth/ONLINE transaction,
            # before the handler had a chance to finish predecessor retirement.
            await self.close_replaced(lease)
        finally:
            await self.notify_disconnect(lease)
        return removed

    async def can_mark_offline(self, ownership: DeviceConnectionLease) -> bool:
        """Confirm a retired generation still represents the latest ownership state."""

        async with self._lock:
            return (
                ownership.device_id not in self._connections
                and self._generations.get(ownership.device_id) == ownership.generation
            )

    async def is_current(self, ownership: DeviceConnectionLease) -> bool:
        """Confirm a lease still owns the published socket generation."""

        async with self._lock:
            connection = self._connections.get(ownership.device_id)
            return (
                connection is not None
                and not connection.terminal
                and connection.socket is ownership.socket
                and connection.generation == ownership.generation
            )

    async def send_json(self, device_id: UUID, payload: Mapping[str, object]) -> bool:
        """Send to a connection without holding the registry lock during network I/O."""

        connection = self._connections.get(device_id)
        if connection is None or connection.terminal:
            return False
        ownership = DeviceConnectionLease(
            device_id, connection.socket, connection.generation, None, connection
        )
        await self._send(ownership, dict(payload))
        return True

    async def send_bytes(self, ownership: DeviceConnectionLease, data: bytes) -> None:
        await self._send(ownership, data)

    async def send_json_to(
        self, ownership: DeviceConnectionLease, payload: Mapping[str, object]
    ) -> None:
        """Send an authenticated legacy control to its exact connection generation."""
        await self._send(ownership, dict(payload))

    async def send_control(
        self, ownership: DeviceConnectionLease, message: ConversationOutboundMessage
    ) -> None:
        await self._send(ownership, message)

    async def _send(
        self,
        ownership: DeviceConnectionLease,
        payload: dict[str, object] | bytes | ConversationOutboundMessage,
    ) -> None:
        connection = ownership._connection
        if connection.terminal or not await self.is_current(ownership):
            raise ConnectionError("DEVICE_DISCONNECTED")
        connection.send_waiters += 1
        retired: asyncio.Task[bool] | None = None
        try:
            # Fixed admission bound includes the active writer; no unbounded lock queue.
            if connection.send_waiters > 8:
                raise ConnectionError("DEVICE_DISCONNECTED")
            async with asyncio.timeout(self._send_timeout_seconds):
                async with connection.send_lock:
                    if connection.terminal or not await self.is_current(ownership):
                        raise ConnectionError("DEVICE_DISCONNECTED")
                    if isinstance(payload, bytes):
                        operation = connection.socket.send_bytes(payload)
                    else:
                        if isinstance(payload, dict):
                            data = payload
                        else:
                            data = payload.model_dump(mode="json")
                            data["seq"] = connection.sequence
                            connection.sequence += 1
                        operation = connection.socket.send_json(data)
                    sending = asyncio.create_task(operation)
                    connection.pending_send = sending
                    sending.add_done_callback(self._observe)
                    retired = asyncio.create_task(connection.retired.wait())
                    await asyncio.wait({sending, retired}, return_when=asyncio.FIRST_COMPLETED)
                    if connection.terminal:
                        raise ConnectionError("DEVICE_DISCONNECTED")
                    sending.result()
                    connection.pending_send = None
        except asyncio.CancelledError:
            connection.retire()
            await self._close_connection(ownership, code=4500, reason="INTERNAL_ERROR")
            raise
        except Exception:
            connection.retire()
            await self._close_connection(ownership, code=4500, reason="INTERNAL_ERROR")
            raise ConnectionError("DEVICE_DISCONNECTED") from None
        finally:
            connection.send_waiters -= 1
            if retired is not None:
                retired.cancel()

    async def verify_upload_token(
        self,
        device_id: UUID,
        token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Constant-time compare a token against the unexpired active connection hash."""

        checked_at = now if now is not None else datetime.now(UTC)
        async with self._lock:
            connection = self._connections.get(device_id)
            session = connection.upload_session if connection is not None else None
            if session is None or connection is None or connection.terminal:
                # Keep the comparison path uniform even when no session exists.
                hmac.compare_digest(sha256(token.encode()).digest(), bytes(sha256().digest_size))
                return False
            return session.matches(token, now=checked_at)

    async def close_all(self) -> None:
        """Revoke every session and close sockets for a clean application shutdown."""

        await self.wait_revocations()
        async with self._lock:
            connections = list(self._connections.items())
            for _, connection in connections:
                connection.retire()
            self._connections.clear()
        for device_id, connection in connections:
            ownership = DeviceConnectionLease(
                device_id, connection.socket, connection.generation, None, connection
            )
            try:
                await self._close_connection(ownership, code=1001, reason="shutdown")
            finally:
                with suppress(Exception):
                    await self.notify_disconnect(ownership)


class DeviceConversationTransport:
    """Strict conversation controls and binary PCM share the lease's writer."""

    def __init__(
        self, registry: DeviceConnectionRegistry, ownership: DeviceConnectionLease
    ) -> None:
        self._registry = registry
        self._ownership = ownership
        self.conversation_id: UUID | None = None
        self.stream_id: UUID | None = None

    async def send_control(self, message: ConversationOutboundMessage) -> None:
        await self._registry.send_control(self._ownership, message)
        if isinstance(message, ConversationStarted):
            self.conversation_id = message.conversation_id
            self.stream_id = message.stream_id
        elif (
            isinstance(message, ConversationEnded)
            and self.conversation_id == message.conversation_id
        ):
            self.conversation_id = None
            self.stream_id = None

    async def send_audio(self, data: bytes) -> None:
        await self._registry.send_bytes(self._ownership, data)


device_connections = DeviceConnectionRegistry()
