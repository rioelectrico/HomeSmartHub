"""Filesystem storage with bounded streaming and atomic publication."""

import os
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from app.media.validation import MediaTooLarge, UnsafeMediaPath, validate_jpeg_signature


@dataclass(frozen=True, slots=True)
class SavedMedia:
    """Metadata returned after a file becomes atomically visible."""

    relative_path: Path
    size_bytes: int


class MediaStorage(Protocol):
    """Storage boundary used by media API routes."""

    async def save_jpeg(
        self,
        *,
        home_id: UUID,
        device_id: UUID,
        content: AsyncIterable[bytes],
    ) -> SavedMedia: ...

    def resolve(self, relative_path: Path | str) -> Path: ...

    def delete(self, relative_path: Path | str) -> None: ...


class FilesystemMediaStorage:
    """Persist JPEG captures beneath a server-controlled directory tree."""

    def __init__(self, *, root: Path, max_upload_bytes: int) -> None:
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        self.root = root
        self.max_upload_bytes = max_upload_bytes

    async def save_jpeg(
        self,
        *,
        home_id: UUID,
        device_id: UUID,
        content: AsyncIterable[bytes],
    ) -> SavedMedia:
        """Stream a bounded JPEG into a temporary file, then atomically publish it."""

        del device_id  # Device identity is accepted for storage-provider compatibility.
        now = datetime.now(UTC)
        relative_path = Path(str(home_id), f"{now.year:04d}", f"{now.month:02d}", f"{uuid4()}.jpg")
        final_path = self.resolve(relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = final_path.with_name(f".{final_path.stem}.{uuid4()}.tmp")

        size_bytes = 0
        prefix = bytearray()
        suffix = b""
        try:
            with temporary_path.open("xb") as destination:
                async for chunk in content:
                    size_bytes += len(chunk)
                    if size_bytes > self.max_upload_bytes:
                        raise MediaTooLarge("upload exceeds max_upload_bytes")
                    if len(prefix) < 2:
                        prefix.extend(chunk[: 2 - len(prefix)])
                    suffix = (suffix + chunk)[-2:]
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            validate_jpeg_signature(bytes(prefix), suffix)
            os.replace(temporary_path, final_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return SavedMedia(relative_path=relative_path, size_bytes=size_bytes)

    def resolve(self, relative_path: Path | str) -> Path:
        """Resolve one persisted path and reject absolute or escaping paths."""

        requested = Path(relative_path)
        if requested.is_absolute():
            raise UnsafeMediaPath("absolute media paths are forbidden")
        root = self.root.resolve()
        candidate = (root / requested).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise UnsafeMediaPath("media path escapes the configured root") from error
        return candidate

    def delete(self, relative_path: Path | str) -> None:
        """Remove a stored object while retaining the same root confinement check."""

        self.resolve(relative_path).unlink(missing_ok=True)
