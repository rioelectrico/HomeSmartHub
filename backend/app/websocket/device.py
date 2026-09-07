"""Authenticated protocol V1 endpoint for physical devices."""

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.conversation_coordinator import ConversationCoordinator
from app.config import Settings, get_settings
from app.database import get_db
from app.devices.audio_protocol import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    HEADER,
    AudioDirection,
    AudioFrameError,
    decode_audio_frame,
)
from app.devices.challenges import DeviceAuthError, create_challenge, verify_challenge
from app.devices.commands import CommandReplyRejected, acknowledge_command, complete_command
from app.devices.connections import (
    DeviceConnectionLease,
    DeviceConnectionRegistry,
    DeviceConversationTransport,
    DeviceSocket,
    device_connections,
)
from app.devices.presence import (
    mark_offline,
    mark_online,
    record_heartbeat,
    record_status,
    stage_online_for_auth,
)
from app.devices.protocol import parse_device_message
from app.models import Device, DeviceStatus
from app.schemas.conversations import (
    ConversationAudioOutputOverflow,
    ConversationError,
    ConversationErrorCode,
    ConversationStart,
    ConversationStop,
)
from app.schemas.devices import (
    AuthResponse,
    CommandAck,
    CommandResult,
    DeviceHeartbeat,
    DeviceHello,
    DeviceStatusMessage,
)

HANDSHAKE_TIMEOUT_SECONDS = 10.0
CHALLENGE_TTL_SECONDS = 15
UPLOAD_TOKEN_TTL_SECONDS = 300

CLOSE_PROTOCOL_ERROR = (4000, "PROTOCOL_ERROR")
CLOSE_AUTH_FAILED = (4002, "AUTH_FAILED")
CLOSE_VERSION_UNSUPPORTED = (4003, "PROTOCOL_VERSION_UNSUPPORTED")
CLOSE_MESSAGE_TOO_LARGE = (4004, "MESSAGE_TOO_LARGE")
CLOSE_SEQUENCE_INVALID = (4005, "SEQUENCE_INVALID")
CLOSE_HANDSHAKE_TIMEOUT = (4006, "HANDSHAKE_TIMEOUT")
CLOSE_INTERNAL_ERROR = (4500, "INTERNAL_ERROR")

router = APIRouter(tags=["device-websocket"])


class _SocketClosed(Exception):
    pass


class _FrameRejected(Exception):
    def __init__(self, close: tuple[int, str]) -> None:
        self.close = close


async def _close(socket: DeviceSocket, close: tuple[int, str]) -> None:
    with suppress(Exception):
        await socket.close(code=close[0], reason=close[1])


async def _receive_text(
    websocket: WebSocket,
    settings: Settings,
    *,
    timeout: float | None,
) -> str:
    try:
        if timeout is None:
            frame = await websocket.receive()
        else:
            frame = await asyncio.wait_for(websocket.receive(), timeout=timeout)
    except TimeoutError as error:
        raise _FrameRejected(CLOSE_HANDSHAKE_TIMEOUT) from error
    if frame.get("type") == "websocket.disconnect":
        raise _SocketClosed
    text = frame.get("text")
    if not isinstance(text, str):
        raise _FrameRejected(CLOSE_PROTOCOL_ERROR)
    if len(text.encode("utf-8")) > settings.max_websocket_message_bytes:
        raise _FrameRejected(CLOSE_MESSAGE_TOO_LARGE)
    return text


async def _receive_frame(websocket: WebSocket, settings: Settings) -> str | bytes:
    """Accept PAUD only after both text-only handshake reads have succeeded."""
    frame = await websocket.receive()
    if frame.get("type") == "websocket.disconnect":
        raise _SocketClosed
    data = frame.get("bytes")
    if isinstance(data, bytes):
        return data
    text = frame.get("text")
    if not isinstance(text, str):
        raise _FrameRejected(CLOSE_PROTOCOL_ERROR)
    if len(text.encode("utf-8")) > settings.max_websocket_message_bytes:
        raise _FrameRejected(CLOSE_MESSAGE_TOO_LARGE)
    return text


