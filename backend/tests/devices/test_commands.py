"""Durable device command lifecycle contracts."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.devices.commands import (
    CommandPayloadTooLarge,
    CommandReplyRejected,
    CommandResultTooLarge,
    InvalidCommandTransition,
    acknowledge_command,
    complete_command,
    create_and_dispatch_command,
    expire_commands,
    sanitize_command_result,
    transition_command,
    validate_transition,
)
from app.models import AuditLog, CommandStatus, Device, DeviceCommand, Event
from app.schemas.devices import CommandAck, CommandResult


class RecordingRegistry:
    """Small network-boundary fake that records only successful wire sends."""

    def __init__(self, *, available: bool = True, error: Exception | None = None) -> None:
        self.available = available
        self.error = error
        self.sent: list[dict[str, object]] = []

    async def send_json(self, device_id: UUID, payload: dict[str, object]) -> bool:
        if self.error is not None:
            raise self.error
        if not self.available:
            return False
        self.sent.append(payload)
        return True


async def _device(db, home) -> Device:
    device = Device(home_id=home.id, device_id="PI-000001", name="Entrada")
    db.add(device)
    await db.commit()
    return device


def _ack(command_id: UUID, status: str = "accepted") -> CommandAck:
    return CommandAck(
        type="command.ack",
        boot_id="boot-a",
        seq=2,
        command_id=command_id,
        status=status,
        error_code="DEVICE_BUSY" if status == "rejected" else None,
    )


def _result(command_id: UUID, status: str = "completed") -> CommandResult:
    return CommandResult(
        type="command.result",
        boot_id="boot-a",
        seq=3,
        command_id=command_id,
        status=status,
        result={"image_id": "image-1", "token": "must-not-persist"},
        error_code="CAMERA_CAPTURE_FAILED" if status == "failed" else None,
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CommandStatus.PENDING, CommandStatus.COMPLETED),
        (CommandStatus.COMPLETED, CommandStatus.ACKNOWLEDGED),
        (CommandStatus.TIMEOUT, CommandStatus.COMPLETED),
    ],
)
def test_invalid_command_transition_is_rejected(current, target) -> None:
    """Adding permissive transition fallbacks would corrupt the lifecycle."""

    with pytest.raises(InvalidCommandTransition):
        validate_transition(current, target)


def test_transition_assigns_only_the_target_state_timestamp() -> None:
    """Forgetting a state timestamp would make the API timeline ambiguous."""

    command = DeviceCommand(
        device_id=UUID(int=1),
        command="camera.capture",
        payload={},
        status=CommandStatus.PENDING,
    )
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)

    transition_command(command, CommandStatus.SENT, now=now)

    assert command.status == CommandStatus.SENT
    assert command.sent_at == now
    assert command.acknowledged_at is None
    assert command.completed_at is None
    assert command.failed_at is None
    assert command.timeout_at is None


def test_result_is_recursively_sanitized_and_bounded_by_stable_utf8_json() -> None:
    """Nested credentials or oversized device output must not reach storage."""

    sanitized = sanitize_command_result(
        {
            "ok": True,
            "password": "outer",
            "nested": {"access_token": "inner", "value": "visible"},
            "items": [{"api_key": "key", "count": 2}],
        }
    )

    assert sanitized == {
        "items": [{"count": 2}],
        "nested": {"value": "visible"},
        "ok": True,
    }
    with pytest.raises(CommandResultTooLarge):
        sanitize_command_result({"data": "á" * 4_100})


async def test_camera_command_is_flushed_before_exact_wire_send(db, home, user) -> None:
    """Sending a command without its durable UUID would break reply correlation."""

    device = await _device(db, home)
    registry = RecordingRegistry()

    command = await create_and_dispatch_command(
        db, registry, device, "camera.capture", {"quality": 80}, user.id
    )

    assert command.command_id is not None
    assert registry.sent == [
        {
            "type": "command.request",
            "command_id": str(command.id),
            "command": "camera.capture",
            "payload": {"quality": 80},
        }
    ]
    assert command.status == CommandStatus.SENT
    assert command.sent_at is not None
    assert await db.get(DeviceCommand, command.id) is command


async def test_pending_command_and_audit_are_committed_before_transport_send(
    db, async_engine, home, user
) -> None:
    """An immediate device reply must find the command in another DB session."""

    device = await _device(db, home)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    observed: dict[str, object] = {}

    class VisibilityRegistry:
        async def send_json(self, device_id, payload) -> bool:
            async with factory() as observer:
                command = await observer.get(DeviceCommand, UUID(str(payload["command_id"])))
                observed["status"] = command.status if command is not None else None
                observed["audits"] = await observer.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "command.pending")
                )
            return True

    command = await create_and_dispatch_command(
        db, VisibilityRegistry(), device, "camera.capture", {}, user.id
    )

    assert observed == {"status": CommandStatus.PENDING, "audits": 1}
    assert command.status == CommandStatus.SENT


async def test_immediate_ack_inside_send_is_accepted_and_not_overwritten(
    db, async_engine, home, user
) -> None:
    """A real ACK racing the sender return must atomically advance committed pending state."""

    device = await _device(db, home)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    observed_status: CommandStatus | None = None

    class ImmediateAckRegistry:
        async def send_json(self, device_id, payload) -> bool:
            nonlocal observed_status
            async with factory() as reply_session:
                acknowledged = await acknowledge_command(
                    reply_session,
                    device_id,
                    _ack(UUID(str(payload["command_id"]))),
                )
                observed_status = acknowledged.status
            return True

    command = await create_and_dispatch_command(
        db, ImmediateAckRegistry(), device, "camera.capture", {}, user.id
    )

    assert observed_status == CommandStatus.ACKNOWLEDGED
    assert command.status == CommandStatus.ACKNOWLEDGED
    assert command.sent_at is not None
    assert command.acknowledged_at is not None


async def test_immediate_ack_and_result_inside_send_are_not_overwritten(
    db, async_engine, home, user
) -> None:
    """A complete fast device exchange must win over the dispatcher's stale snapshot."""

    device = await _device(db, home)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    class ImmediateResultRegistry:
        async def send_json(self, device_id, payload) -> bool:
            command_id = UUID(str(payload["command_id"]))
            async with factory() as reply_session:
                await acknowledge_command(reply_session, device_id, _ack(command_id))
                await complete_command(reply_session, device_id, _result(command_id))
            return True

    command = await create_and_dispatch_command(
        db, ImmediateResultRegistry(), device, "camera.capture", {}, user.id
    )

    assert command.status == CommandStatus.COMPLETED
    assert command.result == {"image_id": "image-1"}
    assert command.completed_at is not None


