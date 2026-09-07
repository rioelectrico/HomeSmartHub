"""PAUD binary audio frame contracts."""

from struct import pack
from uuid import UUID

import pytest

from app.devices.audio_protocol import (
    AUDIO_MAX_SEQUENCE,
    AudioDirection,
    AudioFrame,
    AudioFrameError,
    decode_audio_frame,
    encode_audio_frame,
)

STREAM_ID = UUID("123e4567-e89b-12d3-a456-426614174000")
PAYLOAD = b"\x00\x00" * 480


def _wire(
    *,
    magic: bytes = b"PAUD",
    version: int = 1,
    direction: int = 1,
    stream_id: UUID = STREAM_ID,
    seq: int = 1,
    payload_length: int | None = None,
    payload: bytes = PAYLOAD,
) -> bytes:
    """Build controlled wire bytes without using the codec under test."""

    length = len(payload) if payload_length is None else payload_length
    return pack(">4sBB16sQI", magic, version, direction, stream_id.bytes, seq, length) + payload


def test_audio_frame_round_trip() -> None:
    """Changing any PAUD header field or payload ordering breaks binary interoperability."""

    frame = AudioFrame(STREAM_ID, 1, AudioDirection.DEVICE_TO_SERVER, PAYLOAD)

    encoded = encode_audio_frame(frame)

    assert len(encoded) == 34 + 960
    assert encoded[:34] == _wire(payload=PAYLOAD)[:34]
    assert decode_audio_frame(encoded, expected_direction=AudioDirection.DEVICE_TO_SERVER) == frame


@pytest.mark.parametrize("seq", [1, AUDIO_MAX_SEQUENCE])
def test_audio_frame_accepts_binary_sequence_bounds(seq: int) -> None:
    """Narrowing the independent unsigned binary sequence range rejects valid frames."""

    frame = AudioFrame(STREAM_ID, seq, AudioDirection.SERVER_TO_DEVICE, b"\x01\x02")

    assert (
        decode_audio_frame(
            encode_audio_frame(frame), expected_direction=AudioDirection.SERVER_TO_DEVICE
        )
        == frame
    )


@pytest.mark.parametrize(
    ("data", "expected_direction", "code"),
    [
        (_wire(magic=b"NOPE"), AudioDirection.DEVICE_TO_SERVER, "INVALID_AUDIO_FRAME"),
        (_wire(version=2), AudioDirection.DEVICE_TO_SERVER, "INVALID_AUDIO_FRAME"),
        (_wire(direction=3), AudioDirection.DEVICE_TO_SERVER, "INVALID_AUDIO_FRAME"),
        (_wire(payload=b""), AudioDirection.DEVICE_TO_SERVER, "INVALID_AUDIO_FRAME"),
        (_wire(payload=b"\x00"), AudioDirection.DEVICE_TO_SERVER, "INVALID_AUDIO_FRAME"),
        (_wire(payload=b"\x00\x00" * 481), AudioDirection.DEVICE_TO_SERVER, "INVALID_AUDIO_FRAME"),
        (
            _wire(payload_length=len(PAYLOAD) + 2),
            AudioDirection.DEVICE_TO_SERVER,
            "INVALID_AUDIO_FRAME",
        ),
        (_wire(seq=0), AudioDirection.DEVICE_TO_SERVER, "INVALID_SEQUENCE"),
        (_wire(direction=2), AudioDirection.DEVICE_TO_SERVER, "INVALID_AUDIO_FRAME"),
        (b"PAUD", AudioDirection.DEVICE_TO_SERVER, "INVALID_AUDIO_FRAME"),
    ],
    ids=[
        "bad_magic",
        "bad_version",
        "bad_direction",
        "empty_payload",
        "odd_payload",
        "oversized_payload",
        "mismatched_payload_length",
        "zero_sequence",
        "wrong_direction",
        "truncated_header",
    ],
)
def test_decode_rejects_invalid_wire_frames(
    data: bytes, expected_direction: AudioDirection, code: str
) -> None:
    """Relaxing a PAUD validation branch admits malformed or misrouted audio."""

    with pytest.raises(AudioFrameError) as error:
        decode_audio_frame(data, expected_direction=expected_direction)

    assert error.value.code == code


def test_decode_honors_configured_payload_cap() -> None:
    """Ignoring the configured cap allows an endpoint to exceed its audio memory budget."""

    data = _wire(payload=b"\x00\x00" * 481)

    frame = decode_audio_frame(
        data, expected_direction=AudioDirection.DEVICE_TO_SERVER, max_payload_bytes=962
    )

    assert frame.payload == b"\x00\x00" * 481


@pytest.mark.parametrize(
    "frame,code",
    [
        (
            AudioFrame(STREAM_ID, 0, AudioDirection.DEVICE_TO_SERVER, b"\x00\x00"),
            "INVALID_SEQUENCE",
        ),
        (
            AudioFrame(
                STREAM_ID, AUDIO_MAX_SEQUENCE + 1, AudioDirection.DEVICE_TO_SERVER, b"\x00\x00"
            ),
            "INVALID_SEQUENCE",
        ),
        (AudioFrame(STREAM_ID, 1, AudioDirection.DEVICE_TO_SERVER, b""), "INVALID_AUDIO_FRAME"),
        (AudioFrame(STREAM_ID, 1, AudioDirection.DEVICE_TO_SERVER, b"\x00"), "INVALID_AUDIO_FRAME"),
        (
            AudioFrame(STREAM_ID, 1, AudioDirection.DEVICE_TO_SERVER, b"\x00\x00" * 481),
            "INVALID_AUDIO_FRAME",
        ),
    ],
)
def test_encode_validates_frame_before_constructing_wire_bytes(
    frame: AudioFrame, code: str
) -> None:
    """Packing before validation leaks low-level errors and permits invalid PAUD frames."""

    with pytest.raises(AudioFrameError) as error:
        encode_audio_frame(frame)

    assert error.value.code == code
