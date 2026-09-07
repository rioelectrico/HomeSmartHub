"""Idempotent initial administrator and authorization bootstrap."""

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.passwords import hash_password
from app.config import Settings, get_settings
from app.models import Home, HomeUser, Permission, Role, RolePermission, User

PERMISSIONS = (
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
)


def bootstrap_inputs(settings: Settings) -> tuple[str, str, str]:
    """Return configured bootstrap inputs without exposing them through logs."""

    username = settings.bootstrap_admin_username
    email = settings.bootstrap_admin_email
    password = settings.bootstrap_admin_password
    if (
        username is None
        or not username.strip()
        or email is None
        or not email.strip()
        or password is None
        or not password.get_secret_value().strip()
        or password.get_secret_value().lower().startswith("replace-with-")
    ):
        raise ValueError("Bootstrap administrator credentials must be configured")
    return (
        username,
        email,
        password.get_secret_value(),
    )


async def bootstrap_database(db: AsyncSession, settings: Settings) -> User:
    """Create the initial owner, home, roles and permissions exactly once."""

    username, email, password = bootstrap_inputs(settings)
    async with db.begin():
        permissions = {
            permission.name: permission
            for permission in (await db.scalars(select(Permission))).all()
        }
        for name in PERMISSIONS:
            if name not in permissions:
                permission = Permission(name=name)
                db.add(permission)
                permissions[name] = permission

        roles = {role.name: role for role in (await db.scalars(select(Role))).all()}
        for name, description in (
            ("administrator", "Full administration of a home"),
            ("owner", "Full control of a home"),
            ("operator", "Operates configured devices"),
            ("user", "Uses configured home services"),
            ("read_only", "Read-only access"),
        ):
            if name not in roles:
                role = Role(name=name, description=description)
                db.add(role)
                roles[name] = role
        await db.flush()

        for privileged_role in (roles["administrator"], roles["owner"]):
            role_permission_ids = {
                permission_id
                for permission_id in (
                    await db.scalars(
                        select(RolePermission.permission_id).where(
                            RolePermission.role_id == privileged_role.id
                        )
                    )
                ).all()
            }
            for permission in permissions.values():
                if permission.id not in role_permission_ids:
                    db.add(
                        RolePermission(
                            role_id=privileged_role.id,
                            permission_id=permission.id,
                        )
                    )

        home = await db.scalar(select(Home).order_by(Home.created_at, Home.id).limit(1))
        if home is None:
            home = Home(name=settings.bootstrap_home_name)
            db.add(home)
            await db.flush()

        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, email=email, password_hash=hash_password(password))
            db.add(user)
            await db.flush()

        membership = await db.scalar(
            select(HomeUser).where(HomeUser.home_id == home.id, HomeUser.user_id == user.id)
        )
        if membership is None:
            db.add(
                HomeUser(
                    home_id=home.id,
                    user_id=user.id,
                    role_id=roles["administrator"].id,
                )
            )
    return user


async def _run_configured_bootstrap() -> None:
    """Apply bootstrap once using only validated process configuration."""

    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await bootstrap_database(db, settings)
    finally:
        await engine.dispose()


def main() -> None:
    """Run the idempotent bootstrap without logging configured credentials."""

    if os.name == "nt":
        asyncio.run(_run_configured_bootstrap(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(_run_configured_bootstrap())


if __name__ == "__main__":
    main()