async def test_ack_proven_delivery_wins_even_if_send_returns_exception(
    db, async_engine, home, user
) -> None:
    """A post-delivery sender error must not rewrite an already acknowledged command as failed."""

    device = await _device(db, home)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    class AckThenErrorRegistry:
        async def send_json(self, device_id, payload) -> bool:
            async with factory() as reply_session:
                await acknowledge_command(
                    reply_session,
                    device_id,
                    _ack(UUID(str(payload["command_id"]))),
                )
            raise ConnectionError("send completion unavailable")

    command = await create_and_dispatch_command(
        db, AckThenErrorRegistry(), device, "camera.capture", {}, user.id
    )

    assert command.status == CommandStatus.ACKNOWLEDGED
    assert command.failed_at is None


async def test_pending_ack_without_active_transport_is_rejected(db, home) -> None:
    """A pending UUID alone is not proof that its command was delivered."""

    device = await _device(db, home)
    command = DeviceCommand(
        device_id=device.id,
        command="camera.capture",
        payload={},
        status=CommandStatus.PENDING,
    )
    db.add(command)
    await db.commit()
    device_id = device.id
    command_id = command.id

    with pytest.raises(CommandReplyRejected):
        await acknowledge_command(db, device_id, _ack(command_id))

    stored = await db.get(DeviceCommand, command_id)
    assert stored is not None and stored.status == CommandStatus.PENDING


async def test_ack_waits_for_unconfirmed_transport_and_is_rejected_on_send_failure(
    db, async_engine, home, user
) -> None:
    """A concurrent frame cannot turn an in-flight but failed send into delivery proof."""

    device = await _device(db, home)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    command_id: UUID | None = None

    class BlockedFailingRegistry:
        async def send_json(self, _device_id, payload) -> bool:
            nonlocal command_id
            command_id = UUID(str(payload["command_id"]))
            send_started.set()
            await release_send.wait()
            raise ConnectionError("transport failed before confirming delivery")

    dispatch_task = asyncio.create_task(
        create_and_dispatch_command(
            db, BlockedFailingRegistry(), device, "camera.capture", {}, user.id
        )
    )
    await send_started.wait()
    assert command_id is not None

    async def reply_while_send_is_unconfirmed() -> DeviceCommand:
        async with factory() as reply_session:
            return await acknowledge_command(reply_session, device.id, _ack(command_id))

    reply_task = asyncio.create_task(reply_while_send_is_unconfirmed())
    await asyncio.sleep(0.05)
    assert not reply_task.done()

    release_send.set()
    command = await dispatch_task
    with pytest.raises(CommandReplyRejected):
        await reply_task
    assert command.status == CommandStatus.FAILED
    assert command.sent_at is None


