"""Authentication API contracts."""

from sqlalchemy import select

from app.models import AuditLog, HomeUser, Permission, RolePermission


async def test_login_sets_http_only_cookie(client, admin) -> None:
    """Removing browser cookie protections would expose a login session."""

    response = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )

    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie


async def test_successful_login_is_audited_without_credential_material(client, admin, db) -> None:
    """Skipping an audit or recording the password on login success is a security regression."""

    response = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "auth.login.succeeded"))

    assert response.status_code == 204
    assert audit is not None
    assert audit.user_id == admin.id
    assert audit.home_id is None
    assert audit.detail == "Session established"
    assert audit.context == {}


async def test_wrong_password_is_generic_and_audited(client, admin, db) -> None:
    """Leaking which credential failed or skipping its audit record is a security regression."""

    response = await client.post(
        "/api/auth/login", json={"identifier": admin.email, "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "auth.login.failed"))
    assert audit is not None
    assert audit.user_id == admin.id
    assert audit.home_id is None


async def test_unknown_login_is_audited_without_identity(client, db) -> None:
    """Resolving an unknown account before auditing must retain nullable audit references."""

    response = await client.post(
        "/api/auth/login", json={"identifier": "missing", "password": "wrong"}
    )

    assert response.status_code == 401
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "auth.login.failed"))
    assert audit is not None
    assert audit.user_id is None
    assert audit.home_id is None


async def test_me_returns_identity_without_credential_hash(client, admin) -> None:
    """Including password fields in the current-user response would disclose credentials."""

    login = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    response = await client.get("/api/auth/me")

    assert login.status_code == 204
    assert response.status_code == 200
    assert response.json()["username"] == admin.username
    assert "password_hash" not in response.text


async def test_me_returns_home_role_and_permissions(client, admin, db, home, roles) -> None:
    """Omitting a tenancy grant from /me would leave clients unable to authorize navigation."""

    permission = Permission(name="devices.read")
    db.add(permission)
    await db.flush()
    db.add_all(
        [
            RolePermission(role_id=roles["owner"].id, permission_id=permission.id),
            HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id),
        ]
    )
    await db.commit()
    await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["homes"] == [
        {
            "id": str(home.id),
            "name": home.name,
            "timezone": home.timezone,
            "role": "owner",
            "permissions": ["devices.read"],
        }
    ]


async def test_logout_revokes_the_cookie_session(client, admin) -> None:
    """Leaving the session valid after logout would allow replay of the cookie."""

    await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    logout = await client.post("/api/auth/logout")
    me = await client.get("/api/auth/me")

    assert logout.status_code == 204
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert me.status_code == 401


async def test_logout_is_audited_for_the_session_user(client, admin, db) -> None:
    """An unaudited logout would leave session termination untraceable."""

    await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    response = await client.post("/api/auth/logout")
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "auth.logout"))

    assert response.status_code == 204
    assert audit is not None
    assert audit.user_id == admin.id
    assert audit.home_id is None
    assert audit.detail == "Session revoked"
    assert audit.context == {}
