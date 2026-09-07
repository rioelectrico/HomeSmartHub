"""Home-scoped device administration contracts."""

from sqlalchemy import select

from app.models import AuditLog, DeviceCredential, HomeUser, Permission, RolePermission


async def grant_device_permissions(db, admin, home, roles, *names: str) -> None:
    permissions = [Permission(name=name) for name in names]
    db.add_all(
        [
            *permissions,
            HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id),
        ]
    )
    await db.flush()
    db.add_all(
        [
            RolePermission(role_id=roles["owner"].id, permission_id=permission.id)
            for permission in permissions
        ]
    )
    await db.commit()


async def login_as_admin(client, admin) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    assert response.status_code == 204


async def test_device_create_list_status_rotate_disable_and_enable(
    client, admin, db, home, roles
) -> None:
    """Dropping a lifecycle mutation or exposing the stored secret breaks the admin API."""

    await grant_device_permissions(db, admin, home, roles, "devices.read", "devices.manage")
    await login_as_admin(client, admin)

    invalid = await client.post(
        f"/api/homes/{home.id}/devices",
        json={"device_id": "PI-00001", "name": "Entrada"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    created = await client.post(
        f"/api/homes/{home.id}/devices",
        json={"device_id": "PI-000001", "name": "Entrada"},
    )
    assert created.status_code == 201
    created_body = created.json()
    first_secret = created_body.pop("secret")
    assert len(first_secret) >= 32
    device_uuid = created_body["id"]
    assert created_body["status"] == "provisioning"

    listed = await client.get(f"/api/homes/{home.id}/devices")
    status = await client.get(f"/api/homes/{home.id}/devices/{device_uuid}/status")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["device_id"] == "PI-000001"
    assert "secret" not in listed.text
    assert status.status_code == 200
    assert "secret" not in status.text

    rotated = await client.post(f"/api/homes/{home.id}/devices/{device_uuid}/rotate")
    assert rotated.status_code == 200
    assert rotated.json()["secret"] != first_secret

    disabled = await client.post(f"/api/homes/{home.id}/devices/{device_uuid}/disable")
    enabled = await client.post(f"/api/homes/{home.id}/devices/{device_uuid}/enable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "offline"

    credentials = (
        await db.scalars(select(DeviceCredential).order_by(DeviceCredential.created_at))
    ).all()
    assert len(credentials) == 2
    assert credentials[0].revoked_at is not None
    assert credentials[1].revoked_at is None
    audits = (await db.scalars(select(AuditLog))).all()
    assert {audit.action for audit in audits} >= {
        "device.provisioned",
        "device.credential_rotated",
        "device.disabled",
        "device.enabled",
    }
    assert first_secret not in str([audit.context for audit in audits])
    assert first_secret not in str([audit.detail for audit in audits])


async def test_device_routes_enforce_exact_permissions_and_identifier_pattern(
    client, admin, db, home, roles
) -> None:
    """Read access must not imply management and malformed hardware IDs must fail."""

    await grant_device_permissions(db, admin, home, roles, "devices.read")
    await login_as_admin(client, admin)

    listed = await client.get(f"/api/homes/{home.id}/devices")
    forbidden = await client.post(
        f"/api/homes/{home.id}/devices",
        json={"device_id": "PI-000001", "name": "Entrada"},
    )
    invalid = await client.post(
        f"/api/homes/{home.id}/devices",
        json={"device_id": "pi-1", "name": "Entrada"},
    )

    assert listed.status_code == 200
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "PERMISSION_DENIED"
    # Permission checks deliberately run before payload validation.
    assert invalid.status_code == 403
