"""Home-scoped events with stable descending (created_at, id) keyset pagination."""

from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import APIError
from app.models import Event
from app.schemas.events import EventQuery, EventResponse, EventsResponse, decode_cursor


async def list_events(db: AsyncSession, home_id: UUID, query: EventQuery) -> EventsResponse:
    statement = select(Event).where(Event.home_id == home_id)
    if query.event_type is not None:
        statement = statement.where(Event.event_type == query.event_type)
    if query.device_id is not None:
        statement = statement.where(Event.device_id == query.device_id)
    if query.date_from is not None:
        statement = statement.where(Event.created_at >= query.date_from)
    if query.date_to is not None:
        statement = statement.where(Event.created_at < query.date_to)
    if query.cursor is not None:
        statement = statement.where(
            tuple_(Event.created_at, Event.id) < decode_cursor(query.cursor)
        )
    rows = (
        await db.scalars(
            statement.order_by(Event.created_at.desc(), Event.id.desc()).limit(query.limit + 1)
        )
    ).all()
    items = rows[: query.limit]
    cursor = (
        f"{items[-1].created_at.isoformat()}|{items[-1].id}" if len(rows) > query.limit else None
    )
    return EventsResponse(
        items=[EventResponse.model_validate(item) for item in items], next_cursor=cursor
    )


async def get_event(db: AsyncSession, home_id: UUID, event_id: UUID) -> EventResponse:
    entity = await db.scalar(select(Event).where(Event.home_id == home_id, Event.id == event_id))
    if entity is None:
        raise APIError(404, "NOT_FOUND")
    return EventResponse.model_validate(entity)
