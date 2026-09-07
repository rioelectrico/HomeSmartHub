"""Persistent web-session contracts."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.auth.sessions import (
    get_session_user,
    hash_session_token,
    issue_session,
    revoke_session,
)
from app.models import AuthSession


async def test_database_stores_only_hash_of_session_token(db, user, settings) -> None:
    """Persisting the bearer token itself must never be possible."""

    raw = await issue_session(db, user, settings, ip_address="127.0.0.1")
    saved = await db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))

    assert saved is not None
    assert raw not in saved.token_hash
    assert saved.token_hash == hash_session_token(raw, settings)


async def test_revoked_session_cannot_resolve_a_user(db, user, settings) -> None:
    """Removing revocation filtering must prevent a logged-out token from authenticating."""

    raw = await issue_session(db, user, settings)
    await revoke_session(db, raw, settings)

    assert await get_session_user(db, raw, settings) is None


async def test_session_expiry_is_timezone_aware_and_in_the_future(db, user, settings) -> None:
    """Dropping the TTL or UTC-aware expiry would leave sessions unsafe or ambiguous."""

    await issue_session(db, user, settings)
    saved = await db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))

    assert saved is not None
    assert saved.expires_at.tzinfo is not None
    assert saved.expires_at > datetime.now(UTC)


async def test_expired_session_cannot_resolve_a_user(db, user, settings) -> None:
    """Dropping the expiry filter would reactivate an expired bearer token."""

    raw = await issue_session(db, user, settings)
    saved = await db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
    assert saved is not None
    saved.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.flush()

    assert await get_session_user(db, raw, settings) is None
