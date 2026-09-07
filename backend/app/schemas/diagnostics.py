"""Service and device diagnostic views excluding credentials and storage internals."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.ai.provider import ProviderStatus
from app.models import DeviceStatus
from app.schemas.events import PageQuery


class DiagnosticsQuery(PageQuery):
    pass


class ServiceStatus(BaseModel):
    backend: Literal["up"] = "up"
    postgresql: Literal["up", "down"]
    openai: ProviderStatus


class DeviceDiagnostic(BaseModel):
    id: UUID
    device_id: str
    name: str
    status: DeviceStatus
    connected: bool
    last_seen_at: datetime | None
    snapshot_at: datetime | None
    snapshot: dict[str, object] | None


class DiagnosticDevices(BaseModel):
    items: list[DeviceDiagnostic] = Field(max_length=100)
    next_cursor: str | None


class DiagnosticsResponse(BaseModel):
    services: ServiceStatus
    devices: DiagnosticDevices
