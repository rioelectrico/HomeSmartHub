"""JPEG capture and upload helpers for the device simulator."""

from importlib import resources
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx

JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


class InvalidJpeg(ValueError):
    """Raised when a configured capture does not have JPEG boundary markers."""


def _read_default_asset() -> bytes:
    source_asset = Path(__file__).resolve().parents[2] / "assets" / "test-pattern.jpg"
    if source_asset.is_file():
        return source_asset.read_bytes()
    return resources.files("portero_simulator").joinpath("assets/test-pattern.jpg").read_bytes()


def load_jpeg(image: Path | None = None) -> bytes:
    """Load a capture without transforming its raw JPEG bytes."""

    jpeg = image.read_bytes() if image is not None else _read_default_asset()
    if not jpeg.startswith(JPEG_START) or not jpeg.endswith(JPEG_END):
        raise InvalidJpeg("JPEG signature is invalid")
    return jpeg


async def upload_jpeg(
    client: httpx.AsyncClient,
    *,
    backend_url: str,
    command_id: UUID,
    upload_token: str,
    jpeg: bytes,
) -> dict[str, object]:
    """Upload raw JPEG bytes using the backend's connection-bound contract."""

    response = await client.post(
        f"{backend_url.rstrip('/')}/api/device/media",
        params={"command_id": str(command_id)},
        headers={
            "authorization": f"DeviceUpload {upload_token}",
            "content-type": "image/jpeg",
        },
        content=jpeg,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("media upload response must be a JSON object")
    return cast(dict[str, object], payload)
