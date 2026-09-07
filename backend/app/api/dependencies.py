"""Reusable FastAPI authentication dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import get_session_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models import User

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    request: Request,
    db: DatabaseSession,
    settings: ApplicationSettings,
) -> User:
    """Return the user authenticated by the opaque browser-session cookie."""

    token = request.cookies.get(settings.session_cookie_name)
    if token is not None:
        user = await get_session_user(db, token, settings)
        if user is not None:
            return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


CurrentUser = Annotated[User, Depends(get_current_user)]
