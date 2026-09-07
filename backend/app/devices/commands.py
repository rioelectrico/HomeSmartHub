"""Durable state transitions and dispatch for device commands."""

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit
from app.config import Settings
from app.models import CommandStatus, Device, DeviceCommand, Event
from app.schemas.commands import CommandPayloadTooLarge as CommandPayloadTooLarge
from app.schemas.commands import CommandRequestMessage, validate_command_payload
from app.schemas.devices import MAX_RESULT_BYTES, CommandAck, CommandResult


class InvalidCommandTransition(ValueError):
    """Raised when a command lifecycle edge is not part of protocol V1."""


class UnsupportedCommand(ValueError):
    """Raised when an operator asks for a command disabled in this delivery."""


class CommandResultTooLarge(ValueError):
    """Raised when sanitized result JSON exceeds the durable storage bound."""


class CommandReplyRejected(ValueError):
    """Stable domain error for an uncorrelated or out-of-order device reply."""

    code = "INVALID_COMMAND_REPLY"

    def __init__(self, reason: str) -> None:
        super().__init__(self.code)
        self.reason = reason


class CommandRegistry(Protocol):
    """Network boundary needed by command dispatch."""

    async def send_json(self, device_id: UUID, payload: Mapping[str, object]) -> bool: ...


class _CommandDispatchAttempt:
    """Coordinate a transport outcome with replies arriving during its await."""

    def __init__(self) -> None:
        self.owner_task: object | None = asyncio.current_task()
        self.delivered: bool | None = None
        self.resolved = asyncio.Event()

    def resolve(self, delivered: bool) -> None:
        if self.delivered is None:
            self.delivered = delivered
            self.resolved.set()

    async def pending_reply_proves_delivery(self) -> bool:
        if asyncio.current_task() is self.owner_task:
            # Test transports and equivalent adapters can synchronously surface a
            # correlated device reply from inside send_json. The reply is delivery proof.
            self.resolve(True)
            return True
        await self.resolved.wait()
        return self.delivered is True


class CommandDispatchTracker:
    """Track commands whose owning connection is inside a transport send.

    Connection ownership guarantees that dispatch and replies for a device are handled
    by the same process. Synchronous membership changes make the check atomic between
    event-loop scheduling points; the database row lock provides the durable linearization.
    """

    def __init__(self) -> None:
        self._active: dict[UUID, _CommandDispatchAttempt] = {}

    def begin(self, command_id: UUID) -> _CommandDispatchAttempt:
        if command_id in self._active:
            raise RuntimeError("command dispatch is already active")
        attempt = _CommandDispatchAttempt()
        self._active[command_id] = attempt
        return attempt

    def get(self, command_id: UUID) -> _CommandDispatchAttempt | None:
        return self._active.get(command_id)

    def end(self, command_id: UUID, attempt: _CommandDispatchAttempt) -> None:
        if self._active.get(command_id) is attempt:
            del self._active[command_id]


command_dispatches = CommandDispatchTracker()


VALID_TRANSITIONS = MappingProxyType(
    {
        CommandStatus.PENDING: frozenset({CommandStatus.SENT, CommandStatus.FAILED}),
        CommandStatus.SENT: frozenset(
            {CommandStatus.ACKNOWLEDGED, CommandStatus.FAILED, CommandStatus.TIMEOUT}
        ),
        CommandStatus.ACKNOWLEDGED: frozenset(
            {CommandStatus.COMPLETED, CommandStatus.FAILED, CommandStatus.TIMEOUT}
        ),
        CommandStatus.COMPLETED: frozenset(),
        CommandStatus.FAILED: frozenset(),
        CommandStatus.TIMEOUT: frozenset(),
    }
)


def validate_transition(current: CommandStatus, target: CommandStatus) -> None:
    """Reject every state edge outside the immutable protocol map."""

    if target not in VALID_TRANSITIONS[current]:
        raise InvalidCommandTransition(f"cannot transition command from {current} to {target}")


_SENSITIVE_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
)
_SUPPORTED_COMMANDS: frozenset[str] = frozenset({"camera.capture", "device.status.request"})


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS)


