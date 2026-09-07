"""Authorization boundaries for home-scoped administration."""

from uuid import uuid4

from sqlalchemy import select

from app.models import Home, HomeUser, Permission, RolePermission


async def test_owner_cannot_edit_agent_in_unrelated_home(client, admin, db, home, roles) -> None:
    """Removing the home membership check must deny writes outside the caller's home."""

    permission = Permission(name="agent.edit")
    unrelated_home = Home(name="Unrelated home")
    db.add_all(
        [
            permission,
            unrelated_home,
            HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id),
        ]
    )
    await db.flush()
    db.add_all(
        [
            RolePermission(role_id=roles["owner"].id, permission_id=permission.id),
            HomeUser(
                home_id=unrelated_home.id,
                user_id=admin.id,
                role_id=roles["read_only"].id,
            ),
        ]
    )
    await db.commit()

    login = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    assert login.status_code == 204

    response = await client.put(
        f"/api/homes/{unrelated_home.id}/agent",
        json={
            "name": "Ada",
            "system_prompt": "Recibir visitantes",
            "language": "es-AR",
            "voice": "configured",
            "voice_speed": 1.0,
            "realtime_model": "configured",
            "enabled": True,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert await db.scalar(select(Home).where(Home.id == unrelated_home.id)) is not None

    unknown_response = await client.put(
        f"/api/homes/{uuid4()}/agent",
        json={
            "name": "Ada",
            "system_prompt": "Recibir visitantes",
            "language": "es-AR",
            "voice": "configured",
            "voice_speed": 1.0,
            "realtime_model": "configured",
            "enabled": True,
        },
    )
    assert unknown_response.status_code == response.status_code
    assert unknown_response.json() == response.json()


async def test_home_listing_preserves_each_membership_role_and_permissions(
    client, admin, db, two_homes, roles
) -> None:
    """Collapsing role lookup to the user level must not blur grants between homes."""

    read_permission = Permission(name="homes.read")
    manage_permission = Permission(name="homes.manage")
    db.add_all([read_permission, manage_permission])
    await db.flush()
    db.add_all(
        [
            RolePermission(role_id=roles["owner"].id, permission_id=read_permission.id),
            RolePermission(role_id=roles["owner"].id, permission_id=manage_permission.id),
            RolePermission(role_id=roles["read_only"].id, permission_id=read_permission.id),
            HomeUser(
                home_id=two_homes[0].id,
                user_id=admin.id,
                role_id=roles["owner"].id,
            ),
            HomeUser(
                home_id=two_homes[1].id,
                user_id=admin.id,
                role_id=roles["read_only"].id,
            ),
        ]
    )
    await db.commit()

    login = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    assert login.status_code == 204

    response = await client.get("/api/homes")

    assert response.status_code == 200
    homes = {item["name"]: item for item in response.json()["items"]}
    assert homes["Casa"]["role"] == "owner"
    assert homes["Casa"]["permissions"] == ["homes.manage", "homes.read"]
    assert homes["Oficina"]["role"] == "read_only"
    assert homes["Oficina"]["permissions"] == ["homes.read"]
