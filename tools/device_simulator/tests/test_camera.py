from pathlib import Path
from uuid import UUID

import httpx
import pytest

from portero_simulator.camera import InvalidJpeg, load_jpeg, upload_jpeg


def test_load_jpeg_returns_exact_raw_bytes(tmp_path: Path) -> None:
    """Replacing capture bytes with Base64 or another representation must fail."""

    expected = b"\xff\xd8camera-payload\xff\xd9"
    image = tmp_path / "capture.jpg"
    image.write_bytes(expected)

    assert load_jpeg(image) == expected


def test_load_jpeg_rejects_invalid_signature(tmp_path: Path) -> None:
    """A non-JPEG file must not be presented to the backend as image/jpeg."""

    image = tmp_path / "capture.jpg"
    image.write_bytes(b"not-a-jpeg")

    with pytest.raises(InvalidJpeg, match="JPEG signature"):
        load_jpeg(image)


def test_bundled_test_pattern_is_a_jpeg() -> None:
    """Packaging without the default capture asset must break zero-config capture."""

    jpeg = load_jpeg()

    assert jpeg.startswith(b"\xff\xd8")
    assert jpeg.endswith(b"\xff\xd9")
    assert len(jpeg) > 100


async def test_upload_sends_raw_jpeg_with_backend_contract() -> None:
    """Changing auth, correlation, MIME, or body encoding must break device upload."""

    command_id = UUID("3029e584-27ad-4b15-bae6-b470b6ea14ac")
    jpeg = b"\xff\xd8camera-payload\xff\xd9"

    async def backend(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL(
            "http://backend:8000/api/device/media",
            params={"command_id": str(command_id)},
        )
        assert request.headers["authorization"] == "DeviceUpload upload-token"
        assert request.headers["content-type"] == "image/jpeg"
        assert await request.aread() == jpeg
        return httpx.Response(
            201,
            json={
                "id": "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
                "home_id": "b73e6650-9f82-4822-b7b3-bc17a4e0da94",
                "device_id": "d5af9bf4-86f7-468f-b062-f62592561b42",
                "command_id": str(command_id),
                "content_type": "image/jpeg",
                "size_bytes": len(jpeg),
                "url": "/api/homes/b73e6650-9f82-4822-b7b3-bc17a4e0da94/media/"
                "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        result = await upload_jpeg(
            client,
            backend_url="http://backend:8000/",
            command_id=command_id,
            upload_token="upload-token",
            jpeg=jpeg,
        )

    assert result == {
        "id": "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
        "home_id": "b73e6650-9f82-4822-b7b3-bc17a4e0da94",
        "device_id": "d5af9bf4-86f7-468f-b062-f62592561b42",
        "command_id": str(command_id),
        "content_type": "image/jpeg",
        "size_bytes": len(jpeg),
        "url": "/api/homes/b73e6650-9f82-4822-b7b3-bc17a4e0da94/media/"
        "72e790eb-1cb2-4c3b-993d-7eb03bdabf59",
    }