async def test_sent_timestamp_is_captured_only_after_awaited_transport(
    db, async_engine, home, user
) -> None:
    """A blocked socket send must leave committed pending state without sent_at."""

    device = await _device(db, home)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingRegistry:
        async def send_json(self, device_id, payload) -> bool:
            started.set()
            await release.wait()
            return True

    task = asyncio.create_task(
        create_and_dispatch_command(db, BlockingRegistry(), device, "camera.capture", {}, user.id)
    )
    await started.wait()
    async with factory() as observer:
        pending = await observer.scalar(
            select(DeviceCommand).where(DeviceCommand.device_id == device.id)
        )
    assert pending is not None
    assert pending.status == CommandStatus.PENDING
    assert pending.sent_at is None

    released_at = datetime.now(UTC)
    release.set()
    command = await task

    assert command.sent_at is not None
    assert command.sent_at >= released_at


async def test_dispatch_outcome_survives_caller_rollback(db, async_engine, home, user) -> None:
    """Later response serialization failure must not erase the sent command or audit."""

    device = await _device(db, home)
    command = await create_and_dispatch_command(
        db, RecordingRegistry(), device, "camera.capture", {}, user.id
    )
    command_id = command.id

    await db.rollback()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as observer:
        stored = await observer.get(DeviceCommand, command_id)
        actions = set(
            await observer.scalars(
                select(AuditLog.action).where(
                    AuditLog.action.in_(["command.pending", "command.sent"])
                )
            )
        )

    assert stored is not None and stored.status == CommandStatus.SENT
    assert actions == {"command.pending", "command.sent"}


async def test_oversized_request_payload_is_rejected_before_persistence_or_send(
    db, home, user
) -> None:
    """Unbounded command parameters must never enter PostgreSQL or the socket."""

    device = await _device(db, home)
    registry = RecordingRegistry()

    with pytest.raises(CommandPayloadTooLarge):
        await create_and_dispatch_command(
            db,
            registry,
            device,
            "camera.capture",
            {"data": "á" * 4_100},
            user.id,
        )

    assert registry.sent == []
    assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 0


@pytest.mark.parametrize(
    "registry",
    [RecordingRegistry(available=False), RecordingRegistry(error=ConnectionError("closed"))],
)
async def test_offline_or_broken_send_marks_flushed_command_failed(
    db, async_engine, home, user, registry
) -> None:
    """A failed transport must leave a queryable terminal command, not pending work."""

    device = await _device(db, home)

    command = await create_and_dispatch_command(
        db, registry, device, "device.status.request", {}, user.id
    )

    assert command.command_id is not None
    assert command.status == CommandStatus.FAILED
    assert command.sent_at is None
    assert command.failed_at is not None
    assert command.result is None
    command_id = command.id
    await db.rollback()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as observer:
        stored = await observer.get(DeviceCommand, command_id)
    assert stored is not None and stored.status == CommandStatus.FAILED


async def test_ack_and_result_are_idempotent_but_out_of_order_result_is_rejected(
    db, home, user
) -> None:
    """Replayed or reordered wire frames must not skip required lifecycle states."""

    device = await _device(db, home)
    command = await create_and_dispatch_command(
        db, RecordingRegistry(), device, "camera.capture", {}, user.id
    )
    device_id = device.id
    command_id = command.id
    with pytest.raises(CommandReplyRejected) as premature:
        await complete_command(db, device_id, _result(command_id))
    assert premature.value.code == "INVALID_COMMAND_REPLY"

    first_ack = await acknowledge_command(db, device_id, _ack(command_id))
    second_ack = await acknowledge_command(db, device_id, _ack(command_id))
    assert first_ack is not None and first_ack.status == CommandStatus.ACKNOWLEDGED
    assert second_ack is not None and second_ack.status == CommandStatus.ACKNOWLEDGED

    first_result = await complete_command(db, device_id, _result(command_id))
    second_result = await complete_command(db, device_id, _result(command_id))
    assert first_result is not None and first_result.status == CommandStatus.COMPLETED
    assert second_result is not None and second_result.status == CommandStatus.COMPLETED
    assert first_result.result == {"image_id": "image-1"}
    transitions = await db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.event_type.in_(["command_acknowledged", "command_completed"]))
    )
    assert transitions == 2


@pytest.mark.parametrize(
    "handler,message_factory", [(acknowledge_command, _ack), (complete_command, _result)]
)
async def test_unknown_or_wrong_device_reply_is_rejected_without_mutation(
    db, home, user, handler, message_factory
) -> None:
    """Reply correlation must require both an existing UUID and its owning device."""

    device = await _device(db, home)
    other = Device(home_id=home.id, device_id="PI-000002", name="Cochera")
    db.add(other)
    await db.commit()
    command = await create_and_dispatch_command(
        db, RecordingRegistry(), device, "camera.capture", {}, user.id
    )
    device_id = device.id
    other_id = other.id
    command_id = command.id

    with pytest.raises(CommandReplyRejected):
        await handler(db, other_id, message_factory(command_id))
    with pytest.raises(CommandReplyRejected):
        await handler(db, device_id, message_factory(uuid4()))

    stored = await db.get(DeviceCommand, command_id)
    assert stored is not None and stored.status == CommandStatus.SENT


