"""Validation errors and signature checks for uploaded media."""

JPEG_START_OF_IMAGE = b"\xff\xd8"
JPEG_END_OF_IMAGE = b"\xff\xd9"


class InvalidMedia(ValueError):
    """Raised when uploaded bytes do not match the declared media type."""


class MediaTooLarge(ValueError):
    """Raised as soon as an upload crosses its configured byte limit."""


class UnsafeMediaPath(ValueError):
    """Raised when a persisted path resolves outside the media root."""


def validate_jpeg_signature(prefix: bytes, suffix: bytes) -> None:
    """Require the JPEG SOI and EOI markers without decoding untrusted data."""

    if prefix != JPEG_START_OF_IMAGE or suffix != JPEG_END_OF_IMAGE:
        raise InvalidMedia("JPEG signature is invalid")
