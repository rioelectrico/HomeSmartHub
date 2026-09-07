"""Transactional operations for home-scoped user administration."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import APIError
from app.audit.service import write_audit
from app.auth.passwords import hash_password
from app.models import AuthSession, HomeUser, Role, User
from app.schemas.users import HomeUserResponse, UserCreateRequest, UserUpdateRequest


async def require_role(db: AsyncSession, role_name: str) -> Role:
    """Resolve an assignable role or return a validation error."""

    role = await db.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        raise APIError(422, "VALIDATION_ERROR")
    return role


async def home_member(db: AsyncSession, home_id: UUID, user_id: UUID) -> HomeUser:
    """Return a membership only after the caller has been authorized for its home."""

    membership = await db.scalar(
        select(HomeUser)
        .where(HomeUser.home_id == home_id, HomeUser.user_id == user_id)
        .options(selectinload(HomeUser.user), selectinload(HomeUser.role))
    )
    if membership is None:
        raise APIError(404, "NOT_FOUND")
    return membership


def member_response(membership: HomeUser) -> HomeUserResponse:
    """Map loaded membership relations to the public response contract."""

    return HomeUserResponse(
        id=membership.user.id,
        username=membership.user.username,
        email=membership.user.email,
        is_active=membership.user.is_active,
        role=membership.role.name,
    )


async def list_home_users(db: AsyncSession, home_id: UUID) -> list[HomeUserResponse]:
    """List users assigned to one home in a deterministic order."""

    memberships = (
        await db.scalars(
            select(HomeUser)
            .where(HomeUser.home_id == home_id)
            .join(HomeUser.user)
            .order_by(User.username)
            .options(selectinload(HomeUser.user), selectinload(HomeUser.role))
        )
    ).all()
    return [member_response(membership) for membership in memberships]


async def create_home_user(
    db: AsyncSession,
    *,
    actor_id: UUID,
    home_id: UUID,
    payload: UserCreateRequest,
) -> HomeUserResponse:
    """Create an identity, attach its requested role, and audit only safe metadata."""

    existing = await db.scalar(
        select(User.id).where(or_(User.username == payload.username, User.email == payload.email))
    )
    if existing is not None:
        raise APIError(409, "CONFLICT")
    role = await require_role(db, payload.role)
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    membership = HomeUser(home_id=home_id, user_id=user.id, role_id=role.id)
    db.add(membership)
    await db.flush()
    await db.refresh(membership, attribute_names=["user", "role"])
    write_audit(
        db,
        action="user.created",
        user_id=actor_id,
        home_id=home_id,
        detail="User created",
        context={"created_user_id": str(user.id), "role": role.name},
    )
    return member_response(membership)


async def update_home_user(
    db: AsyncSession,
    *,
    actor_id: UUID,
    home_id: UUID,
    user_id: UUID,
    payload: UserUpdateRequest,
) -> HomeUserResponse:
    """Update a role and/or active state, auditing each requested administrative action."""

    membership = await home_member(db, home_id, user_id)
    if payload.role is not None:
        role = await require_role(db, payload.role)
        membership.role_id = role.id
        write_audit(
            db,
            action="user.role_changed",
            user_id=actor_id,
            home_id=home_id,
            detail="User role changed",
            context={"target_user_id": str(user_id), "role": role.name},
        )
    if payload.is_active is not None:
        membership.user.is_active = payload.is_active
        write_audit(
            db,
            action="user.enabled" if payload.is_active else "user.disabled",
            user_id=actor_id,
            home_id=home_id,
            detail="User enabled" if payload.is_active else "User disabled",
            context={"target_user_id": str(user_id)},
        )
    await db.flush()
    await db.refresh(membership, attribute_names=["user", "role"])
    return member_response(membership)


async def reset_password(
    db: AsyncSession,
    *,
    actor_id: UUID,
    home_id: UUID,
    user_id: UUID,
    password: str,
) -> None:
    """Replace a password and revoke every existing web session for that identity."""

    membership = await home_member(db, home_id, user_id)
    membership.user.password_hash = hash_password(password)
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    write_audit(
        db,
        action="user.password_reset",
        user_id=actor_id,
        home_id=home_id,
        detail="Password reset",
        context={"target_user_id": str(user_id)},
    )
    await db.flush()
