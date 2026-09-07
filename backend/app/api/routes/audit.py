"""Administrative audit queries; access is granted by home-scoped audit.read."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import DatabaseSession
from app.audit.service import list_audit
from app.auth.rbac import PermissionName, require_permission
from app.schemas.audit import AuditListResponse, AuditQuery

router = APIRouter(
    prefix="/api/homes/{home_id}/audit",
    tags=["audit"],
    dependencies=[Depends(require_permission(PermissionName.AUDIT_READ))],
)


@router.get("", response_model=AuditListResponse)
async def audit(
    home_id: UUID, db: DatabaseSession, query: Annotated[AuditQuery, Query()]
) -> AuditListResponse:
    return await list_audit(db, home_id, query)
