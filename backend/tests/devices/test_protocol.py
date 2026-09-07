"""Strict protocol V1 parsing contracts."""

from typing import get_args
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from app.devices.protocol import parse_device_message
from app.schemas.conversations import (
    ConversationAudioClear,
    ConversationAudioFormat,
    ConversationEnded,
    ConversationError,
    ConversationErrorCode,
    ConversationStarted,
    ConversationStateMessage,
    ConversationStop,
    ConversationTranscript,
)
from app.schemas.devices import (
    AuthResponse,
    CommandAck,
    CommandResult,
    DeviceHeartbeat,
    DeviceHello,
    DeviceStatusMessage,
)


@pytest.mark.parametrize(
    "payload,expected_type",
    [
        (
            {
                "type": "device.hello",
                "version": "v1",
                "device_id": "PI-000001",
                "boot_id": "boot-a",
                "seq": 0,
            },
            DeviceHello,
        ),
        (
            {
                "type": "auth.response",
                "boot_id": "boot-a",
                "seq": 1,
                "nonce": "n" * 32,
                "digest": "a" * 64,
            },
            AuthResponse,
        ),
        (
            {
                "type": "device.heartbeat",
                "boot_id": "boot-a",
                "seq": 2,
                "uptime_seconds": 5,
            },
            DeviceHeartbeat,
        ),
        (
            {
                "type": "device.status",
                "boot_id": "boot-a",
                "seq": 3,
                "firmware_version": "1.0.0",
                "hardware_model": "ESP32-P4",
                "uptime_seconds": 5,
                "ethernet": "online",
                "camera": "ready",
                "microphone": "unavailable",
                "speaker": "error",
                "free_heap_bytes": 1024,
            },
            DeviceStatusMessage,
        ),
        (
            {
                "type": "command.ack",
                "boot_id": "boot-a",
                "seq": 4,
                "command_id": "3029e584-27ad-4b15-bae6-b470b6ea14ac",
                "status": "accepted",
            },
            CommandAck,
        ),
        (
            {
                "type": "command.result",
                "boot_id": "boot-a",
                "seq": 5,
                "command_id": "3029e584-27ad-4b15-bae6-b470b6ea14ac",
                "status": "completed",
                "result": {"captured": True},
            },
            CommandResult,
        ),
    ],
)
def test_parser_selects_each_discriminated_message(payload, expected_type) -> None:
    """Dropping a discriminator branch would reject a documented V1 message."""

    parsed = parse_device_message(payload)

    assert isinstance(parsed, expected_type)
    if isinstance(parsed, (CommandAck, CommandResult)):
        assert isinstance(parsed.command_id, UUID)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "device.hello",
            "version": "v1",
            "device_id": "pi-000001",
            "boot_id": "boot-a",
            "seq": 0,
        },
        {
            "type": "device.hello",
            "version": "v2",
            "device_id": "PI-000001",
            "boot_id": "boot-a",
            "seq": 0,
        },
        {
            "type": "device.hello",
            "version": "v1",
            "device_id": "PI-000001",
            "boot_id": "boot-a",
            "seq": -1,
        },
        {
            "type": "device.hello",
            "version": "v1",
            "device_id": "PI-000001",
            "boot_id": "boot-a",
            "seq": 0,
            "unexpected": True,
        },
        {
            "type": "auth.response",
            "boot_id": "boot-a",
            "seq": 1,
            "nonce": "short",
            "digest": "not-a-sha256",
        },
        {"type": "unknown", "boot_id": "boot-a", "seq": 1},
    ],
)
def test_parser_rejects_invalid_ids_versions_sequences_sizes_and_extras(payload) -> None:
    """Relaxing identifiers, bounds, enums, or extras would admit incompatible input."""

    with pytest.raises(ValidationError):
        parse_device_message(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "command.ack",
            "boot_id": "boot-a",
            "seq": 4,
            "command_id": "3029e584-27ad-4b15-bae6-b470b6ea14ac",
            "status": "accepted",
            "error_code": "DEVICE_BUSY",
        },
        {
            "type": "command.ack",
            "boot_id": "boot-a",
            "seq": 4,
            "command_id": "3029e584-27ad-4b15-bae6-b470b6ea14ac",
            "status": "rejected",
        },
        {
            "type": "command.result",
            "boot_id": "boot-a",
            "seq": 5,
            "command_id": "3029e584-27ad-4b15-bae6-b470b6ea14ac",
            "status": "completed",
            "result": {},
            "error_code": "INTERNAL_ERROR",
        },
        {
            "type": "command.result",
            "boot_id": "boot-a",
            "seq": 5,
            "command_id": "3029e584-27ad-4b15-bae6-b470b6ea14ac",
            "status": "failed",
            "result": {},
        },
        {
            "type": "command.result",
            "boot_id": "boot-a",
            "seq": 5,
            "command_id": "3029e584-27ad-4b15-bae6-b470b6ea14ac",
            "status": "failed",
            "result": {},
            "error_code": "MADE_UP_ERROR",
        },
    ],
)
def test_command_replies_enforce_status_error_code_contract(payload) -> None:
    """Contradictory or unknown failure metadata must not enter lifecycle handling."""

    with pytest.raises(ValidationError):
        parse_device_message(payload)


