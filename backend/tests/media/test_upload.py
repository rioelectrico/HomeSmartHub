"""Authenticated, command-correlated device media upload contracts."""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import register_exception_handlers
from app.api.routes.media import router
from app.config import get_settings
from app.database import get_db
from app.devices.connections import DeviceConnectionRegistry
from app.models import (
    CommandStatus,
    Device,
    DeviceCommand,
    Event,
    HomeUser,
    Media,
    Permission,
    RolePermission,
)
from app.models.activity import MediaType


class DeviceSocketStub:
    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        pass

    async def send_json(self, data: object) -> None:
        pass


class DeviceClient:
    """HTTP peer that supplies the command correlation owned by the fixture."""

    def __init__(self, client: AsyncClient, command: DeviceCommand, registry, lease) -> None:
        self._client = client
        self.command = command
        self.registry = registry
        self.lease = lease

    async def post(self, url: str, **kwargs):
        kwargs.setdefault("params", {"command_id": str(self.command.id)})
        return await self._client.post(url, **kwargs)


async def grant_camera_view(db, admin, home, roles) -> None:
    permission = Permission(name="camera.view")
    db.add_all(
        [
            permission,
            HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id),
        ]
    )
    await db.flush()
    db.add(RolePermission(role_id=roles["owner"].id, permission_id=permission.id))
    await db.commit()


@pytest.fixture
async def device_client(db: AsyncSession, home, settings, monkeypatch):
    device = Device(home_id=home.id, device_id="PI-000001", name="Entrada")
    db.add(device)
    await db.flush()
    command = DeviceCommand(
        device_id=device.id,
        command="camera.capture",
        status=CommandStatus.ACKNOWLEDGED,
        payload={},
    )
    db.add(command)
    await db.commit()

    registry = DeviceConnectionRegistry()
    lease = await registry.register(
        device.id,
        DeviceSocketStub(),
        upload_token="upload-token",
        upload_token_expires_at=command.created_at.replace(year=command.created_at.year + 1),
    )
    monkeypatch.setattr("app.api.routes.media.device_connections", registry)

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
        headers={"authorization": "DeviceUpload upload-token"},
    ) as test_client:
        yield DeviceClient(test_client, command, registry, lease)


