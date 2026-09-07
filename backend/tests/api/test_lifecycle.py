"""Real PostgreSQL maintenance, per-iteration recovery, and resource shutdown."""

import asyncio
import importlib.util
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.devices import presence
from app.devices.connections import device_connections
from app.models import CommandStatus, Device, DeviceCommand, DeviceStatus


class ShutdownSocket:
    def __init__(self):
        self.closed = False

    async def close(self, code=1000, reason=None):
        self.closed = True

    async def send_json(self, data):
        pass


async def test_lifespan_runs_maintenance_recovers_and_shuts_down(
    application, db, home, monkeypatch, caplog
):
    settings = application.state.settings
    settings.device_heartbeat_interval = 1
    settings.device_offline_timeout = 2
    settings.command_timeout_seconds = 1
    old = datetime.now(UTC) - timedelta(minutes=10)
    device = Device(
        home_id=home.id,
        device_id="PI-000001",
        name="Entry",
        status=DeviceStatus.ONLINE,
        last_seen_at=old,
    )
    db.add(device)
    await db.flush()
    command = DeviceCommand(
        device_id=device.id, command="device.status.request", status=CommandStatus.SENT, sent_at=old
    )
    db.add(command)
    await db.commit()
    original = presence.mark_stale_devices_offline
    attempts = 0

    async def intermittent(session, configured):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("do-not-log-secret-database-url")
        return await original(session, configured)

    monkeypatch.setattr(presence, "mark_stale_devices_offline", intermittent)
    socket = ShutdownSocket()
    ownership = await device_connections.register(
        device.id,
        socket,
        upload_token="private-upload",
        upload_token_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    pool = application.state.engine.pool
    try:
        async with application.router.lifespan_context(application):
            assert hasattr(application.state, "maintenance_tasks"), (
                "lifespan maintenance is missing"
            )
            tasks = application.state.maintenance_tasks
            assert len(tasks) == 2
            for _ in range(40):
                await asyncio.sleep(0.1)
                await db.refresh(device)
                await db.refresh(command)
                if (
                    device.status == DeviceStatus.OFFLINE
                    and command.status == CommandStatus.TIMEOUT
                ):
                    break
            assert device.status == DeviceStatus.OFFLINE
            assert command.status == CommandStatus.TIMEOUT
            assert all(not task.done() for task in tasks)
        assert all(task.cancelled() for task in tasks)
        assert socket.closed
        assert not await device_connections.verify_upload_token(device.id, "private-upload")
        assert application.state.engine.pool is not pool
        failed = [
            record for record in caplog.records if record.msg == "maintenance_iteration_failed"
        ]
        assert failed and failed[0].operation == "presence_offline"
        assert UUID(failed[0].correlation_id)
        assert failed[0].exc_info is None
        assert "do-not-log-secret" not in caplog.text
    finally:
        await device_connections.unregister(ownership)
        await application.state.engine.dispose()


@pytest.mark.parametrize("production", [False, True])
def test_log_format_excludes_unapproved_context_and_tracebacks(production):
    assert importlib.util.find_spec("app.logging") is not None, "safe logging is missing"
    from app.logging import SafeFormatter

    record = logging.LogRecord(
        "portero", logging.ERROR, __file__, 1, "maintenance_iteration_failed", (), None
    )
    record.correlation_id = "79cd1515-59d4-47bf-a679-f2c3f210eaf6"
    record.operation = "presence_offline"
    record.device_id = "79cd1515-59d4-47bf-a679-f2c3f210eaf6"
    record.password = "password-secret"
    record.cookies = "cookie-secret"
    record.api_key = "api-key-secret"
    record.exc_text = "Traceback with secret values"
    rendered = SafeFormatter(production=production).format(record)
    assert "secret" not in rendered and "Traceback" not in rendered
    assert "presence_offline" in rendered and record.correlation_id in rendered
    if production:
        result = json.loads(rendered)
        assert result["event"] == "maintenance_iteration_failed"
        assert result["device_id"] == record.device_id


async def test_shutdown_cannot_be_blocked_by_an_unresponsive_socket(application, home, monkeypatch):
    import app.main as main

    class StalledSocket(ShutdownSocket):
        async def close(self, code=1000, reason=None):
            await asyncio.sleep(10)

    monkeypatch.setattr(main, "SHUTDOWN_TIMEOUT_SECONDS", 0.02, raising=False)
    socket = StalledSocket()
    ownership = await device_connections.register(home.id, socket)
    lifespan = application.router.lifespan_context(application)
    pool = application.state.engine.pool
    try:
        await lifespan.__aenter__()
        await asyncio.wait_for(lifespan.__aexit__(None, None, None), timeout=0.2)
        assert application.state.engine.pool is not pool
        assert device_connections.get(home.id) is None
    finally:
        await device_connections.unregister(ownership)
        await application.state.engine.dispose()


async def test_shutdown_is_bounded_when_maintenance_ignores_initial_cancellation(
    application, monkeypatch
):
    import app.main as main

    started = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_maintenance(session, settings):
        del session, settings
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return 0

    async def idle_maintenance(session, settings):
        del session, settings
        await asyncio.sleep(10)
        return 0

    monkeypatch.setattr(presence, "mark_stale_devices_offline", stubborn_maintenance)
    monkeypatch.setattr(main.commands, "expire_commands", idle_maintenance)
    monkeypatch.setattr(main, "SHUTDOWN_TIMEOUT_SECONDS", 0.02, raising=False)
    lifespan = application.router.lifespan_context(application)
    try:
        await lifespan.__aenter__()
        await asyncio.wait_for(started.wait(), timeout=0.2)
        await asyncio.wait_for(lifespan.__aexit__(None, None, None), timeout=0.2)
        assert all(task.done() for task in application.state.maintenance_tasks)
    finally:
        release.set()
        tasks = getattr(application.state, "maintenance_tasks", [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await application.state.engine.dispose()
