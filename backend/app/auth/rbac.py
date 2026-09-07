"""Declarative home-scoped permission dependencies."""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from uuid import UUID

from fastapi import status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DatabaseSession
from app.api.errors import APIError
from app.models import HomeUser, Permission, RolePermission


class PermissionName(StrEnum):
    """Permissions seeded by the bootstrap command."""

    HOMES_READ = "homes.read"
    HOMES_MANAGE = "homes.manage"
    USERS_READ = "users.read"
    USERS_MANAGE = "users.manage"
    AGENT_VIEW = "agent.view"
    AGENT_EDIT = "agent.edit"
    DEVICES_READ = "devices.read"
    DEVICES_MANAGE = "devices.manage"
    DEVICES_CONTROL = "devices.control"
    EVENTS_READ = "events.read"
    MEDIA_READ = "media.read"
    CAMERA_VIEW = "camera.view"
    AUDIT_READ = "audit.read"


def require_permission(permission: PermissionName) -> Callable[..., Awaitable[None]]:
    """Require a permission in the ``home_id`` path scope without probing that home."""

    async def permission_dependency(
        home_id: UUID,
        db: DatabaseSession,
        current_user: CurrentUser,
    ) -> None:
        membership_id = await db.scalar(
            select(HomeUser.id)
            .join(RolePermission, RolePermission.role_id == HomeUser.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                HomeUser.home_id == home_id,
                HomeUser.user_id == current_user.id,
                Permission.name == str(permission),
            )
            .limit(1)
        )
        if membership_id is None:
            raise APIError(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED")

    return permission_dependency
