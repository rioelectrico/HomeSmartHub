"""Device upload and authenticated media download routes."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.dependencies import ApplicationSettings, DatabaseSession
from app.api.errors import APIError
from app.auth.rbac import PermissionName, require_permission
from app.devices.connections import device_connections
from app.media.storage import FilesystemMediaStorage
from app.media.validation import InvalidMedia, MediaTooLarge, UnsafeMediaPath
from app.models import CommandStatus, Device, DeviceCommand, Event, Media
from app.models.activity import MediaType

router = APIRouter(tags=["media"])

CameraViewPermission = Annotated[
    None,
    Depends(require_permission(PermissionName.CAMERA_VIEW)),
]


@router.post("/api/device/media", status_code=status.HTTP_201_CREATED)
async def upload_device_media(
    command_id: UUID,
    request: Request,
    db: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    """Accept one JPEG capture correlated to a durable device command."""

    authorization = request.headers.get("authorization", "")
    scheme, separator, upload_token = authorization.partition(" ")
    if scheme != "DeviceUpload" or separator != " " or not upload_token or " " in upload_token:
        raise APIError(status.HTTP_401_UNAUTHORIZED, "AUTH_FAILED")
    if request.headers.get("content-type") != "image/jpeg":
        raise APIError(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "INVALID_MEDIA")
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            declared_bytes = int(declared_length)
        except ValueError as error:
            raise APIError(status.HTTP_400_BAD_REQUEST, "INVALID_MEDIA") from error
        if declared_bytes < 0:
            raise APIError(status.HTTP_400_BAD_REQUEST, "INVALID_MEDIA")
        if declared_bytes > settings.max_upload_bytes:
            raise APIError(status.HTTP_413_CONTENT_TOO_LARGE, "MEDIA_TOO_LARGE")
    command = await db.scalar(
        select(DeviceCommand)
        .join(Device, Device.id == DeviceCommand.device_id)
        .where(DeviceCommand.id == command_id)
    )
    if command is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "INVALID_CORRELATION")
    if command.command != "camera.capture" or command.status not in {
        CommandStatus.ACKNOWLEDGED,
        CommandStatus.COMPLETED,
    }:
        raise APIError(status.HTTP_409_CONFLICT, "INVALID_CORRELATION")
    device = await db.get(Device, command.device_id)
    assert device is not None
    if not await device_connections.verify_upload_token(device.id, upload_token):
        raise APIError(status.HTTP_401_UNAUTHORIZED, "AUTH_FAILED")

    storage = FilesystemMediaStorage(
        root=settings.media_root,
        max_upload_bytes=settings.max_upload_bytes,
    )
    try:
        saved = await storage.save_jpeg(
            home_id=device.home_id,
            device_id=device.id,
            content=request.stream(),
        )
    except InvalidMedia as error:
        raise APIError(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "INVALID_MEDIA") from error
    except MediaTooLarge as error:
        raise APIError(status.HTTP_413_CONTENT_TOO_LARGE, "MEDIA_TOO_LARGE") from error

    media_id = uuid4()
    event_id = uuid4()
    event = Event(
        id=event_id,
        home_id=device.home_id,
        device_id=device.id,
        event_type="camera_capture",
        payload={"command_id": str(command.id), "media_id": str(media_id)},
    )
    media = Media(
        id=media_id,
        home_id=device.home_id,
        device_id=device.id,
        command_id=command.id,
        event_id=event_id,
        media_type=MediaType.IMAGE,
        content_type="image/jpeg",
        path=saved.relative_path.as_posix(),
        size_bytes=saved.size_bytes,
    )
    db.add_all([event, media])
    try:
        await db.commit()
    except BaseException:
        await db.rollback()
        storage.delete(saved.relative_path)
        raise
    return {
        "id": media.id,
        "home_id": media.home_id,
        "device_id": media.device_id,
        "command_id": media.command_id,
        "content_type": "image/jpeg",
        "size_bytes": saved.size_bytes,
        "url": f"/api/homes/{media.home_id}/media/{media.id}",
    }


@router.get("/api/homes/{home_id}/media/{media_id}", response_class=FileResponse)
async def download_media(
    home_id: UUID,
    media_id: UUID,
    db: DatabaseSession,
    settings: ApplicationSettings,
    _: CameraViewPermission,
) -> FileResponse:
    """Stream one home-scoped JPEG only after browser-session RBAC succeeds."""

    media = await db.scalar(select(Media).where(Media.id == media_id, Media.home_id == home_id))
    if media is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "NOT_FOUND")
    storage = FilesystemMediaStorage(
        root=settings.media_root,
        max_upload_bytes=settings.max_upload_bytes,
    )
    try:
        path = storage.resolve(media.path)
    except UnsafeMediaPath as error:
        raise APIError(status.HTTP_404_NOT_FOUND, "NOT_FOUND") from error
    if not path.is_file():
        raise APIError(status.HTTP_404_NOT_FOUND, "NOT_FOUND")
    return FileResponse(path=path, media_type=media.content_type)