async def test_upload_rejects_declared_jpeg_with_invalid_signature(device_client):
    """Changing JPEG signature validation to trust MIME alone must fail this test."""

    response = await device_client.post(
        "/api/device/media",
        content=b"not-jpeg",
        headers={"content-type": "image/jpeg"},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "INVALID_MEDIA"


async def test_upload_requires_exact_jpeg_content_type(device_client):
    """Relaxing MIME comparison must permit this unsupported generic image type."""

    response = await device_client.post(
        "/api/device/media",
        content=b"\xff\xd8camera-payload\xff\xd9",
        headers={"content-type": "image/*"},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "INVALID_MEDIA"


async def test_upload_rejects_declared_oversize_before_reading_body(device_client, settings):
    """Dropping the Content-Length guard must accept this deceptively small body."""

    settings.max_upload_bytes = 8
    response = await device_client.post(
        "/api/device/media",
        content=b"\xff\xd8\xff\xd9",
        headers={"content-type": "image/jpeg", "content-length": "9"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "MEDIA_TOO_LARGE"


async def test_upload_requires_exact_device_upload_authorization_scheme(device_client):
    """Accepting a bearer token must bypass the connection-bound upload scheme."""

    response = await device_client.post(
        "/api/device/media",
        content=b"\xff\xd8camera-payload\xff\xd9",
        headers={
            "authorization": "Bearer upload-token",
            "content-type": "image/jpeg",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_FAILED"


async def test_upload_token_cannot_authorize_another_devices_command(
    device_client, db: AsyncSession, home
):
    """Dropping device-bound token verification must allow foreign correlation."""

    foreign_device = Device(home_id=home.id, device_id="PI-000002", name="Cochera")
    db.add(foreign_device)
    await db.flush()
    foreign_command = DeviceCommand(
        device_id=foreign_device.id,
        command="camera.capture",
        status=CommandStatus.ACKNOWLEDGED,
        payload={},
    )
    db.add(foreign_command)
    await db.commit()

    response = await device_client.post(
        "/api/device/media",
        params={"command_id": str(foreign_command.id)},
        content=b"\xff\xd8camera-payload\xff\xd9",
        headers={"content-type": "image/jpeg"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_FAILED"


async def test_upload_token_is_rejected_after_device_connection_ends(device_client):
    """The HTTP route must not outlive the WebSocket upload-session lease."""

    assert await device_client.registry.unregister(device_client.lease)

    response = await device_client.post(
        "/api/device/media",
        content=b"\xff\xd8camera-payload\xff\xd9",
        headers={"content-type": "image/jpeg"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_FAILED"


async def test_upload_rejects_command_before_acknowledgement(device_client, db: AsyncSession):
    """Removing the lifecycle gate must let a pending command receive media."""

    device_client.command.status = CommandStatus.PENDING
    await db.commit()

    response = await device_client.post(
        "/api/device/media",
        content=b"\xff\xd8camera-payload\xff\xd9",
        headers={"content-type": "image/jpeg"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_CORRELATION"


async def test_upload_rejects_non_capture_command(device_client, db: AsyncSession):
    """Dropping command-kind correlation must let unrelated commands receive media."""

    device_client.command.command = "device.status.request"
    await db.commit()

    response = await device_client.post(
        "/api/device/media",
        content=b"\xff\xd8camera-payload\xff\xd9",
        headers={"content-type": "image/jpeg"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_CORRELATION"


async def test_upload_persists_media_and_camera_capture_event_in_one_response(
    device_client, db: AsyncSession, settings
):
    """Omitting transactional metadata must leave a successful file unqueryable."""

    jpeg = b"\xff\xd8camera-payload\xff\xd9"
    response = await device_client.post(
        "/api/device/media",
        content=jpeg,
        headers={"content-type": "image/jpeg"},
    )

    assert response.status_code == 201
    media = await db.get(Media, response.json()["id"])
    assert media is not None
    event = await db.get(Event, media.event_id)
    assert event is not None
    assert event.event_type == "camera_capture"
    assert event.payload == {
        "command_id": str(device_client.command.id),
        "media_id": str(media.id),
    }
    assert media.command_id == device_client.command.id
    assert media.device_id == device_client.command.device_id
    assert media.content_type == "image/jpeg"
    assert media.size_bytes == len(jpeg)
    assert (settings.media_root / media.path).read_bytes() == jpeg
    assert response.json()["url"] == f"/api/homes/{media.home_id}/media/{media.id}"
    assert "path" not in response.json()


async def test_upload_accepts_completed_capture_command(device_client, db: AsyncSession):
    """Narrowing correlation to acknowledged only must reject a completed capture."""

    device_client.command.status = CommandStatus.COMPLETED
    await db.commit()

    response = await device_client.post(
        "/api/device/media",
        content=b"\xff\xd8camera-payload\xff\xd9",
        headers={"content-type": "image/jpeg"},
    )

    assert response.status_code == 201


async def test_upload_removes_file_when_database_commit_fails(
    device_client, db: AsyncSession, settings, monkeypatch
):
    """Removing compensating cleanup must orphan the newly published JPEG."""

    async def fail_commit() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await device_client.post(
            "/api/device/media",
            content=b"\xff\xd8camera-payload\xff\xd9",
            headers={"content-type": "image/jpeg"},
        )

    assert list(settings.media_root.rglob("*.jpg")) == []


async def test_download_returns_jpeg_with_camera_view_permission(
    client, admin, db: AsyncSession, home, roles, settings
):
    """Omitting the protected download route must make persisted media unavailable."""

    await grant_camera_view(db, admin, home, roles)
    relative_path = f"{home.id}/2026/09/capture.jpg"
    absolute_path = settings.media_root / relative_path
    absolute_path.parent.mkdir(parents=True)
    jpeg = b"\xff\xd8camera-payload\xff\xd9"
    absolute_path.write_bytes(jpeg)
    media = Media(
        home_id=home.id,
        media_type=MediaType.IMAGE,
        content_type="image/jpeg",
        path=relative_path,
        size_bytes=len(jpeg),
    )
    db.add(media)
    await db.commit()
    login = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    assert login.status_code == 204

    response = await client.get(f"/api/homes/{home.id}/media/{media.id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == jpeg


async def test_download_rejects_persisted_path_traversal(
    client, admin, db: AsyncSession, home, roles, settings
):
    """Removing storage confinement must disclose a file outside MEDIA_ROOT."""

    await grant_camera_view(db, admin, home, roles)
    outside_path = settings.media_root.parent / f"outside-{uuid4()}.jpg"
    outside_path.write_bytes(b"\xff\xd8outside\xff\xd9")
    media = Media(
        home_id=home.id,
        media_type=MediaType.IMAGE,
        content_type="image/jpeg",
        path=f"../{outside_path.name}",
        size_bytes=11,
    )
    db.add(media)
    await db.commit()
    login = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    assert login.status_code == 204

    response = await client.get(f"/api/homes/{home.id}/media/{media.id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert outside_path.exists()
    outside_path.unlink()


async def test_download_rejects_legacy_media_read_without_camera_view(
    client, admin, db: AsyncSession, home, roles
):
    """Substituting the legacy media grant must weaken the exact camera permission."""

    permission = Permission(name="media.read")
    media = Media(
        home_id=home.id,
        media_type=MediaType.IMAGE,
        content_type="image/jpeg",
        path=f"{home.id}/2026/09/missing.jpg",
        size_bytes=1,
    )
    db.add_all(
        [
            permission,
            media,
            HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id),
        ]
    )
    await db.flush()
    db.add(RolePermission(role_id=roles["owner"].id, permission_id=permission.id))
    await db.commit()
    login = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    assert login.status_code == 204

    response = await client.get(f"/api/homes/{home.id}/media/{media.id}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
