"""Bounded device diagnostics with latest status snapshots selected in PostgreSQL."""

import asyncio
from uuid import UUID

from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.ai.provider import AIRealtimeProvider
from app.devices.connections import DeviceConnectionRegistry
from app.models import Device, Event
from app.schemas.diagnostics import (
    DeviceDiagnostic,
    DiagnosticDevices,
    DiagnosticsQuery,
    DiagnosticsResponse,
    ServiceStatus,
)
from app.schemas.events import decode_cursor, public_context

READINESS_TIMEOUT_SECONDS = 3.0


async def postgresql_ready(engine: AsyncEngine) -> bool:
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def diagnostics(
    db: AsyncSession,
    home_id: UUID,
    query: DiagnosticsQuery,
    provider: AIRealtimeProvider,
    registry: DeviceConnectionRegistry,
) -> DiagnosticsResponse:
    statement = select(Device).where(Device.home_id == home_id)
    if query.cursor is not None:
        statement = statement.where(
            tuple_(Device.created_at, Device.id) < decode_cursor(query.cursor)
        )
    if query.date_from is not None:
        statement = statement.where(Device.created_at >= query.date_from)
    if query.date_to is not None:
        statement = statement.where(Device.created_at < query.date_to)
    rows = (
        await db.scalars(
            statement.order_by(Device.created_at.desc(), Device.id.desc()).limit(query.limit + 1)
        )
    ).all()
    page = rows[: query.limit]
    snapshots = (
        (
            await db.scalars(
                select(Event)
                .where(
                    Event.home_id == home_id,
                    Event.device_id.in_([device.id for device in page]),
                    Event.event_type == "device_status",
                )
                .distinct(Event.device_id)
                .order_by(Event.device_id, Event.created_at.desc(), Event.id.desc())
            )
        ).all()
        if page
        else []
    )
    by_device = {snapshot.device_id: snapshot for snapshot in snapshots}
    items = []
    for device in page:
        snapshot = by_device.get(device.id)
        items.append(
            DeviceDiagnostic(
                id=device.id,
                device_id=device.device_id,
                name=device.name,
                status=device.status,
                connected=registry.get(device.id) is not None,
                last_seen_at=device.last_seen_at,
                snapshot_at=snapshot.created_at if snapshot else None,
                snapshot=public_context(snapshot.payload) if snapshot else None,
            )
        )
    return DiagnosticsResponse(
        services=ServiceStatus(postgresql="up", openai=provider.diagnose()),
        devices=DiagnosticDevices(
            items=items,
            next_cursor=f"{page[-1].created_at.isoformat()}|{page[-1].id}"
            if len(rows) > query.limit
            else None,
        ),
    )
