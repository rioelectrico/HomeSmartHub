"""Filesystem-backed JPEG storage contracts."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from app.media.storage import FilesystemMediaStorage
from app.media.validation import MediaTooLarge, UnsafeMediaPath


async def aiter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.fixture
def jpeg_bytes() -> bytes:
    return b"\xff\xd8camera-payload\xff\xd9"


@pytest.fixture
def storage(tmp_path):
    return FilesystemMediaStorage(root=tmp_path, max_upload_bytes=1024)


async def test_storage_generates_server_path_and_writes_atomically(storage, jpeg_bytes):
    """A saved capture must use only server-derived safe path segments."""

    saved = await storage.save_jpeg(
        home_id=uuid4(),
        device_id=uuid4(),
        content=aiter([jpeg_bytes]),
    )

    assert saved.relative_path.suffix == ".jpg"
    assert ".." not in saved.relative_path.parts
    assert (storage.root / saved.relative_path).read_bytes() == jpeg_bytes
    assert list((storage.root / saved.relative_path.parent).glob("*.tmp")) == []


async def test_storage_aborts_and_removes_temporary_file_when_stream_exceeds_limit(tmp_path):
    """Removing the streaming byte counter must allow this oversized body through."""

    storage = FilesystemMediaStorage(root=tmp_path, max_upload_bytes=8)

    with pytest.raises(MediaTooLarge):
        await storage.save_jpeg(
            home_id=uuid4(),
            device_id=uuid4(),
            content=aiter([b"\xff\xd8abcd", b"ef\xff\xd9"]),
        )

    assert list(tmp_path.rglob("*.jpg")) == []
    assert list(tmp_path.rglob("*.tmp")) == []


def test_storage_rejects_path_traversal_when_resolving_persisted_path(tmp_path):
    """Dropping root confinement must expose this attacker-controlled DB path."""

    storage = FilesystemMediaStorage(root=tmp_path / "media", max_upload_bytes=1024)

    with pytest.raises(UnsafeMediaPath):
        storage.resolve("../outside.jpg")
