"""Environment-backed runtime configuration for the device simulator."""

from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DeviceId = Annotated[str, StringConstraints(pattern=r"^PI-[0-9]{6}$", strict=True)]
Health = Literal["ready", "unavailable", "error"]


class SimulatorSettings(BaseSettings):
    """Validated simulator settings loaded from CLI values or environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    backend_url: str = Field(
        default="http://127.0.0.1:8000/",
        validation_alias="BACKEND_URL",
    )
    device_id: DeviceId = Field(validation_alias="DEVICE_ID")
    device_secret: SecretStr = Field(validation_alias="DEVICE_SECRET")
    image: Path | None = Field(default=None, validation_alias="DEVICE_IMAGE")
    firmware_version: str = Field(
        default="simulator-1.0.0",
        min_length=1,
        max_length=64,
        validation_alias="DEVICE_FIRMWARE_VERSION",
    )
    hardware_model: str = Field(
        default="PORTERO-SIMULATOR-V1",
        min_length=1,
        max_length=128,
        validation_alias="DEVICE_HARDWARE_MODEL",
    )
    ethernet: Literal["online", "offline", "error"] = Field(
        default="online",
        validation_alias="DEVICE_ETHERNET",
    )
    camera: Health = Field(default="ready", validation_alias="DEVICE_CAMERA")
    microphone: Health = Field(default="unavailable", validation_alias="DEVICE_MICROPHONE")
    speaker: Health = Field(default="unavailable", validation_alias="DEVICE_SPEAKER")
    free_heap_bytes: int = Field(
        default=262_144,
        ge=0,
        le=9_223_372_036_854_775_807,
        validation_alias="DEVICE_FREE_HEAP_BYTES",
    )
    reconnect_max_seconds: float = Field(
        default=30,
        gt=0,
        validation_alias="DEVICE_RECONNECT_MAX_SECONDS",
    )
    reconnect_jitter: bool = Field(default=True, validation_alias="DEVICE_RECONNECT_JITTER")
    http_timeout_seconds: float = Field(
        default=30,
        gt=0,
        validation_alias="DEVICE_HTTP_TIMEOUT_SECONDS",
    )
    max_websocket_message_bytes: int = Field(
        default=262_144,
        gt=0,
        validation_alias="MAX_WEBSOCKET_MESSAGE_BYTES",
    )

    @field_validator("backend_url")
    @classmethod
    def validate_backend_url(cls, value: str) -> str:
        """Require a root HTTP origin because route paths are fixed by protocol V1."""

        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("BACKEND_URL must be an HTTP(S) origin without a path")
        return value if value.endswith("/") else f"{value}/"

    @property
    def websocket_url(self) -> str:
        """Map the configured backend origin onto the fixed device WebSocket route."""

        parsed = urlsplit(self.backend_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, "/ws/device", "", ""))
