"""Security audit writes that deliberately exclude secret payload fields."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.schemas.audit import AuditListResponse, AuditQuery, AuditResponse
from app.schemas.events import decode_cursor

_SENSITIVE_KEYS = frozenset({"password", "password_hash", "system_prompt", "token", "secret"})


def sanitized_context(context: Mapping[str, object]) -> dict[str, object]:
    """Return audit metadata with credential and prompt values removed."""

    return {
        key: value
        for key, value in context.items()
        if key.lower() not in _SENSITIVE_KEYS and "password" not in key.lower()
    }


def write_audit(
    db: AsyncSession,
    *,
    action: str,
    user_id: UUID | None,
    home_id: UUID | None,
    detail: str,
    context: Mapping[str, object],
) -> AuditLog:
    """Stage a sanitized audit event in the caller's transaction."""

    event = AuditLog(
        action=action,
        user_id=user_id,
        home_id=home_id,
        detail=detail,
        context=sanitized_context(context),
    )
    db.add(event)
    return event


async def list_audit(db: AsyncSession, home_id: UUID, query: AuditQuery) -> AuditListResponse:
    """Read only the authorized home's audit; global login records are never included."""
    statement = select(AuditLog).where(AuditLog.home_id == home_id)
    if query.action is not None:
        statement = statement.where(AuditLog.action == query.action)
    if query.user_id is not None:
        statement = statement.where(AuditLog.user_id == query.user_id)
    if query.date_from is not None:
        statement = statement.where(AuditLog.created_at >= query.date_from)
    if query.date_to is not None:
        statement = statement.where(AuditLog.created_at < query.date_to)
    if query.cursor is not None:
        statement = statement.where(
            tuple_(AuditLog.created_at, AuditLog.id) < decode_cursor(query.cursor)
        )
    rows = (
        await db.scalars(
            statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(
                query.limit + 1
            )
        )
    ).all()
    items = rows[: query.limit]
    cursor = (
        f"{items[-1].created_at.isoformat()}|{items[-1].id}" if len(rows) > query.limit else None
    )
    return AuditListResponse(
        items=[AuditResponse.model_validate(item) for item in items], next_cursor=cursor
    )
