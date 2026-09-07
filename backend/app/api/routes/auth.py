"""Browser authentication endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.dependencies import ApplicationSettings, CurrentUser, DatabaseSession
from app.auth.passwords import verify_password
from app.auth.sessions import get_session_user, issue_session, revoke_session
from app.models import AuditLog, HomeUser, Role, RolePermission, User
from app.schemas.auth import CurrentUserResponse, HomeAccessResponse, LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def invalid_credentials() -> JSONResponse:
    """Return the same response for all credential failures."""

    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"error": {"code": "INVALID_CREDENTIALS"}},
    )


def request_ip(request: Request) -> str | None:
    """Return the directly connected client address, when the ASGI server provides it."""

    return request.client.host if request.client is not None else None


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(
    payload: LoginRequest,
    request: Request,
    db: DatabaseSession,
    settings: ApplicationSettings,
) -> Response:
    """Authenticate credentials and establish a protected opaque-cookie session."""

    user = await db.scalar(
        select(User).where(
            or_(User.username == payload.identifier, User.email == payload.identifier)
        )
    )
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        db.add(
            AuditLog(
                user_id=user.id if user is not None else None,
                home_id=None,
                action="auth.login.failed",
                detail="Invalid credentials",
                context={},
                ip_address=request_ip(request),
            )
        )
        await db.commit()
        return invalid_credentials()

    raw_token = await issue_session(db, user, settings, ip_address=request_ip(request))
    user.last_login_at = datetime.now(UTC)
    db.add(
        AuditLog(
            user_id=user.id,
            home_id=None,
            action="auth.login.succeeded",
            detail="Session established",
            context={},
            ip_address=request_ip(request),
        )
    )
    await db.commit()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=settings.cookie_path,
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    db: DatabaseSession,
    settings: ApplicationSettings,
) -> Response:
    """Revoke the present session token and instruct the browser to remove it."""

    token = request.cookies.get(settings.session_cookie_name)
    if token is not None:
        user = await get_session_user(db, token, settings)
        if user is not None:
            await revoke_session(db, token, settings)
            db.add(
                AuditLog(
                    user_id=user.id,
                    home_id=None,
                    action="auth.logout",
                    detail="Session revoked",
                    context={},
                    ip_address=request_ip(request),
                )
            )
            await db.commit()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=settings.cookie_path,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    return response


@router.get("/me", response_model=CurrentUserResponse)
async def me(
    db: DatabaseSession,
    current_user: CurrentUser,
) -> CurrentUserResponse:
    """Return the caller's identity and tenancy grants without credential material."""

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
    homes = [
        HomeAccessResponse(
            id=membership.home.id,
            name=membership.home.name,
            timezone=membership.home.timezone,
            role=membership.role.name,
            permissions=sorted(
                role_permission.permission.name for role_permission in membership.role.permissions
            ),
        )
        for membership in memberships
    ]
    return CurrentUserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        homes=homes,
    )
