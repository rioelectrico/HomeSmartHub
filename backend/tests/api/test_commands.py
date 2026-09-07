"""Home-scoped device command API contracts."""

import pytest
from sqlalchemy import func, select

from app.models import Device, DeviceCommand, HomeUser, Permission, RolePermission


async def _grant_control(db, admin, home, roles, name: str = "devices.control") -> None:
    permission = Permission(name=name)
    db.add_all(
        [
            permission,
            HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id),
        ]
    )
    await db.flush()
    db.add(RolePermission(role_id=roles["owner"].id, permission_id=permission.id))
    await db.commit()


async def _login(client, admin) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    assert response.status_code == 204


async def _device(db, home, identifier: str = "PI-000001") -> Device:
    device = Device(home_id=home.id, device_id=identifier, name="Entrada")
    db.add(device)
    await db.commit()
    return device


async def test_command_routes_require_exact_devices_control_permission(
    client, admin, db, home, roles
) -> None:
    """Legacy device permissions must not authorize control operations."""

    await _grant_control(db, admin, home, roles, "devices.manage")
    device = await _device(db, home)
    await _login(client, admin)

    created = await client.post(
        f"/api/homes/{home.id}/devices/{device.id}/commands",
        json={"command": "camera.capture", "payload": {}},
    )
    listed = await client.get(f"/api/homes/{home.id}/devices/{device.id}/commands")

    assert created.status_code == 403
    assert listed.status_code == 403
    assert created.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_create_list_and_detail_return_sanitized_command_timeline(
    client, admin, db, home, roles, monkeypatch
) -> None:
    """Operators need stable lifecycle history without device-supplied secrets."""

    await _grant_control(db, admin, home, roles)
    device = await _device(db, home)
    await _login(client, admin)

    class OnlineRegistry:
        async def send_json(self, device_id, payload) -> bool:
            return True

    monkeypatch.setattr("app.api.routes.commands.device_connections", OnlineRegistry())
    created = await client.post(
        f"/api/homes/{home.id}/devices/{device.id}/commands",
        json={
            "command": "camera.capture",
            "payload": {"quality": 80, "access_token": "request-secret"},
        },
    )

    assert created.status_code == 201
    command_id = created.json()["command_id"]
    command = await db.get(DeviceCommand, command_id)
    assert command is not None
    command.result = {
        "image_id": "image-1",
        "nested": {"secret": "result-secret", "visible": True},
    }
    await db.commit()

    listed = await client.get(f"/api/homes/{home.id}/devices/{device.id}/commands")
    detailed = await client.get(f"/api/homes/{home.id}/devices/{device.id}/commands/{command_id}")

    assert listed.status_code == 200
    assert detailed.status_code == 200
    body = detailed.json()
    assert [entry["status"] for entry in body["timeline"]] == ["pending", "sent"]
    assert body["payload"] == {"quality": 80}
    assert body["result"] == {"image_id": "image-1", "nested": {"visible": True}}
    assert listed.json()["items"][0] == body
    assert "request-secret" not in detailed.text
    assert "result-secret" not in detailed.text


async def test_access_unlock_is_rejected_without_persisting_a_command(
    client, admin, db, home, roles
) -> None:
    """A future physical-access contract must remain disabled in MVP1."""

    await _grant_control(db, admin, home, roles)
    device = await _device(db, home)
    await _login(client, admin)

    response = await client.post(
        f"/api/homes/{home.id}/devices/{device.id}/commands",
        json={"command": "access.unlock", "payload": {}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_COMMAND"
    assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 0


async def test_oversized_command_payload_returns_validation_error_without_persistence(
    client, admin, db, home, roles
) -> None:
    """HTTP validation must bound serialized command payloads before dispatch."""

    await _grant_control(db, admin, home, roles)
    device = await _device(db, home)
    await _login(client, admin)

    response = await client.post(
        f"/api/homes/{home.id}/devices/{device.id}/commands",
        json={"command": "camera.capture", "payload": {"data": "á" * 4_100}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 0


async def test_response_failure_cannot_rollback_committed_command(
    client, admin, db, home, roles, monkeypatch
) -> None:
    """An HTTP serialization failure after send must preserve the durable outcome."""

    await _grant_control(db, admin, home, roles)
    device = await _device(db, home)
    await _login(client, admin)

    class OnlineRegistry:
        async def send_json(self, device_id, payload) -> bool:
            return True

    def fail_response(command):
        raise RuntimeError("serialization failed")

    monkeypatch.setattr("app.api.routes.commands.device_connections", OnlineRegistry())
    monkeypatch.setattr("app.api.routes.commands.command_response", fail_response)

    with pytest.raises(RuntimeError, match="serialization failed"):
        await client.post(
            f"/api/homes/{home.id}/devices/{device.id}/commands",
            json={"command": "camera.capture", "payload": {}},
        )
    await db.rollback()

    stored = await db.scalar(select(DeviceCommand).where(DeviceCommand.device_id == device.id))
    assert stored is not None
    assert stored.status.value == "sent"


async def test_command_detail_is_scoped_to_its_device(client, admin, db, home, roles) -> None:
    """A valid command UUID must not escape its device resource boundary."""

    await _grant_control(db, admin, home, roles)
    first = await _device(db, home)
    second = Device(home_id=home.id, device_id="PI-000002", name="Cochera")
    db.add(second)
    await db.flush()
    command = DeviceCommand(device_id=first.id, command="camera.capture", payload={})
    db.add(command)
    await db.commit()
    await _login(client, admin)

    response = await client.get(f"/api/homes/{home.id}/devices/{second.id}/commands/{command.id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
