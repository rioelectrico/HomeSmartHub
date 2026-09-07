"""Durable device presence and diagnostic status transitions."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.devices.connections import (
    DeviceConnectionLease,
    DeviceConnectionRegistry,
    device_connections,
)
from app.models import Device, DeviceStatus, Event
from app.schemas.devices import DeviceStatusMessage


def _event(
    device: Device,
    event_type: str,
    payload: dict[str, object] | None = None,
) -> Event:
    return Event(
        home_id=device.home_id,
        device_id=device.id,
        event_type=event_type,
        payload=payload or {},
    )


def _transition_online(
    db: AsyncSession,
    device: Device,
    *,
    now: datetime,
    ip_address: str | None,
) -> bool:
    transitioned = device.status != DeviceStatus.ONLINE
    device.status = DeviceStatus.ONLINE
    device.last_seen_at = now
    if transitioned:
        payload: dict[str, object] = {}
        if ip_address is not None:
            payload["ip_address"] = ip_address
        db.add(_event(device, "device_online", payload))
    return transitioned


async def _locked_device(db: AsyncSession, device: Device) -> Device:
    stored = await db.scalar(
        select(Device)
        .where(Device.id == device.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if stored is None:
        raise LookupError("device no longer exists")
    return stored


async def mark_online(
    db: AsyncSession,
    device: Device,
    *,
    ip_address: str | None = None,
    ownership: DeviceConnectionLease | None = None,
    registry: DeviceConnectionRegistry = device_connections,
) -> bool:
    """Mark a device seen and emit only an actual online transition."""

    stored = await _locked_device(db, device)
    if stored.status == DeviceStatus.DISABLED or (
        ownership is not None and not await registry.is_current(ownership)
    ):
        await db.commit()
        return False
    transitioned = _transition_online(
        db,
        stored,
        now=datetime.now(UTC),
        ip_address=ip_address,
    )
    await db.commit()
    return transitioned


async def stage_online_for_auth(
    db: AsyncSession,
    device: Device,
    *,
    ip_address: str | None = None,
    ownership: DeviceConnectionLease | None = None,
    registry: DeviceConnectionRegistry = device_connections,
) -> bool:
    """Lock and refresh presence while leaving the auth transaction open.

    The caller must commit only after sending the success frame, or roll back on
    failure, so a concurrent disable cannot commit between the enabled check and
    that frame.
    """

    stored = await _locked_device(db, device)
    if stored.status == DeviceStatus.DISABLED or (
        ownership is not None and not await registry.is_current(ownership)
    ):
        return False
    _transition_online(
        db,
        stored,
        now=datetime.now(UTC),
        ip_address=ip_address,
    )
    return True


async def record_heartbeat(
    db: AsyncSession,
    device: Device,
    *,
    uptime_seconds: int,
    ip_address: str | None = None,
    ownership: DeviceConnectionLease | None = None,
    registry: DeviceConnectionRegistry = device_connections,
) -> bool:
    """Refresh last-seen without storing one row per heartbeat."""

    # Uptime is validated by the protocol schema. It remains an explicit
    # argument so callers cannot accidentally bypass that contract.
    del uptime_seconds
    return await mark_online(
        db, device, ip_address=ip_address, ownership=ownership, registry=registry
    )


async def record_status(
    db: AsyncSession,
    device: Device,
    message: DeviceStatusMessage,
    *,
    ip_address: str | None,
    ownership: DeviceConnectionLease | None = None,
    registry: DeviceConnectionRegistry = device_connections,
) -> None:
    """Atomically persist presence plus the latest diagnostic snapshot event."""

    stored = await _locked_device(db, device)
    if stored.status == DeviceStatus.DISABLED or (
        ownership is not None and not await registry.is_current(ownership)
    ):
        await db.commit()
        return
    _transition_online(db, stored, now=datetime.now(UTC), ip_address=ip_address)
    payload: dict[str, object] = {
        "firmware_version": message.firmware_version,
        "hardware_model": message.hardware_model,
        "uptime_seconds": message.uptime_seconds,
        "ethernet": message.ethernet,
        "camera": message.camera,
        "microphone": message.microphone,
        "speaker": message.speaker,
        "free_heap_bytes": message.free_heap_bytes,
    }
    if ip_address is not None:
        payload["ip_address"] = ip_address
    db.add(_event(stored, "device_status", payload))
    await db.commit()


async def mark_offline(db: AsyncSession, device: Device) -> bool:
    """Mark one connected device offline, emitting only a real transition."""

    stored = await _locked_device(db, device)
    if stored.status != DeviceStatus.ONLINE:
        await db.commit()
        return False
    stored.status = DeviceStatus.OFFLINE
    db.add(_event(stored, "device_offline"))
    await db.commit()
    return True


async def mark_stale_devices_offline(
    db: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """Transition stale online devices once; intended for a periodic lifecycle task."""

    checked_at = now if now is not None else datetime.now(UTC)
    cutoff = checked_at - timedelta(seconds=settings.device_offline_timeout)
    stale = (
        await db.scalars(
            select(Device)
            .where(
                Device.status == DeviceStatus.ONLINE,
                Device.last_seen_at.is_not(None),
                Device.last_seen_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for device in stale:
        device.status = DeviceStatus.OFFLINE
        db.add(_event(device, "device_offline"))
    await db.commit()
    return len(stale)
