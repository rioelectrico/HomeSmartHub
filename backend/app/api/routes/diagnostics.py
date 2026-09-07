"""Home diagnostics use devices.read; the optional AI provider never gates readiness."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import DatabaseSession
from app.auth.rbac import PermissionName, require_permission
from app.devices.connections import device_connections
from app.schemas.diagnostics import DiagnosticsQuery, DiagnosticsResponse
from app.services.diagnostics import diagnostics

router = APIRouter(
    prefix="/api/homes/{home_id}/diagnostics",
    tags=["diagnostics"],
    dependencies=[Depends(require_permission(PermissionName.DEVICES_READ))],
)


@router.get("", response_model=DiagnosticsResponse)
async def home_diagnostics(
    home_id: UUID,
    request: Request,
    db: DatabaseSession,
    query: Annotated[DiagnosticsQuery, Query()],
) -> DiagnosticsResponse:
    return await diagnostics(db, home_id, query, request.app.state.ai_provider, device_connections)