def _sanitize_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def sanitize_command_result(result: Mapping[str, object]) -> dict[str, object]:
    """Recursively redact secrets and enforce a deterministic UTF-8 JSON bound."""

    sanitized = _sanitize_value(result)
    assert isinstance(sanitized, dict)
    try:
        encoded = json.dumps(
            sanitized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("command result must be JSON serializable") from error
    if len(encoded) > MAX_RESULT_BYTES:
        raise CommandResultTooLarge(f"result exceeds {MAX_RESULT_BYTES} bytes")
    return sanitized


def transition_command(
    command: DeviceCommand,
    target: CommandStatus,
    *,
    now: datetime | None = None,
    result: Mapping[str, object] | None = None,
) -> None:
    """Apply one validated state edge and its target timestamp."""

    validate_transition(command.status, target)
    transitioned_at = now if now is not None else datetime.now(UTC)
    command.status = target
    timestamp_fields = {
        CommandStatus.SENT: "sent_at",
        CommandStatus.ACKNOWLEDGED: "acknowledged_at",
        CommandStatus.COMPLETED: "completed_at",
        CommandStatus.FAILED: "failed_at",
        CommandStatus.TIMEOUT: "timeout_at",
    }
    setattr(command, timestamp_fields[target], transitioned_at)
    if result is not None:
        command.result = sanitize_command_result(result)


def _record_transition(
    db: AsyncSession,
    command: DeviceCommand,
    *,
    home_id: UUID,
    status: CommandStatus,
) -> None:
    context: dict[str, object] = {
        "command_id": str(command.id),
        "device_id": str(command.device_id),
        "command": command.command,
        "status": status.value,
    }
    db.add(
        Event(
            home_id=home_id,
            device_id=command.device_id,
            event_type=f"command_{status.value}",
            payload=context,
        )
    )
    write_audit(
        db,
        action=f"command.{status.value}",
        user_id=command.requested_by_user_id,
        home_id=home_id,
        detail=f"Device command transitioned to {status.value}",
        context=context,
    )


async def create_and_dispatch_command(
    db: AsyncSession,
    registry: CommandRegistry,
    device: Device,
    command_name: str,
    payload: Mapping[str, object],
    requested_by_user_id: UUID | None,
    *,
    dispatch_tracker: CommandDispatchTracker = command_dispatches,
) -> DeviceCommand:
    """Commit pending intent before sending, then commit the transport outcome."""

    if command_name not in _SUPPORTED_COMMANDS:
        raise UnsupportedCommand(command_name)
    validated_payload = validate_command_payload(dict(payload))
    entity = DeviceCommand(
        device_id=device.id,
        requested_by_user_id=requested_by_user_id,
        command=command_name,
        payload=validated_payload,
        status=CommandStatus.PENDING,
    )
    db.add(entity)
    await db.flush()
    _record_transition(db, entity, home_id=device.home_id, status=CommandStatus.PENDING)
    await db.commit()
    wire_message = CommandRequestMessage(
        command_id=entity.id,
        command=command_name,
        payload=entity.payload,
    )
    dispatch_attempt = dispatch_tracker.begin(entity.id)
    try:
        try:
            sent = await registry.send_json(
                device.id,
                wire_message.model_dump(mode="json"),
            )
        except Exception:
            sent = False
        dispatch_attempt.resolve(sent)

        # The session identity map can be stale after a reply was committed by a
        # concurrent session. Re-lock and populate the entity before deciding whether
        # the transport outcome still owns the next state transition.
        locked = await _lock_command(db, device.id, entity.id)
        if locked is None:  # The committed command cannot legitimately disappear.
            await db.rollback()
            raise RuntimeError("dispatched command disappeared")
        current, home_id = locked
        if current.status == CommandStatus.PENDING:
            target = CommandStatus.SENT if sent else CommandStatus.FAILED
            transition_command(current, target)
            _record_transition(db, current, home_id=home_id, status=target)
        # An ACK (and potentially RESULT) received during send already established a
        # later durable state under the same row lock. Never overwrite that state,
        # including when the transport coroutine subsequently raises.
        await db.commit()
        return current
    finally:
        # Cancellation or an unexpected service error must release reply waiters as a
        # failed, unconfirmed delivery before retiring the in-process attempt.
        dispatch_attempt.resolve(False)
        dispatch_tracker.end(entity.id, dispatch_attempt)


async def _lock_command(
    db: AsyncSession,
    device_id: UUID,
    command_id: UUID,
) -> tuple[DeviceCommand, UUID] | None:
    row = (
        await db.execute(
            select(DeviceCommand, Device.home_id)
            .join(Device, Device.id == DeviceCommand.device_id)
            .where(DeviceCommand.id == command_id, DeviceCommand.device_id == device_id)
            .with_for_update(of=DeviceCommand)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1]


async def acknowledge_command(
    db: AsyncSession,
    device_id: UUID,
    message: CommandAck,
    *,
    now: datetime | None = None,
    dispatch_tracker: CommandDispatchTracker = command_dispatches,
) -> DeviceCommand:
    """Linearize an ACK, accepting only a true accepted-ACK duplicate."""

    dispatch_attempt = dispatch_tracker.get(message.command_id)
    pending_delivery_proven = (
        await dispatch_attempt.pending_reply_proves_delivery()
        if dispatch_attempt is not None
        else False
    )
    locked = await _lock_command(db, device_id, message.command_id)
    if locked is None:
        await db.rollback()
        raise CommandReplyRejected("unknown_or_wrong_device")
    command, home_id = locked
    if command.status == CommandStatus.PENDING and pending_delivery_proven:
        # A correlated reply received while the owning connection is actively sending
        # is itself proof that delivery happened. Persist the implicit SENT edge and
        # the ACK edge atomically; a bare PENDING command remains ineligible below.
        transition_command(command, CommandStatus.SENT, now=now)
        _record_transition(db, command, home_id=home_id, status=CommandStatus.SENT)

    if message.status == "accepted" and command.status == CommandStatus.SENT:
        transition_command(command, CommandStatus.ACKNOWLEDGED, now=now)
        _record_transition(db, command, home_id=home_id, status=CommandStatus.ACKNOWLEDGED)
        await db.commit()
    elif message.status == "rejected" and command.status == CommandStatus.SENT:
        assert message.error_code is not None
        transition_command(
            command,
            CommandStatus.FAILED,
            now=now,
            result={"error_code": message.error_code},
        )
        _record_transition(db, command, home_id=home_id, status=CommandStatus.FAILED)
        await db.commit()
    elif message.status == "accepted" and command.status == CommandStatus.ACKNOWLEDGED:
        await db.commit()
    else:
        await db.rollback()
        raise CommandReplyRejected("invalid_ack_state")
    return command


async def complete_command(
    db: AsyncSession,
    device_id: UUID,
    message: CommandResult,
    *,
    now: datetime | None = None,
) -> DeviceCommand:
    """Linearize a RESULT, rejecting reordering and non-identical terminal replies."""

    locked = await _lock_command(db, device_id, message.command_id)
    if locked is None:
        await db.rollback()
        raise CommandReplyRejected("unknown_or_wrong_device")
    command, home_id = locked
    result = dict(message.result)
    if message.error_code is not None:
        result["error_code"] = message.error_code
    sanitized_result = sanitize_command_result(result)
    if (
        command.status == CommandStatus.COMPLETED
        and message.status == "completed"
        and command.result == sanitized_result
    ):
        await db.commit()
        return command
    if command.status != CommandStatus.ACKNOWLEDGED:
        await db.rollback()
        raise CommandReplyRejected("invalid_result_state")
    target = CommandStatus.COMPLETED if message.status == "completed" else CommandStatus.FAILED
    transition_command(command, target, now=now, result=sanitized_result)
    _record_transition(db, command, home_id=home_id, status=target)
    await db.commit()
    return command


async def expire_commands(
    db: AsyncSession,
    settings: Settings | None = None,
    *,
    timeout_seconds: int | None = None,
    now: datetime | None = None,
) -> int:
    """Mark only stale sent/acknowledged commands timed out under row locks."""

    if timeout_seconds is None:
        if settings is None:
            raise TypeError("settings or timeout_seconds is required")
        timeout_seconds = settings.command_timeout_seconds
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    checked_at = now if now is not None else datetime.now(UTC)
    cutoff = checked_at - timedelta(seconds=timeout_seconds)
    rows = (
        await db.execute(
            select(DeviceCommand, Device.home_id)
            .join(Device, Device.id == DeviceCommand.device_id)
            .where(
                DeviceCommand.status.in_([CommandStatus.SENT, CommandStatus.ACKNOWLEDGED]),
                DeviceCommand.sent_at <= cutoff,
            )
            .order_by(DeviceCommand.sent_at, DeviceCommand.id)
            .with_for_update(of=DeviceCommand, skip_locked=True)
        )
    ).all()
    for command, home_id in rows:
        transition_command(
            command,
            CommandStatus.TIMEOUT,
            now=checked_at,
        )
        _record_transition(db, command, home_id=home_id, status=CommandStatus.TIMEOUT)
    await db.commit()
    return len(rows)
