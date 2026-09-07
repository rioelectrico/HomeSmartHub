"""Home event queries guarded by the seeded events.read permission."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import DatabaseSession
from app.auth.rbac import PermissionName, require_permission
from app.schemas.events import EventQuery, EventResponse, EventsResponse
from app.services.events import get_event, list_events

router = APIRouter(
    prefix="/api/homes/{home_id}/events",
    tags=["events"],
    dependencies=[Depends(require_permission(PermissionName.EVENTS_READ))],
)


@router.get("", response_model=EventsResponse)
async def events(
    home_id: UUID, db: DatabaseSession, query: Annotated[EventQuery, Query()]
) -> EventsResponse:
    return await list_events(db, home_id, query)


@router.get("/{event_id}", response_model=EventResponse)
async def event_detail(home_id: UUID, event_id: UUID, db: DatabaseSession) -> EventResponse:
    return await get_event(db, home_id, event_id)