def test_hello_accepts_the_pcm16_capability() -> None:
    """Dropping the negotiated audio capability would prevent SIM-1 audio."""

    message = parse_device_message(
        {
            "type": "device.hello",
            "version": "v1",
            "device_id": "PI-000001",
            "boot_id": "boot-a",
            "seq": 0,
            "capabilities": ["audio_pcm16_v1"],
        }
    )

    assert isinstance(message, DeviceHello)
    assert message.capabilities == ["audio_pcm16_v1"]


def test_hello_rejects_unknown_capability() -> None:
    """An unnegotiated capability must not silently expand the wire protocol."""

    with pytest.raises(ValidationError):
        parse_device_message(
            {
                "type": "device.hello",
                "version": "v1",
                "device_id": "PI-000001",
                "boot_id": "boot-a",
                "seq": 0,
                "capabilities": ["future_audio_v2"],
            }
        )


def test_conversation_start_is_strict() -> None:
    """Conversation starts only accept server-authorized defaults."""

    message = parse_device_message(
        {
            "type": "conversation.start",
            "boot_id": "boot-a",
            "seq": 2,
            "version": 1,
            "mode": "hands_free",
        }
    )

    from app.schemas.devices import ConversationStart

    assert isinstance(message, ConversationStart)


@pytest.mark.parametrize("field", ["prompt", "model", "voice", "home_id", "tools"])
def test_conversation_start_rejects_unauthorized_configuration(field: str) -> None:
    """Device input must not override backend-selected AI configuration."""

    payload = {
        "type": "conversation.start",
        "boot_id": "boot-a",
        "seq": 2,
        "version": 1,
        "mode": "hands_free",
        field: "forbidden",
    }

    with pytest.raises(ValidationError):
        parse_device_message(payload)


@pytest.mark.parametrize(
    "message",
    [
        ConversationStarted(
            type="conversation.started",
            version=1,
            seq=10,
            conversation_id="123e4567-e89b-12d3-a456-426614174000",
            stream_id="123e4567-e89b-12d3-a456-426614174001",
            audio=ConversationAudioFormat(
                codec="pcm16", sample_rate=24000, channels=1, frame_ms=20
            ),
            max_seconds=300,
        ),
        ConversationStateMessage(
            type="conversation.state",
            version=1,
            seq=11,
            conversation_id="123e4567-e89b-12d3-a456-426614174000",
            state="listening",
        ),
        ConversationTranscript(
            type="conversation.transcript",
            version=1,
            seq=12,
            conversation_id="123e4567-e89b-12d3-a456-426614174000",
            role="assistant",
            text="Buen día.",
            final=True,
        ),
        ConversationAudioClear(
            type="conversation.audio.clear",
            version=1,
            seq=13,
            conversation_id="123e4567-e89b-12d3-a456-426614174000",
            stream_id="123e4567-e89b-12d3-a456-426614174001",
            reason="barge_in",
        ),
        ConversationEnded(
            type="conversation.ended",
            version=1,
            seq=14,
            conversation_id="123e4567-e89b-12d3-a456-426614174000",
            outcome="registered",
            reason="visitor_finished",
        ),
        ConversationError(
            type="conversation.error",
            version=1,
            seq=15,
            code="AI_UNAVAILABLE",
            correlation_id="123e4567-e89b-12d3-a456-426614174002",
        ),
    ],
)
def test_outbound_conversation_messages_reject_extra_fields(message: BaseModel) -> None:
    """Relaxing any outgoing model would permit incompatible server controls."""

    model_type = type(message)
    payload = message.model_dump(mode="json") | {"unexpected": True}

    with pytest.raises(ValidationError):
        model_type.model_validate(payload)


def test_conversation_stop_is_discriminated_and_strict() -> None:
    """Conversation stops retain the V1 boot/sequence envelope and reject extras."""

    payload = {
        "type": "conversation.stop",
        "boot_id": "boot-a",
        "seq": 3,
        "version": 1,
        "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
        "reason": "visitor_finished",
    }

    message = parse_device_message(payload)

    assert isinstance(message, ConversationStop)
    for invalid_payload in (
        payload | {"version": True},
        payload | {"reason": "unknown"},
        payload | {"unexpected": True},
    ):
        with pytest.raises(ValidationError):
            parse_device_message(invalid_payload)


