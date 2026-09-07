"""Home access routes."""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.dependencies import CurrentUser, DatabaseSession
from app.models import HomeUser, Role, RolePermission
from app.schemas.homes import HomeResponse, HomesResponse

router = APIRouter(prefix="/api/homes", tags=["homes"])


@router.get("", response_model=HomesResponse)
async def list_homes(db: DatabaseSession, current_user: CurrentUser) -> HomesResponse:
    """List each home available to the caller with its role and permissions."""

    memberships = (
        await db.scalars(
            select(HomeUser)
            .where(HomeUser.user_id == current_user.id)
            .options(
                selectinload(HomeUser.home),
                selectinload(HomeUser.role)
                .selectinload(Role.permissions)
                .selectinload(RolePermission.permission),
            )
        )
    ).all()
    return HomesResponse(
        items=[
            HomeResponse(
                id=membership.home.id,
                name=membership.home.name,
                timezone=membership.home.timezone,
                role=membership.role.name,
                permissions=sorted(
                    role_permission.permission.name
                    for role_permission in membership.role.permissions
                ),
            )
            for membership in memberships
        ]
    )
