"""PAUD V1 binary PCM16 audio frame codec."""

from dataclasses import dataclass
from enum import IntEnum
from struct import Struct
from typing import Literal, NoReturn
from uuid import UUID

MAGIC = b"PAUD"
VERSION = 1
HEADER = Struct(">4sBB16sQI")
AUDIO_MAX_SEQUENCE = 18_446_744_073_709_551_615
DEFAULT_MAX_PAYLOAD_BYTES = 960

AudioFrameErrorCode = Literal[
    "INVALID_AUDIO_FRAME", "INVALID_SEQUENCE", "STREAM_NOT_FOUND", "STREAM_NOT_OWNED"
]


class AudioDirection(IntEnum):
    """Allowed PAUD frame directions."""

    DEVICE_TO_SERVER = 1
    SERVER_TO_DEVICE = 2


@dataclass(frozen=True)
class AudioFrame:
    """One ephemeral stream frame; ``stream_id`` is never a conversation ID."""

    stream_id: UUID
    seq: int
    direction: AudioDirection
    payload: bytes


class AudioFrameError(ValueError):
    """A PAUD failure represented by a stable public error code."""

    def __init__(self, code: AudioFrameErrorCode) -> None:
        self.code: AudioFrameErrorCode = code
        super().__init__(code)


def _invalid_audio_frame() -> NoReturn:
    raise AudioFrameError("INVALID_AUDIO_FRAME")


def _validate_sequence(seq: object) -> int:
    if isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= AUDIO_MAX_SEQUENCE:
        raise AudioFrameError("INVALID_SEQUENCE")
    return seq


def _validate_payload(payload: object, max_payload_bytes: object) -> bytes:
    if isinstance(max_payload_bytes, bool) or not isinstance(max_payload_bytes, int):
        _invalid_audio_frame()
    if max_payload_bytes < 0 or not isinstance(payload, bytes):
        _invalid_audio_frame()
    if not payload or len(payload) % 2 or len(payload) > max_payload_bytes:
        _invalid_audio_frame()
    return payload


def _validate_direction(direction: object) -> AudioDirection:
    if not isinstance(direction, AudioDirection):
        _invalid_audio_frame()
    return direction


def encode_audio_frame(frame: AudioFrame) -> bytes:
    """Encode a validated PCM16 frame with the exact 34-byte PAUD V1 header."""

    if not isinstance(frame.stream_id, UUID):
        _invalid_audio_frame()
    seq = _validate_sequence(frame.seq)
    direction = _validate_direction(frame.direction)
    payload = _validate_payload(frame.payload, DEFAULT_MAX_PAYLOAD_BYTES)
    header = HEADER.pack(MAGIC, VERSION, int(direction), frame.stream_id.bytes, seq, len(payload))
    return header + payload


def decode_audio_frame(
    data: bytes,
    *,
    expected_direction: AudioDirection,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> AudioFrame:
    """Decode one complete PAUD V1 frame after validating its wire constraints."""

    if not isinstance(data, bytes) or len(data) < HEADER.size:
        _invalid_audio_frame()
    magic, version, raw_direction, stream_id_bytes, raw_seq, payload_length = HEADER.unpack(
        data[: HEADER.size]
    )
    payload = data[HEADER.size :]
    if payload_length != len(payload) or magic != MAGIC or version != VERSION:
        _invalid_audio_frame()
    try:
        direction = AudioDirection(raw_direction)
    except ValueError:
        _invalid_audio_frame()
    _validate_direction(expected_direction)
    if direction != expected_direction:
        _invalid_audio_frame()
    seq = _validate_sequence(raw_seq)
    validated_payload = _validate_payload(payload, max_payload_bytes)
    return AudioFrame(UUID(bytes=stream_id_bytes), seq, direction, validated_payload)
