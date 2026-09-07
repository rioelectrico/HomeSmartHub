"""Home activity statistics guarded by events.read."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import DatabaseSession
from app.auth.rbac import PermissionName, require_permission
from app.schemas.statistics import StatisticsResponse
from app.services.statistics import statistics

router = APIRouter(
    prefix="/api/homes/{home_id}/statistics",
    tags=["statistics"],
    dependencies=[Depends(require_permission(PermissionName.EVENTS_READ))],
)


@router.get("", response_model=StatisticsResponse)
async def home_statistics(home_id: UUID, db: DatabaseSession) -> StatisticsResponse:
    return await statistics(db, home_id)
