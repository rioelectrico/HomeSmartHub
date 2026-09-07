"""Initial authorization bootstrap contracts."""

import os
import subprocess
import sys
from pathlib import Path
from secrets import token_urlsafe

import pytest
from sqlalchemy import func, select

from app.auth.passwords import verify_password
from app.bootstrap import PERMISSIONS, bootstrap_database
from app.config import Settings
from app.models import Home, HomeUser, Permission, Role, RolePermission, User

EXPECTED_ROLES = {"administrator", "owner", "operator", "user", "read_only"}
EXPECTED_PERMISSIONS = {
    "homes.read",
    "homes.manage",
    "users.read",
    "users.manage",
    "agent.view",
    "agent.edit",
    "devices.read",
    "devices.manage",
    "devices.control",
    "events.read",
    "media.read",
    "camera.view",
    "audit.read",
}


async def test_bootstrap_module_entrypoint_creates_configured_administrator(db, settings) -> None:
    """Removing the module CLI must break the documented clean-install command."""

    password = f"Test-{token_urlsafe(24)}"
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": str(settings.database_url),
            "APP_SECRET_KEY": settings.app_secret_key.get_secret_value(),
            "DEVICE_CREDENTIAL_ENCRYPTION_KEY": (
                settings.device_credential_encryption_key.get_secret_value()
            ),
            "BOOTSTRAP_ADMIN_USERNAME": "module-admin",
            "BOOTSTRAP_ADMIN_EMAIL": "module-admin@example.test",
            "BOOTSTRAP_ADMIN_PASSWORD": password,
        }
    )

    completed = subprocess.run(
        [sys.executable, "-m", "app.bootstrap"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert password not in completed.stdout
    assert password not in completed.stderr
    created = await db.scalar(select(User).where(User.username == "module-admin"))
    assert created is not None
    assert verify_password(password, created.password_hash)


def bootstrap_settings(settings, **overrides: str | None) -> Settings:
    """Create bootstrap settings without exposing a real credential."""

    values: dict[str, object] = {
        "DATABASE_URL": str(settings.database_url),
        "APP_SECRET_KEY": settings.app_secret_key.get_secret_value(),
        "DEVICE_CREDENTIAL_ENCRYPTION_KEY": (
            settings.device_credential_encryption_key.get_secret_value()
        ),
        "MEDIA_ROOT": settings.media_root,
        "BOOTSTRAP_ADMIN_USERNAME": "bootstrap-admin",
        "BOOTSTRAP_ADMIN_EMAIL": "bootstrap@example.test",
        "BOOTSTRAP_ADMIN_PASSWORD": "ValidPass!42",
    }
    values.update(overrides)
    return Settings(**values)


async def test_bootstrap_is_idempotent_and_creates_administrator_access(db, settings) -> None:
    """Repeating bootstrap must retain the exact role and administrator permission contract."""

    configured = bootstrap_settings(settings)

    first = await bootstrap_database(db, configured)
    second = await bootstrap_database(db, configured)

    assert first.id == second.id
    assert verify_password("ValidPass!42", second.password_hash)
    assert await db.scalar(select(func.count()).select_from(User)) == 1
    assert await db.scalar(select(func.count()).select_from(Home)) == 1
    assert await db.scalar(select(func.count()).select_from(HomeUser)) == 1
    roles = {role.name: role for role in (await db.scalars(select(Role))).all()}
    assert set(roles) == EXPECTED_ROLES
    assert set(await db.scalars(select(Permission.name))) == EXPECTED_PERMISSIONS
    membership = await db.scalar(select(HomeUser))
    assert membership is not None
    assert membership.role_id == roles["administrator"].id
    administrator_permissions = set(
        await db.scalars(
            select(Permission.name)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == roles["administrator"].id)
        )
    )
    assert administrator_permissions == EXPECTED_PERMISSIONS
    assert set(PERMISSIONS) == EXPECTED_PERMISSIONS


@pytest.mark.parametrize(
    "overrides",
    [
        {"BOOTSTRAP_ADMIN_USERNAME": None},
        {"BOOTSTRAP_ADMIN_USERNAME": "   "},
        {"BOOTSTRAP_ADMIN_EMAIL": None},
        {"BOOTSTRAP_ADMIN_EMAIL": "   "},
        {"BOOTSTRAP_ADMIN_PASSWORD": None},
        {"BOOTSTRAP_ADMIN_PASSWORD": "   "},
        {"BOOTSTRAP_ADMIN_PASSWORD": "replace-with-a-bootstrap-password"},
    ],
)
async def test_bootstrap_rejects_missing_blank_or_template_admin_values(
    db, settings, overrides
) -> None:
    """Creating an owner with a missing, blank, or template credential is unsafe."""

    with pytest.raises(ValueError, match="Bootstrap administrator credentials"):
        await bootstrap_database(db, bootstrap_settings(settings, **overrides))

    assert await db.scalar(select(func.count()).select_from(User)) == 0
