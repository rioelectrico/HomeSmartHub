"""Home-scoped device provisioning and lifecycle routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import ApplicationSettings, CurrentUser, DatabaseSession
from app.api.errors import APIError
from app.audit.service import write_audit
from app.auth.rbac import PermissionName, require_permission
from app.devices.connections import DeviceConnectionRegistry, device_connections
from app.devices.credentials import provision_device, rotate_device_secret, set_device_enabled
from app.models import Device, DeviceCredential
from app.schemas.devices import (
    DeviceCreateRequest,
    DeviceResponse,
    DevicesResponse,
    ProvisionedDeviceResponse,
)

router = APIRouter(prefix="/api/homes/{home_id}/devices", tags=["devices"])

DevicesReadPermission = Annotated[None, Depends(require_permission(PermissionName.DEVICES_READ))]
DevicesManagePermission = Annotated[
    None, Depends(require_permission(PermissionName.DEVICES_MANAGE))
]


def _connections(request: Request) -> DeviceConnectionRegistry:
    return getattr(request.app.state, "device_connections", device_connections)


DeviceConnections = Annotated[DeviceConnectionRegistry, Depends(_connections)]


def _response(device: Device) -> DeviceResponse:
    return DeviceResponse(
        id=device.id,
        home_id=device.home_id,
        device_id=device.device_id,
        name=device.name,
        status=device.status.value,
        last_seen_at=device.last_seen_at,
        created_at=device.created_at,
        updated_at=device.updated_at,
    )


def _provisioned_response(device: Device, secret: str) -> ProvisionedDeviceResponse:
    return ProvisionedDeviceResponse(**_response(device).model_dump(), secret=secret)


async def _home_device(db: DatabaseSession, home_id: UUID, device_id: UUID) -> Device:
    device = await db.scalar(
        select(Device).where(Device.id == device_id, Device.home_id == home_id)
    )
    if device is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "NOT_FOUND")
    return device


@router.get("", response_model=DevicesResponse)
async def list_devices(
    home_id: UUID,
    db: DatabaseSession,
    _: DevicesReadPermission,
) -> DevicesResponse:
    """List device metadata without credential material."""

    devices = (
        await db.scalars(
            select(Device).where(Device.home_id == home_id).order_by(Device.created_at, Device.id)
        )
    ).all()
    return DevicesResponse(items=[_response(device) for device in devices])


@router.post("", response_model=ProvisionedDeviceResponse, status_code=status.HTTP_201_CREATED)
@router.post(
    "/provision",
    response_model=ProvisionedDeviceResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_device(
    home_id: UUID,
    payload: DeviceCreateRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    settings: ApplicationSettings,
    _: DevicesManagePermission,
) -> ProvisionedDeviceResponse:
    """Register and provision a device, exposing the new secret once."""

    existing = await db.scalar(select(Device.id).where(Device.device_id == payload.device_id))
    if existing is not None:
        raise APIError(status.HTTP_409_CONFLICT, "CONFLICT")
    try:
        provisioned = await provision_device(
            db,
            home_id,
            payload.device_id,
            payload.name,
            settings,
        )
        write_audit(
            db,
            action="device.provisioned",
            user_id=current_user.id,
            home_id=home_id,
            detail="Device registered and provisioned",
            context={"device_id": payload.device_id},
        )
        await db.flush()
        await db.refresh(provisioned.device)
        response = _provisioned_response(provisioned.device, provisioned.secret)
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise APIError(status.HTTP_409_CONFLICT, "CONFLICT") from error
    return response


@router.get("/{device_id}/status", response_model=DeviceResponse)
async def get_device_status(
    home_id: UUID,
    device_id: UUID,
    db: DatabaseSession,
    _: DevicesReadPermission,
) -> DeviceResponse:
    """Return the current durable device snapshot without its secret."""

    return _response(await _home_device(db, home_id, device_id))


@router.post("/{device_id}/provision", response_model=ProvisionedDeviceResponse)
async def provision_existing_device(
    home_id: UUID,
    device_id: UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
    settings: ApplicationSettings,
    registry: DeviceConnections,
    _: DevicesManagePermission,
) -> ProvisionedDeviceResponse:
    """Provision a registered device that does not yet have an active credential."""

    device = await _home_device(db, home_id, device_id)
    active_credential = await db.scalar(
        select(DeviceCredential.id).where(
            DeviceCredential.device_id == device.id,
            DeviceCredential.revoked_at.is_(None),
        )
    )
    if active_credential is not None:
        raise APIError(status.HTTP_409_CONFLICT, "ALREADY_PROVISIONED")
    provisioned = await rotate_device_secret(db, device, settings, registry=registry)
    write_audit(
        db,
        action="device.provisioned",
        user_id=current_user.id,
        home_id=home_id,
        detail="Device credential provisioned",
        context={"device_id": device.device_id},
    )
    await db.flush()
    await db.refresh(device)
    response = _provisioned_response(device, provisioned.secret)
    await db.commit()
    await registry.wait_revocations(device_id)
    return response


@router.post("/{device_id}/rotate", response_model=ProvisionedDeviceResponse)
@router.post(
    "/{device_id}/rotate-secret",
    response_model=ProvisionedDeviceResponse,
    include_in_schema=False,
)
async def rotate_secret(
    home_id: UUID,
    device_id: UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
    settings: ApplicationSettings,
    registry: DeviceConnections,
    _: DevicesManagePermission,
) -> ProvisionedDeviceResponse:
    """Replace the active credential and expose the replacement once."""

    device = await _home_device(db, home_id, device_id)
    rotated = await rotate_device_secret(db, device, settings, registry=registry)
    write_audit(
        db,
        action="device.credential_rotated",
        user_id=current_user.id,
        home_id=home_id,
        detail="Device credential rotated",
        context={"device_id": device.device_id},
    )
    await db.flush()
    await db.refresh(device)
    response = _provisioned_response(device, rotated.secret)
    await db.commit()
    await registry.wait_revocations(device_id)
    return response


async def _set_enabled(
    *,
    db: DatabaseSession,
    home_id: UUID,
    device_id: UUID,
    current_user: CurrentUser,
    enabled: bool,
    registry: DeviceConnectionRegistry,
) -> DeviceResponse:
    device = await _home_device(db, home_id, device_id)
    await set_device_enabled(db, device, enabled=enabled, registry=registry)
    write_audit(
        db,
        action="device.enabled" if enabled else "device.disabled",
        user_id=current_user.id,
        home_id=home_id,
        detail="Device enabled" if enabled else "Device disabled",
        context={"device_id": device.device_id},
    )
    await db.flush()
    await db.refresh(device)
    response = _response(device)
    await db.commit()
    await registry.wait_revocations(device_id)
    return response


@router.post("/{device_id}/enable", response_model=DeviceResponse)
async def enable_device(
    home_id: UUID,
    device_id: UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
    registry: DeviceConnections,
    _: DevicesManagePermission,
) -> DeviceResponse:
    """Enable future authentication for a device."""

    return await _set_enabled(
        db=db,
        home_id=home_id,
        device_id=device_id,
        current_user=current_user,
        enabled=True,
        registry=registry,
    )


@router.post("/{device_id}/disable", response_model=DeviceResponse)
async def disable_device(
    home_id: UUID,
    device_id: UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
    registry: DeviceConnections,
    _: DevicesManagePermission,
) -> DeviceResponse:
    """Disable authentication and retire its current socket and conversation."""

    return await _set_enabled(
        db=db,
        home_id=home_id,
        device_id=device_id,
        current_user=current_user,
        enabled=False,
        registry=registry,
    )
