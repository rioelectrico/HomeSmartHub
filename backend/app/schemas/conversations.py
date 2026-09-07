"""Strict SIM-1 conversation control-message contracts."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

MAX_JSON_SEQUENCE = 9_223_372_036_854_775_807

ConversationBootIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", strict=True),
]
ConversationSequence = Annotated[StrictInt, Field(ge=0, le=MAX_JSON_SEQUENCE)]
ConversationText = Annotated[str, StringConstraints(min_length=1, max_length=8_000, strict=True)]


def _require_exact_int(value: object) -> object:
    """Reject bools and floats before literal comparison treats them as integers."""

    if type(value) is not int:
        raise ValueError("must be an integer")
    return value


def _require_exact_bool(value: object) -> object:
    """Reject integer stand-ins before literal comparison treats them as booleans."""

    if type(value) is not bool:
        raise ValueError("must be a boolean")
    return value


ConversationVersion = Annotated[Literal[1], BeforeValidator(_require_exact_int)]
Pcm16SampleRate = Annotated[Literal[24_000], BeforeValidator(_require_exact_int)]
Pcm16ChannelCount = Annotated[Literal[1], BeforeValidator(_require_exact_int)]
Pcm16FrameMilliseconds = Annotated[Literal[20], BeforeValidator(_require_exact_int)]
ConversationFinal = Annotated[Literal[True], BeforeValidator(_require_exact_bool)]

ConversationState = Literal[
    "preparing",
    "listening",
    "visitor_speaking",
    "assistant_speaking",
    "error",
]
ConversationOutcomeValue = Literal["registered", "declined", "abandoned", "failed"]
ConversationStopReason = Literal[
    "visitor_finished",
    "device_disconnected",
    "conversation_timeout",
    "conversation_idle_timeout",
    "audio_input_overflow",
    "audio_output_overflow",
]
ConversationErrorCode = Literal[
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
]
ConversationAudioClearReason = Literal["barge_in", "audio_output_overflow"]


class StrictConversationInboundMessage(BaseModel):
    """Base for conversation controls sent by a device."""

    model_config = ConfigDict(extra="forbid")

    boot_id: ConversationBootIdentifier
    seq: ConversationSequence


class ConversationStart(StrictConversationInboundMessage):
    """Request a backend-configured hands-free conversation."""

    type: Literal["conversation.start"]
    version: ConversationVersion
    mode: Literal["hands_free"]


class ConversationStop(StrictConversationInboundMessage):
    """End the currently active conversation at the visitor's request."""

    type: Literal["conversation.stop"]
    version: ConversationVersion
    conversation_id: UUID
    reason: Literal["visitor_finished"]


class ConversationAudioOutputOverflow(StrictConversationInboundMessage):
    """Report terminal local playback saturation for one owned audio stream."""

    type: Literal["conversation.audio.output_overflow"]
    version: ConversationVersion
    conversation_id: UUID
    stream_id: UUID

    @model_validator(mode="after")
    def validate_distinct_ids(self) -> "ConversationAudioOutputOverflow":
        if self.conversation_id == self.stream_id:
            raise ValueError("conversation_id and stream_id must differ")
        return self


class StrictConversationOutboundMessage(BaseModel):
    """Base for server controls, which deliberately have no device boot ID."""

    model_config = ConfigDict(extra="forbid")

    type: str
    version: ConversationVersion
    seq: ConversationSequence


class ConversationAudioFormat(BaseModel):
    """The fixed PCM format negotiated by SIM-1."""

    model_config = ConfigDict(extra="forbid")

    codec: Literal["pcm16"]
    sample_rate: Pcm16SampleRate
    channels: Pcm16ChannelCount
    frame_ms: Pcm16FrameMilliseconds


class ConversationStarted(StrictConversationOutboundMessage):
    """Confirm a durable conversation and its independent ephemeral audio stream."""

    type: Literal["conversation.started"]
    conversation_id: UUID
    stream_id: UUID
    audio: ConversationAudioFormat
    max_seconds: Annotated[StrictInt, Field(gt=0, le=MAX_JSON_SEQUENCE)]

    @model_validator(mode="after")
    def validate_distinct_ids(self) -> "ConversationStarted":
        """Keep durable conversation identity separate from its audio stream identity."""

        if self.conversation_id == self.stream_id:
            raise ValueError("conversation_id and stream_id must differ")
        return self


class ConversationStateMessage(StrictConversationOutboundMessage):
    """Publish the current voice interaction state."""

    type: Literal["conversation.state"]
    conversation_id: UUID
    state: ConversationState


class ConversationTranscript(StrictConversationOutboundMessage):
    """Publish one bounded transcript item."""

    type: Literal["conversation.transcript"]
    conversation_id: UUID
    role: Literal["user", "assistant"]
    text: ConversationText
    final: ConversationFinal


class ConversationAudioClear(StrictConversationOutboundMessage):
    """Instruct the device to discard queued assistant playback."""

    type: Literal["conversation.audio.clear"]
    conversation_id: UUID
    stream_id: UUID
    reason: ConversationAudioClearReason


class ConversationEnded(StrictConversationOutboundMessage):
    """Report the terminal durable conversation outcome."""

    type: Literal["conversation.ended"]
    conversation_id: UUID
    outcome: ConversationOutcomeValue
    reason: ConversationStopReason


class ConversationError(StrictConversationOutboundMessage):
    """Return a stable public error without internal implementation detail."""

    type: Literal["conversation.error"]
    code: ConversationErrorCode
    correlation_id: UUID = Field(default_factory=uuid4)


ConversationOutboundMessage = (
    ConversationStarted
    | ConversationStateMessage
    | ConversationTranscript
    | ConversationAudioClear
    | ConversationEnded
    | ConversationError
)