async def _conversation_error(
    transport: DeviceConversationTransport, code: ConversationErrorCode
) -> None:
    await transport.send_control(
        ConversationError(type="conversation.error", version=1, seq=0, code=code)
    )


async def _start_conversation(
    coordinator: ConversationCoordinator,
    ownership: DeviceConnectionLease,
    transport: DeviceConversationTransport,
) -> None:
    # The registry owns disconnect notification. Keep handler cancellation from
    # also triggering coordinator.start's standalone caller cleanup path.
    starting = asyncio.create_task(coordinator.start(ownership.device_id, ownership, transport))

    def finished(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    starting.add_done_callback(finished)
    await asyncio.shield(starting)


async def _receive_audio(
    payload: bytes,
    ownership: DeviceConnectionLease,
    transport: DeviceConversationTransport,
    coordinator: ConversationCoordinator | None,
) -> None:
    code: ConversationErrorCode | None = None
    try:
        if "audio_pcm16_v1" not in ownership.capabilities:
            raise AudioFrameError("INVALID_AUDIO_FRAME")
        if len(payload) > HEADER.size + DEFAULT_MAX_PAYLOAD_BYTES:
            raise AudioFrameError("INVALID_AUDIO_FRAME")
        frame = decode_audio_frame(
            payload, expected_direction=AudioDirection.DEVICE_TO_SERVER, max_payload_bytes=960
        )
        if coordinator is None:
            raise AudioFrameError("STREAM_NOT_FOUND")
        await coordinator.receive_audio(ownership.device_id, ownership, frame)
    except AudioFrameError as error:
        code = error.code
        if code == "STREAM_NOT_FOUND" and transport.stream_id is not None:
            code = "STREAM_NOT_OWNED"
    if code is not None:
        if (
            code == "INVALID_AUDIO_FRAME"
            and coordinator is not None
            and await coordinator.reject_audio(ownership.device_id, ownership)
        ):
            return
        await _conversation_error(transport, code)


def _parse_hello(payload: str) -> DeviceHello:
    try:
        raw = json.loads(payload)
    except (json.JSONDecodeError, RecursionError) as error:
        raise _FrameRejected(CLOSE_PROTOCOL_ERROR) from error
    if (
        isinstance(raw, dict)
        and raw.get("type") == "device.hello"
        and isinstance(raw.get("version"), str)
        and raw["version"] != "v1"
    ):
        raise _FrameRejected(CLOSE_VERSION_UNSUPPORTED)
    try:
        message = parse_device_message(raw)
    except (ValidationError, RecursionError) as error:
        raise _FrameRejected(CLOSE_PROTOCOL_ERROR) from error
    if not isinstance(message, DeviceHello):
        raise _FrameRejected(CLOSE_PROTOCOL_ERROR)
    return message


def _parse_authenticated_message(payload: str) -> Any:
    try:
        return parse_device_message(payload)
    except (ValidationError, RecursionError) as error:
        raise _FrameRejected(CLOSE_PROTOCOL_ERROR) from error


def _scope_ip(websocket: WebSocket) -> str | None:
    client = websocket.scope.get("client")
    if isinstance(client, (tuple, list)) and client and isinstance(client[0], str):
        return client[0]
    return None


async def _release_ownership(
    db: AsyncSession,
    device: Device,
    registry: DeviceConnectionRegistry,
    ownership: DeviceConnectionLease,
) -> None:
    """Revoke one lease and persist offline only when it remains the last owner."""

    removed = await registry.unregister(ownership)
    if removed:
        async with registry.lifecycle(ownership.device_id):
            if await registry.can_mark_offline(ownership):
                await mark_offline(db, device)


async def handle_device_socket(
    websocket: WebSocket,
    db: AsyncSession,
    settings: Settings,
    registry: DeviceConnectionRegistry = device_connections,
    coordinator: ConversationCoordinator | None = None,
) -> None:
    """Run a bounded HMAC handshake followed by the validated device message loop."""

    device: Device | None = None
    ownership: DeviceConnectionLease | None = None
    try:
        await websocket.accept()
        hello = _parse_hello(
            await _receive_text(websocket, settings, timeout=HANDSHAKE_TIMEOUT_SECONDS)
        )
        device = await db.scalar(select(Device).where(Device.device_id == hello.device_id))
        if device is None or device.status == DeviceStatus.DISABLED:
            await db.rollback()
            await _close(websocket, CLOSE_AUTH_FAILED)
            return

        challenge = await create_challenge(
            db,
            device.id,
            hello.boot_id,
            ttl_seconds=CHALLENGE_TTL_SECONDS,
        )
        nonce = challenge.nonce
        expires_at = challenge.expires_at
        await db.commit()
        await websocket.send_json(
            {
                "type": "auth.challenge",
                "algorithm": "HMAC-SHA256",
                "nonce": nonce,
                "expires_at": expires_at.isoformat(),
            }
        )

        auth_payload = await _receive_text(
            websocket,
            settings,
            timeout=HANDSHAKE_TIMEOUT_SECONDS,
        )
        auth_message = _parse_authenticated_message(auth_payload)
        if (
            not isinstance(auth_message, AuthResponse)
            or auth_message.boot_id != hello.boot_id
            or auth_message.seq <= hello.seq
            or auth_message.nonce != nonce
        ):
            await db.rollback()
            await _close(websocket, CLOSE_AUTH_FAILED)
            return
        authorization_epoch = registry.authorization_epoch(device.id)
        try:
            await verify_challenge(
                db,
                device,
                hello.boot_id,
                auth_message.nonce,
                auth_message.digest,
                settings,
            )
        except DeviceAuthError:
            await db.rollback()
            await _close(websocket, CLOSE_AUTH_FAILED)
            return

        upload_token = token_urlsafe(32)
        upload_expires_at = datetime.now(UTC) + timedelta(seconds=UPLOAD_TOKEN_TTL_SECONDS)
        async with registry.lifecycle(device.id):

            async def disconnected(lease: DeviceConnectionLease) -> None:
                if coordinator is not None:
                    await coordinator.disconnect(lease.device_id, lease)

            try:
                ownership = await registry.claim(
                    device.id,
                    websocket,
                    upload_token=upload_token,
                    upload_token_expires_at=upload_expires_at,
                    capabilities=hello.capabilities,
                    on_disconnect=disconnected,
                    authorization_epoch=authorization_epoch,
                )
            except ConnectionError as error:
                raise _FrameRejected(CLOSE_AUTH_FAILED) from error
            ip_address = _scope_ip(websocket)
            await mark_online(
                db, device, ip_address=ip_address, ownership=ownership, registry=registry
            )
        await registry.close_replaced(ownership)
        async with registry.lifecycle(device.id):
            is_current = await registry.is_current(ownership)
        if not is_current:
            return

        is_enabled = await stage_online_for_auth(
            db, device, ip_address=ip_address, ownership=ownership, registry=registry
        )
        if not is_enabled:
            await db.commit()
            await _release_ownership(db, device, registry, ownership)
            await registry.close_owned(
                ownership, code=CLOSE_AUTH_FAILED[0], reason=CLOSE_AUTH_FAILED[1]
            )
            ownership = None
            return
        try:
            auth_ok = {
                "type": "auth.ok",
                "upload_token": upload_token,
                "upload_expires_at": upload_expires_at.isoformat(),
                "heartbeat_interval_seconds": settings.device_heartbeat_interval,
            }
            del upload_token
            await registry.send_json_to(ownership, auth_ok)
            del auth_ok
            await db.commit()
        except asyncio.CancelledError:
            await asyncio.shield(db.rollback())
            raise
        except Exception:
            await db.rollback()
            raise
        last_seq = auth_message.seq
        transport = DeviceConversationTransport(registry, ownership)
        while True:
            payload = await _receive_frame(websocket, settings)
            if not await registry.is_current(ownership):
                return
            if isinstance(payload, bytes):
                await _receive_audio(payload, ownership, transport, coordinator)
                continue
            message = _parse_authenticated_message(payload)
            if message.boot_id != hello.boot_id or message.seq <= last_seq:
                raise _FrameRejected(CLOSE_SEQUENCE_INVALID)
            last_seq = message.seq
            if isinstance(message, ConversationStart):
                if "audio_pcm16_v1" not in ownership.capabilities:
                    await _conversation_error(transport, "INVALID_AUDIO_FRAME")
                elif coordinator is None:
                    await _conversation_error(transport, "AI_UNAVAILABLE")
                else:
                    await _start_conversation(coordinator, ownership, transport)
            elif isinstance(message, ConversationStop):
                if message.conversation_id != transport.conversation_id or coordinator is None:
                    await _conversation_error(transport, "CONVERSATION_NOT_ACTIVE")
                else:
                    await coordinator.stop(device.id, ownership, message.reason)
            elif isinstance(message, ConversationAudioOutputOverflow):
                if "audio_pcm16_v1" not in ownership.capabilities:
                    await _conversation_error(transport, "INVALID_AUDIO_FRAME")
                elif coordinator is None:
                    await _conversation_error(transport, "CONVERSATION_NOT_ACTIVE")
                else:
                    try:
                        await coordinator.report_output_overflow(
                            device.id, ownership, message.conversation_id, message.stream_id
                        )
                    except AudioFrameError as error:
                        await _conversation_error(transport, error.code)
            elif isinstance(message, DeviceHeartbeat):
                await record_heartbeat(
                    db,
                    device,
                    uptime_seconds=message.uptime_seconds,
                    ip_address=ip_address,
                    ownership=ownership,
                    registry=registry,
                )
            elif isinstance(message, DeviceStatusMessage):
                await record_status(
                    db,
                    device,
                    message,
                    ip_address=ip_address,
                    ownership=ownership,
                    registry=registry,
                )
            elif isinstance(message, CommandAck):
                try:
                    await acknowledge_command(db, device.id, message)
                except CommandReplyRejected as error:
                    raise _FrameRejected(CLOSE_PROTOCOL_ERROR) from error
                await mark_online(
                    db, device, ip_address=ip_address, ownership=ownership, registry=registry
                )
            elif isinstance(message, CommandResult):
                try:
                    await complete_command(db, device.id, message)
                except CommandReplyRejected as error:
                    raise _FrameRejected(CLOSE_PROTOCOL_ERROR) from error
                await mark_online(
                    db, device, ip_address=ip_address, ownership=ownership, registry=registry
                )
            else:
                raise _FrameRejected(CLOSE_PROTOCOL_ERROR)
            if device.status == DeviceStatus.DISABLED:
                raise _FrameRejected(CLOSE_AUTH_FAILED)
    except _SocketClosed:
        pass
    except _FrameRejected as error:
        await db.rollback()
        if ownership is None:
            await _close(websocket, error.close)
        else:
            await registry.close_owned(ownership, code=error.close[0], reason=error.close[1])
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        if ownership is not None:
            await registry.close_owned(ownership)
        raise
    except Exception:
        await db.rollback()
        if ownership is None:
            await _close(websocket, CLOSE_INTERNAL_ERROR)
        else:
            await registry.close_owned(
                ownership, code=CLOSE_INTERNAL_ERROR[0], reason=CLOSE_INTERNAL_ERROR[1]
            )
    finally:
        if ownership is not None and device is not None:
            try:
                await _release_ownership(db, device, registry, ownership)
            except Exception:
                await db.rollback()


@router.websocket("/ws/device")
async def device_socket(
    websocket: WebSocket,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """FastAPI route adapter for the device socket protocol."""

    await handle_device_socket(
        websocket,
        db,
        settings,
        websocket.app.state.device_connections,
        websocket.app.state.conversation_coordinator,
    )
