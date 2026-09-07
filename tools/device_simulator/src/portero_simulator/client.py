"""Asynchronous protocol V1 device simulator."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from websockets.asyncio.client import connect as websocket_connect

from portero_simulator.camera import InvalidJpeg, load_jpeg, upload_jpeg
from portero_simulator.config import SimulatorSettings
from portero_simulator.protocol import (
    AuthChallenge,
    AuthOk,
    CommandRequest,
    ProtocolError,
    backoff_delays,
    build_hmac,
    parse_server_message,
)

logger = logging.getLogger(__name__)
REAUTH_MARGIN_SECONDS = 5.0


class WebSocketConnection(Protocol):
    """Small transport surface consumed by one simulator session."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


Sleep = Callable[[float], Awaitable[None]]
MediaUploader = Callable[..., Awaitable[dict[str, object]]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DeviceSimulator:
    """Simulate one device boot across authenticated WebSocket reconnects."""

    def __init__(
        self,
        settings: SimulatorSettings,
        *,
        boot_id: str | None = None,
        connect: Callable[..., Any] = websocket_connect,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utc_now,
        media_uploader: MediaUploader = upload_jpeg,
    ) -> None:
        self.settings = settings
        self.boot_id = boot_id if boot_id is not None else uuid4().hex
        self._connect = connect
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._media_uploader = media_uploader
        self._started_at = monotonic()
        self._seq = -1
        self._send_lock = asyncio.Lock()
        self._command_tasks: set[asyncio.Task[None]] = set()
        self._command_tasks_changed = asyncio.Event()

    def _frame(self, message_type: str, **fields: object) -> dict[str, object]:
        """Allocate the sole sequence number source for every outbound device frame."""

        self._seq += 1
        return {
            "type": message_type,
            "boot_id": self.boot_id,
            "seq": self._seq,
            **fields,
        }

    async def _send(
        self,
        websocket: WebSocketConnection,
        message_type: str,
        **fields: object,
    ) -> None:
        async with self._send_lock:
            frame = self._frame(message_type, **fields)
            await websocket.send(json.dumps(frame, separators=(",", ":"), ensure_ascii=False))

    def _uptime_seconds(self) -> int:
        return max(0, int(self._monotonic() - self._started_at))

    async def _send_status(self, websocket: WebSocketConnection) -> None:
        await self._send(
            websocket,
            "device.status",
            firmware_version=self.settings.firmware_version,
            hardware_model=self.settings.hardware_model,
            uptime_seconds=self._uptime_seconds(),
            ethernet=self.settings.ethernet,
            camera=self.settings.camera,
            microphone=self.settings.microphone,
            speaker=self.settings.speaker,
            free_heap_bytes=self.settings.free_heap_bytes,
        )

    async def _heartbeat_loop(
        self,
        websocket: WebSocketConnection,
        interval_seconds: int,
    ) -> None:
        while True:
            await self._sleep(float(interval_seconds))
            await self._send(
                websocket,
                "device.heartbeat",
                uptime_seconds=self._uptime_seconds(),
            )

    async def _send_ack(
        self,
        websocket: WebSocketConnection,
        command_id: UUID,
        *,
        accepted: bool,
        error_code: str = "INVALID_COMMAND",
    ) -> None:
        fields: dict[str, object] = {
            "command_id": str(command_id),
            "status": "accepted" if accepted else "rejected",
        }
        if not accepted:
            fields["error_code"] = error_code
        await self._send(websocket, "command.ack", **fields)

    async def _send_result(
        self,
        websocket: WebSocketConnection,
        command_id: UUID,
        *,
        result: dict[str, object] | None = None,
        error_code: str | None = None,
    ) -> None:
        fields: dict[str, object] = {
            "command_id": str(command_id),
            "status": "failed" if error_code is not None else "completed",
            "result": result or {},
        }
        if error_code is not None:
            fields["error_code"] = error_code
        await self._send(websocket, "command.result", **fields)

    async def _handle_capture(
        self,
        websocket: WebSocketConnection,
        command: CommandRequest,
        upload_token: str,
        upload_expires_at: datetime,
    ) -> None:
        if self._now() >= upload_expires_at:
            await self._send_result(
                websocket,
                command.command_id,
                error_code="AUTH_FAILED",
            )
            return
        try:
            jpeg = load_jpeg(self.settings.image)
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
                if self._now() >= upload_expires_at:
                    await self._send_result(
                        websocket,
                        command.command_id,
                        error_code="AUTH_FAILED",
                    )
                    return
                media = await self._media_uploader(
                    client,
                    backend_url=self.settings.backend_url,
                    command_id=command.command_id,
                    upload_token=upload_token,
                    jpeg=jpeg,
                )
        except (OSError, InvalidJpeg, httpx.HTTPError, ValueError):
            await self._send_result(
                websocket,
                command.command_id,
                error_code="CAMERA_CAPTURE_FAILED",
            )
            return
        await self._send_result(websocket, command.command_id, result=media)

    async def _execute_command(
        self,
        websocket: WebSocketConnection,
        command: CommandRequest,
        upload_token: str,
        upload_expires_at: datetime,
    ) -> None:
        if command.command == "camera.capture":
            await self._handle_capture(
                websocket,
                command,
                upload_token,
                upload_expires_at,
            )
            return
        await self._send_status(websocket)
        await self._send_result(websocket, command.command_id)

    def _command_finished(self, task: asyncio.Task[None]) -> None:
        self._command_tasks.discard(task)
        self._command_tasks_changed.set()
        if not task.cancelled() and task.exception() is not None:
            logger.error("device %s command execution failed", self.settings.device_id)

    async def _handle_command(
        self,
        websocket: WebSocketConnection,
        command: CommandRequest,
        upload_token: str,
        upload_expires_at: datetime,
        *,
        reauthentication_due: bool = False,
    ) -> None:
        if command.command not in {"camera.capture", "device.status.request"}:
            await self._send_ack(websocket, command.command_id, accepted=False)
            return
        if reauthentication_due or self._now() >= upload_expires_at:
            await self._send_ack(
                websocket,
                command.command_id,
                accepted=False,
                error_code="AUTH_FAILED",
            )
            return

        await self._send_ack(websocket, command.command_id, accepted=True)
        task = asyncio.create_task(
            self._execute_command(
                websocket,
                command,
                upload_token,
                upload_expires_at,
            ),
            name=f"portero-command-{command.command_id}",
        )
        self._command_tasks.add(task)
        task.add_done_callback(self._command_finished)

    async def _serve_commands(
        self,
        websocket: WebSocketConnection,
        authenticated: AuthOk,
    ) -> None:
        seconds_until_refresh = max(
            0.0,
            (authenticated.upload_expires_at - self._now()).total_seconds() - REAUTH_MARGIN_SECONDS,
        )

        async def wait_until_refresh() -> None:
            await self._sleep(seconds_until_refresh)

        refresh: asyncio.Task[None] = asyncio.create_task(
            wait_until_refresh(),
            name="portero-upload-session-refresh",
        )
        iterator = websocket.__aiter__()

        async def receive_next() -> str | bytes:
            return await anext(iterator)

        receive: asyncio.Task[str | bytes] = asyncio.create_task(
            receive_next(),
            name="portero-command-receive",
        )
        command_change: asyncio.Task[bool] | None = None
        reauthentication_due = False
        try:
            while True:
                waiters: set[asyncio.Task[object]] = {receive}
                if not reauthentication_due:
                    waiters.add(refresh)
                else:
                    self._command_tasks_changed.clear()
                    if not self._command_tasks:
                        if not receive.done():
                            return
                    else:
                        command_change = asyncio.create_task(
                            self._command_tasks_changed.wait(),
                            name="portero-command-cleanup-wait",
                        )
                        waiters.add(command_change)
                done, _pending = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if refresh in done:
                    reauthentication_due = True
                if receive in done:
                    try:
                        raw_message = receive.result()
                    except StopAsyncIteration:
                        return
                    message = parse_server_message(raw_message)
                    if not isinstance(message, CommandRequest):
                        raise ProtocolError("expected command.request")
                    await self._handle_command(
                        websocket,
                        message,
                        authenticated.upload_token,
                        authenticated.upload_expires_at,
                        reauthentication_due=reauthentication_due,
                    )
                    receive = asyncio.create_task(
                        receive_next(),
                        name="portero-command-receive",
                    )
                if command_change is not None:
                    if command_change not in done:
                        command_change.cancel()
                        await asyncio.gather(command_change, return_exceptions=True)
                    command_change = None
        finally:
            for task in (receive, refresh, command_change):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (receive, refresh, command_change) if task is not None),
                return_exceptions=True,
            )

    async def run_connection(self, websocket: WebSocketConnection) -> None:
        """Authenticate and serve one already-open WebSocket connection."""

        await self._send(
            websocket,
            "device.hello",
            version="v1",
            device_id=self.settings.device_id,
        )
        challenge = parse_server_message(await websocket.recv())
        if not isinstance(challenge, AuthChallenge):
            raise ProtocolError("expected auth.challenge")
        digest = build_hmac(
            self.settings.device_secret.get_secret_value(),
            self.settings.device_id,
            self.boot_id,
            challenge.nonce,
        )
        await self._send(
            websocket,
            "auth.response",
            nonce=challenge.nonce,
            digest=digest,
        )
        authenticated = parse_server_message(await websocket.recv())
        if not isinstance(authenticated, AuthOk):
            raise ProtocolError("expected auth.ok")

        logger.info("device %s authenticated", self.settings.device_id)
        await self._send_status(websocket)
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(websocket, authenticated.heartbeat_interval_seconds)
        )
        try:
            await self._serve_commands(websocket, authenticated)
        finally:
            heartbeat.cancel()
            command_tasks = tuple(self._command_tasks)
            for task in command_tasks:
                task.cancel()
            await asyncio.gather(heartbeat, *command_tasks, return_exceptions=True)
            self._command_tasks.difference_update(command_tasks)

    async def run_once(self) -> None:
        """Open and serve one backend connection."""

        async with self._connect(
            self.settings.websocket_url,
            max_size=self.settings.max_websocket_message_bytes,
        ) as websocket:
            await self.run_connection(websocket)

    async def run_forever(self) -> None:
        """Reconnect forever using the configured capped V1 schedule."""

        delays = backoff_delays(
            max_seconds=self.settings.reconnect_max_seconds,
            jitter=self.settings.reconnect_jitter,
        )
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            else:
                delays = backoff_delays(
                    max_seconds=self.settings.reconnect_max_seconds,
                    jitter=self.settings.reconnect_jitter,
                )
            delay = next(delays)
            logger.warning(
                "device %s disconnected; reconnecting in %.2f seconds",
                self.settings.device_id,
                delay,
            )
            await self._sleep(delay)
