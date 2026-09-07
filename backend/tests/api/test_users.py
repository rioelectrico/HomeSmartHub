"""Home-scoped user administration contracts."""

from uuid import UUID

from sqlalchemy import select

from app.auth.passwords import verify_password
from app.auth.sessions import issue_session
from app.models import AuditLog, AuthSession, HomeUser, Permission, RolePermission, User


async def grant_user_administration(db, admin, home, roles) -> None:
    """Give the authenticated administrator the two permissions these routes require."""

    permissions = [Permission(name="users.read"), Permission(name="users.manage")]
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


async def test_user_administration_creates_lists_changes_role_and_deactivates(
    client, admin, db, home, roles
) -> None:
    """Dropping a user mutation or its membership must be visible through the home API."""

    await grant_user_administration(db, admin, home, roles)
    await login_as_admin(client, admin)

    created = await client.post(
        f"/api/homes/{home.id}/users",
        json={
            "username": "operator",
            "email": "operator@example.test",
            "password": "A-new-password!42",
            "role": "read_only",
        },
    )

    assert created.status_code == 201
    created_body = created.json()
    assert created_body["username"] == "operator"
    assert created_body["role"] == "read_only"
    assert "password" not in created.text
    user_id = created_body["id"]

    listed = await client.get(f"/api/homes/{home.id}/users")
    assert listed.status_code == 200
    assert [member["username"] for member in listed.json()["items"]] == ["admin", "operator"]

    changed = await client.patch(f"/api/homes/{home.id}/users/{user_id}", json={"role": "owner"})
    assert changed.status_code == 200
    assert changed.json()["role"] == "owner"

    disabled = await client.patch(
        f"/api/homes/{home.id}/users/{user_id}", json={"is_active": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    stored_user = await db.get(User, UUID(user_id))
    assert stored_user is not None
    assert stored_user.is_active is False
    audit_actions = set(await db.scalars(select(AuditLog.action)))
    assert {"user.created", "user.role_changed", "user.disabled"} <= audit_actions


async def test_user_creation_rejects_duplicate_identity_and_invalid_role(
    client, admin, db, home, roles
) -> None:
    """Removing uniqueness and role validation must reject a bad administrative write."""

    await grant_user_administration(db, admin, home, roles)
    await login_as_admin(client, admin)

    duplicate = await client.post(
        f"/api/homes/{home.id}/users",
        json={
            "username": admin.username,
            "email": "other@example.test",
            "password": "A-new-password!42",
            "role": "read_only",
        },
    )
    duplicate_email = await client.post(
        f"/api/homes/{home.id}/users",
        json={
            "username": "other-user",
            "email": admin.email.upper(),
            "password": "A-new-password!42",
            "role": "read_only",
        },
    )
    bad_role = await client.post(
        f"/api/homes/{home.id}/users",
        json={
            "username": "new-user",
            "email": "new@example.test",
            "password": "A-new-password!42",
            "role": "does-not-exist",
        },
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CONFLICT"
    assert duplicate_email.status_code == 409
    assert duplicate_email.json()["error"]["code"] == "CONFLICT"
    assert bad_role.status_code == 422
    assert bad_role.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_user_creation_returns_stable_validation_error_for_malformed_email(
    client, admin, db, home, roles
) -> None:
    """Bypassing FastAPI's encoder must not turn a field validation failure into a 500."""

    await grant_user_administration(db, admin, home, roles)
    await login_as_admin(client, admin)

    response = await client.post(
        f"/api/homes/{home.id}/users",
        json={
            "username": "operator",
            "email": "not-an-email",
            "password": "A-new-password!42",
            "role": "read_only",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_user_creation_rejects_password_below_minimum(client, admin, db, home, roles) -> None:
    """Weakening the administrative password minimum must be visible at the HTTP boundary."""

    await grant_user_administration(db, admin, home, roles)
    await login_as_admin(client, admin)

    response = await client.post(
        f"/api/homes/{home.id}/users",
        json={
            "username": "operator",
            "email": "operator@example.test",
            "password": "too-short",
            "role": "read_only",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_reset_password_revokes_every_session_and_audit_omits_password(
    client, admin, db, home, roles, settings
) -> None:
    """Removing session revocation or recording a replacement password is a security regression."""

    await grant_user_administration(db, admin, home, roles)
    target = User(
        username="target",
        email="target@example.test",
        password_hash=admin.password_hash,
    )
    db.add(target)
    await db.flush()
    db.add(HomeUser(home_id=home.id, user_id=target.id, role_id=roles["read_only"].id))
    await db.commit()
    await issue_session(db, target, settings)
    await issue_session(db, target, settings)
    await db.commit()
    await login_as_admin(client, admin)

    response = await client.post(
        f"/api/homes/{home.id}/users/{target.id}/reset-password",
        json={"password": "Replacement-password!42"},
    )

    assert response.status_code == 204
    sessions = (await db.scalars(select(AuthSession).where(AuthSession.user_id == target.id))).all()
    assert len(sessions) == 2
    assert all(session.revoked_at is not None for session in sessions)
    await db.refresh(target)
    assert verify_password("Replacement-password!42", target.password_hash)
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "user.password_reset"))
    assert audit is not None
    assert "Replacement-password!42" not in (audit.detail or "")
    assert "Replacement-password!42" not in str(audit.context)
    assert "password" not in audit.context
    assert "password_hash" not in audit.context
