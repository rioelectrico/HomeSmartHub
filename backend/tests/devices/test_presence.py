"""Durable device presence and status transition contracts."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.devices.presence import (
    mark_offline,
    mark_online,
    mark_stale_devices_offline,
    record_heartbeat,
    record_status,
)
from app.models import Device, DeviceStatus, Event
from app.schemas.devices import DeviceStatusMessage


async def _device(db, home, *, status: DeviceStatus = DeviceStatus.OFFLINE) -> Device:
    device = Device(home_id=home.id, device_id="PI-000001", name="Entrada", status=status)
    db.add(device)
    await db.commit()
    return device


async def test_online_transition_emits_one_event(db, home) -> None:
    """Repeated heartbeats must not create repeated online events."""

    device = await _device(db, home)

    await mark_online(db, device, ip_address="192.0.2.10")
    await record_heartbeat(db, device, uptime_seconds=9, ip_address="192.0.2.10")

    events = (await db.scalars(select(Event).where(Event.event_type == "device_online"))).all()
    assert device.status == DeviceStatus.ONLINE
    assert device.last_seen_at is not None
    assert len(events) == 1
    assert events[0].payload == {"ip_address": "192.0.2.10"}


async def test_offline_transition_emits_one_event(db, home, settings) -> None:
    """A periodic sweep must emit exactly once for one ONLINE-to-OFFLINE transition."""

    device = await _device(db, home, status=DeviceStatus.ONLINE)
    device.last_seen_at = datetime.now(UTC) - timedelta(seconds=settings.device_offline_timeout + 1)
    await db.commit()

    assert await mark_stale_devices_offline(db, settings) == 1
    assert await mark_stale_devices_offline(db, settings) == 0
    count = await db.scalar(
        select(func.count()).select_from(Event).where(Event.event_type == "device_offline")
    )
    assert count == 1


async def test_status_records_server_ip_and_snapshot(db, home) -> None:
    """Dropping diagnostics or accepting a payload IP would corrupt the durable status snapshot."""

    device = await _device(db, home)
    message = DeviceStatusMessage.model_validate(
        {
            "type": "device.status",
            "boot_id": "boot-a",
            "seq": 2,
            "firmware_version": "1.0.0",
            "hardware_model": "ESP32-P4",
            "uptime_seconds": 12,
            "ethernet": "online",
            "camera": "ready",
            "microphone": "unavailable",
            "speaker": "ready",
            "free_heap_bytes": 4096,
        }
    )

    await record_status(db, device, message, ip_address="198.51.100.8")

    event = await db.scalar(select(Event).where(Event.event_type == "device_status"))
    assert event is not None
    assert event.payload == {
        "ip_address": "198.51.100.8",
        "firmware_version": "1.0.0",
        "hardware_model": "ESP32-P4",
        "uptime_seconds": 12,
        "ethernet": "online",
        "camera": "ready",
        "microphone": "unavailable",
        "speaker": "ready",
        "free_heap_bytes": 4096,
    }
    online_count = await db.scalar(
        select(func.count()).select_from(Event).where(Event.event_type == "device_online")
    )
    assert online_count == 1


async def test_heartbeat_refreshes_status_changed_by_another_session(
    db, async_engine, home
) -> None:
    """A cached ONLINE object must not hide a durable OFFLINE-to-ONLINE recovery."""

    device = await _device(db, home, status=DeviceStatus.ONLINE)
    device_id = device.id
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as sweeper:
        stored = await sweeper.get(Device, device_id)
        assert stored is not None
        stored.status = DeviceStatus.OFFLINE
        await sweeper.commit()

    transitioned = await record_heartbeat(db, device, uptime_seconds=15)

    assert transitioned
    await db.refresh(device)
    assert device.status == DeviceStatus.ONLINE
    online_count = await db.scalar(
        select(func.count()).select_from(Event).where(Event.event_type == "device_online")
    )
    assert online_count == 1


async def test_disconnect_does_not_duplicate_offline_event_from_another_session(
    db, async_engine, home
) -> None:
    """A cached ONLINE object must not emit a second event for an existing transition."""

    device = await _device(db, home, status=DeviceStatus.ONLINE)
    device_id = device.id
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as sweeper:
        stored = await sweeper.get(Device, device_id)
        assert stored is not None
        stored.status = DeviceStatus.OFFLINE
        sweeper.add(
            Event(
                home_id=stored.home_id,
                device_id=stored.id,
                event_type="device_offline",
                payload={},
            )
        )
        await sweeper.commit()

    assert not await mark_offline(db, device)
    offline_count = await db.scalar(
        select(func.count()).select_from(Event).where(Event.event_type == "device_offline")
    )
    assert offline_count == 1
