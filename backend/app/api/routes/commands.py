"""Home- and device-scoped command control routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DatabaseSession
from app.api.errors import APIError
from app.auth.rbac import PermissionName, require_permission
from app.devices.commands import (
    CommandResultTooLarge,
    UnsupportedCommand,
    create_and_dispatch_command,
    sanitize_command_result,
)
from app.devices.connections import device_connections
from app.models import CommandStatus, Device, DeviceCommand
from app.schemas.commands import (
    CommandCreateRequest,
    CommandResponse,
    CommandsResponse,
    CommandTimelineEntry,
)

router = APIRouter(prefix="/api/homes/{home_id}/devices/{device_id}/commands", tags=["commands"])

DevicesControlPermission = Annotated[
    None, Depends(require_permission(PermissionName.DEVICES_CONTROL))
]


async def _home_device(db: DatabaseSession, home_id: UUID, device_id: UUID) -> Device:
    device = await db.scalar(
        select(Device).where(Device.id == device_id, Device.home_id == home_id)
    )
    if device is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "NOT_FOUND")
    return device


def _timeline(command: DeviceCommand) -> list[CommandTimelineEntry]:
    entries = [CommandTimelineEntry(status=CommandStatus.PENDING, at=command.created_at)]
    for command_status, timestamp in (
        (CommandStatus.SENT, command.sent_at),
        (CommandStatus.ACKNOWLEDGED, command.acknowledged_at),
        (CommandStatus.COMPLETED, command.completed_at),
        (CommandStatus.FAILED, command.failed_at),
        (CommandStatus.TIMEOUT, command.timeout_at),
    ):
        if timestamp is not None:
            entries.append(CommandTimelineEntry(status=command_status, at=timestamp))
    return sorted(entries, key=lambda entry: entry.at)


def command_response(command: DeviceCommand) -> CommandResponse:
    """Serialize a command while redacting recursively sensitive device data."""

    try:
        payload = sanitize_command_result(command.payload)
        result = sanitize_command_result(command.result) if command.result is not None else None
    except CommandResultTooLarge as error:
        raise APIError(status.HTTP_500_INTERNAL_SERVER_ERROR, "INVALID_COMMAND_RESULT") from error
    return CommandResponse(
        command_id=command.id,
        device_id=command.device_id,
        requested_by_user_id=command.requested_by_user_id,
        command=command.command,
        payload=payload,
        status=command.status,
        result=result,
        created_at=command.created_at,
        timeline=_timeline(command),
    )


@router.post("", response_model=CommandResponse, status_code=status.HTTP_201_CREATED)
async def create_command(
    home_id: UUID,
    device_id: UUID,
    payload: CommandCreateRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    _: DevicesControlPermission,
) -> CommandResponse:
    """Create and immediately dispatch one supported command."""

    device = await _home_device(db, home_id, device_id)
    try:
        command = await create_and_dispatch_command(
            db,
            device_connections,
            device,
            payload.command,
            payload.payload,
            current_user.id,
        )
    except UnsupportedCommand as error:
        raise APIError(status.HTTP_400_BAD_REQUEST, "INVALID_COMMAND") from error
    response = command_response(command)
    return response


@router.get("", response_model=CommandsResponse)
async def list_commands(
    home_id: UUID,
    device_id: UUID,
    db: DatabaseSession,
    _: DevicesControlPermission,
) -> CommandsResponse:
    """List a device's command history newest first."""

    await _home_device(db, home_id, device_id)
    commands = (
        await db.scalars(
            select(DeviceCommand)
            .where(DeviceCommand.device_id == device_id)
            .order_by(DeviceCommand.created_at.desc(), DeviceCommand.id.desc())
        )
    ).all()
    return CommandsResponse(items=[command_response(command) for command in commands])


@router.get("/{command_id}", response_model=CommandResponse)
async def get_command(
    home_id: UUID,
    device_id: UUID,
    command_id: UUID,
    db: DatabaseSession,
    _: DevicesControlPermission,
) -> CommandResponse:
    """Return one command only inside its home and device scope."""

    command = await db.scalar(
        select(DeviceCommand)
        .join(Device, Device.id == DeviceCommand.device_id)
        .where(
            DeviceCommand.id == command_id,
            DeviceCommand.device_id == device_id,
            Device.home_id == home_id,
        )
    )
    if command is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "NOT_FOUND")
    return command_response(command)
