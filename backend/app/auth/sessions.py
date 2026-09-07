"""Opaque, revocable web-session tokens."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AuthSession, User


def hash_session_token(token: str, settings: Settings) -> str:
    """Derive the database-safe HMAC digest for an opaque session token."""

    return hmac.new(
        settings.app_secret_key.get_secret_value().encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def issue_session(
    db: AsyncSession,
    user: User,
    settings: Settings,
    *,
    ip_address: str | None = None,
) -> str:
    """Persist a hashed session token and return the one-time raw token."""

    raw_token = secrets.token_urlsafe(48)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_session_token(raw_token, settings),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds),
        )
    )
    await db.flush()
    return raw_token


async def get_session_user(db: AsyncSession, token: str, settings: Settings) -> User | None:
    """Resolve an active, unexpired opaque token to an active user."""

    expected_hash = hash_session_token(token, settings)
    session = await db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == expected_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    if session is None or not hmac.compare_digest(session.token_hash, expected_hash):
        return None

    user = await db.get(User, session.user_id)
    return user if user is not None and user.is_active else None


async def revoke_session(db: AsyncSession, token: str, settings: Settings) -> None:
    """Mark the matched session revoked; unknown and already-revoked tokens are harmless."""

    expected_hash = hash_session_token(token, settings)
    session = await db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == expected_hash,
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is not None and hmac.compare_digest(session.token_hash, expected_hash):
        session.revoked_at = datetime.now(UTC)
        await db.flush()