@pytest.mark.parametrize("terminal", [CommandStatus.FAILED, CommandStatus.TIMEOUT])
@pytest.mark.parametrize(
    "handler,message_factory", [(acknowledge_command, _ack), (complete_command, _result)]
)
async def test_replies_on_failed_or_timeout_commands_are_rejected(
    db, home, terminal, handler, message_factory
) -> None:
    """Terminal failure states must reject every later device reply."""

    device = await _device(db, home)
    command = DeviceCommand(
        device_id=device.id,
        command="camera.capture",
        payload={},
        status=terminal,
        failed_at=datetime.now(UTC) if terminal == CommandStatus.FAILED else None,
        timeout_at=datetime.now(UTC) if terminal == CommandStatus.TIMEOUT else None,
    )
    db.add(command)
    await db.commit()

    with pytest.raises(CommandReplyRejected):
        await handler(db, device.id, message_factory(command.id))

    await db.refresh(command)
    assert command.status == terminal


async def test_rejected_ack_records_stable_failure_without_device_result(db, home, user) -> None:
    """A rejection must terminate the sent command with its bounded protocol code."""

    device = await _device(db, home)
    command = await create_and_dispatch_command(
        db, RecordingRegistry(), device, "camera.capture", {}, user.id
    )

    rejected = await acknowledge_command(db, device.id, _ack(command.id, "rejected"))

    assert rejected is not None
    assert rejected.status == CommandStatus.FAILED
    assert rejected.result == {"error_code": "DEVICE_BUSY"}
    assert rejected.failed_at is not None


async def test_concurrent_conflicting_results_cannot_overwrite_terminal_state(
    db, async_engine, home, user
) -> None:
    """Row locking must linearize two device results for the same command."""

    device = await _device(db, home)
    command = await create_and_dispatch_command(
        db, RecordingRegistry(), device, "camera.capture", {}, user.id
    )
    await acknowledge_command(db, device.id, _ack(command.id))
    await db.commit()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def finish(message: CommandResult) -> str:
        async with factory() as session:
            try:
                await complete_command(session, device.id, message)
            except CommandReplyRejected:
                return "rejected"
            return "transitioned"

    outcomes = await asyncio.gather(
        finish(_result(command.id, "completed")),
        finish(_result(command.id, "failed")),
    )

    async with factory() as observer:
        stored = await observer.get(DeviceCommand, command.id)
        event_count = await observer.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.event_type.in_(["command_completed", "command_failed"]))
        )
    assert stored is not None
    assert stored.status in {CommandStatus.COMPLETED, CommandStatus.FAILED}
    assert event_count == 1
    assert sorted(outcomes) == ["rejected", "transitioned"]


async def test_expiry_only_times_out_stale_sent_and_acknowledged_commands(
    db, home, user, settings
) -> None:
    """Pending and terminal commands must never be swept by the timeout worker."""

    device = await _device(db, home)
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    stale = now - timedelta(seconds=31)
    commands = [
        DeviceCommand(
            device_id=device.id,
            requested_by_user_id=user.id,
            command="camera.capture",
            status=status,
            sent_at=stale if status != CommandStatus.PENDING else None,
        )
        for status in (
            CommandStatus.PENDING,
            CommandStatus.SENT,
            CommandStatus.ACKNOWLEDGED,
            CommandStatus.COMPLETED,
            CommandStatus.FAILED,
        )
    ]
    fresh = DeviceCommand(
        device_id=device.id,
        command="device.status.request",
        status=CommandStatus.SENT,
        sent_at=now - timedelta(seconds=29),
    )
    db.add_all([*commands, fresh])
    await db.commit()

    settings.command_timeout_seconds = 30
    count = await expire_commands(db, settings, now=now)
    await db.flush()

    assert count == 2
    assert [command.status for command in commands] == [
        CommandStatus.PENDING,
        CommandStatus.TIMEOUT,
        CommandStatus.TIMEOUT,
        CommandStatus.COMPLETED,
        CommandStatus.FAILED,
    ]
    assert fresh.status == CommandStatus.SENT
    assert all(command.timeout_at == now for command in commands[1:3])
    assert (
        await db.scalar(
            select(func.count()).select_from(Event).where(Event.event_type == "command_timeout")
        )
        == 2
    )
    assert (
        await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "command.timeout")
        )
        == 2
    )
