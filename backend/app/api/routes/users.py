"""Home-scoped user administration routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import CurrentUser, DatabaseSession
from app.auth.rbac import PermissionName, require_permission
from app.schemas.users import (
    HomeUserResponse,
    HomeUsersResponse,
    PasswordResetRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.services.users import create_home_user, list_home_users, reset_password, update_home_user

router = APIRouter(prefix="/api/homes/{home_id}/users", tags=["users"])

UsersReadPermission = Annotated[None, Depends(require_permission(PermissionName.USERS_READ))]
UsersManagePermission = Annotated[None, Depends(require_permission(PermissionName.USERS_MANAGE))]


@router.get("", response_model=HomeUsersResponse)
async def list_users(
    home_id: UUID,
    db: DatabaseSession,
    _: UsersReadPermission,
) -> HomeUsersResponse:
    """List the users assigned to an authorized home."""

    return HomeUsersResponse(items=await list_home_users(db, home_id))


@router.post("", response_model=HomeUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    home_id: UUID,
    payload: UserCreateRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    _: UsersManagePermission,
) -> HomeUserResponse:
    """Create an identity and its membership in an authorized home."""

    user = await create_home_user(
        db,
        actor_id=current_user.id,
        home_id=home_id,
        payload=payload,
    )
    await db.commit()
    return user


@router.patch("/{user_id}", response_model=HomeUserResponse)
async def update_user(
    home_id: UUID,
    user_id: UUID,
    payload: UserUpdateRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    _: UsersManagePermission,
) -> HomeUserResponse:
    """Change a member's role and/or active status."""

    user = await update_home_user(
        db,
        actor_id=current_user.id,
        home_id=home_id,
        user_id=user_id,
        payload=payload,
    )
    await db.commit()
    return user


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    home_id: UUID,
    user_id: UUID,
    payload: PasswordResetRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    _: UsersManagePermission,
) -> Response:
    """Reset a password and invalidate every existing session for that user."""

    await reset_password(
        db,
        actor_id=current_user.id,
        home_id=home_id,
        user_id=user_id,
        password=payload.password,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