def test_local_output_overflow_control_requires_exact_stream_and_strict_envelope() -> None:
    payload = {
        "type": "conversation.audio.output_overflow",
        "boot_id": "boot-a",
        "seq": 3,
        "version": 1,
        "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
        "stream_id": "123e4567-e89b-12d3-a456-426614174001",
    }
    message = parse_device_message(payload)
    assert message.model_dump(mode="json") == payload
    for patch in (
        {"version": True},
        {"seq": True},
        {"seq": -1},
        {"seq": 3.5},
        {"boot_id": ""},
        {"stream_id": None},
        {"conversation_id": "bad"},
        {"stream_id": payload["conversation_id"]},
        {"code": "AI_UNAVAILABLE"},
    ):
        with pytest.raises(ValidationError):
            parse_device_message(payload | patch)


@pytest.mark.parametrize(
    "construct",
    [
        lambda: parse_device_message(
            {
                "type": "conversation.start",
                "boot_id": "boot-a",
                "seq": 2,
                "version": True,
                "mode": "hands_free",
            }
        ),
        lambda: ConversationStateMessage(
            type="conversation.state",
            version=True,
            seq=11,
            conversation_id="123e4567-e89b-12d3-a456-426614174000",
            state="listening",
        ),
        lambda: ConversationAudioFormat(
            codec="pcm16", sample_rate=24000.0, channels=1, frame_ms=20
        ),
        lambda: ConversationAudioFormat(
            codec="pcm16", sample_rate=24000, channels=True, frame_ms=20
        ),
        lambda: ConversationAudioFormat(
            codec="pcm16", sample_rate=24000, channels=1, frame_ms=20.0
        ),
        lambda: ConversationTranscript(
            type="conversation.transcript",
            version=1,
            seq=12,
            conversation_id="123e4567-e89b-12d3-a456-426614174000",
            role="assistant",
            text="Buen día.",
            final=1,
        ),
    ],
)
def test_conversation_contracts_reject_coerced_scalars(construct) -> None:
    """Numeric and boolean wire literals must not coerce equivalent Python values."""

    with pytest.raises(ValidationError):
        construct()


def test_conversation_started_requires_distinct_durable_and_stream_ids() -> None:
    """Reusing a durable ID as an audio stream ID destroys their wire distinction."""

    with pytest.raises(ValidationError):
        ConversationStarted(
            type="conversation.started",
            version=1,
            seq=10,
            conversation_id="123e4567-e89b-12d3-a456-426614174000",
            stream_id="123e4567-e89b-12d3-a456-426614174000",
            audio=ConversationAudioFormat(
                codec="pcm16", sample_rate=24000, channels=1, frame_ms=20
            ),
            max_seconds=300,
        )


def test_hello_capabilities_are_limited_to_eight() -> None:
    """Unbounded capability lists would expand handshake memory consumption."""

    payload = {
        "type": "device.hello",
        "version": "v1",
        "device_id": "PI-000001",
        "boot_id": "boot-a",
        "seq": 0,
    }

    accepted = parse_device_message(payload | {"capabilities": ["audio_pcm16_v1"] * 8})

    assert isinstance(accepted, DeviceHello)
    with pytest.raises(ValidationError):
        parse_device_message(payload | {"capabilities": ["audio_pcm16_v1"] * 9})


def test_conversation_error_exposes_exact_public_codes_and_generates_correlation_id() -> None:
    """Changing the public error vocabulary or omitting correlation breaks clients."""

    expected_codes = {
        "AI_UNCONFIGURED",
        "AI_UNAVAILABLE",
        "CONVERSATION_ALREADY_ACTIVE",
        "CONVERSATION_NOT_ACTIVE",
        "INVALID_AUDIO_FRAME",
        "AUDIO_INPUT_OVERFLOW",
        "AUDIO_OUTPUT_OVERFLOW",
        "STREAM_NOT_FOUND",
        "STREAM_NOT_OWNED",
        "INVALID_SEQUENCE",
        "CONVERSATION_TIMEOUT",
        "CONVERSATION_IDLE_TIMEOUT",
        "DEVICE_DISCONNECTED",
    }

    error = ConversationError(type="conversation.error", version=1, seq=15, code="AI_UNAVAILABLE")

    assert len(get_args(ConversationErrorCode)) == 13
    assert set(get_args(ConversationErrorCode)) == expected_codes
    assert isinstance(error.correlation_id, UUID)
