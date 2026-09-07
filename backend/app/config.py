"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic.types import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated settings for the Portero backend.

    Environment variables use the uppercase names documented in the example
    environment files.  ``extra='ignore'`` lets the same environment file be
    shared by the services while each service consumes only its own settings.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="APP_ENV"
    )
    database_url: PostgresDsn = Field(validation_alias="DATABASE_URL")
    app_secret_key: SecretStr = Field(validation_alias="APP_SECRET_KEY")
    device_credential_encryption_key: SecretStr = Field(
        validation_alias="DEVICE_CREDENTIAL_ENCRYPTION_KEY"
    )
    media_root: Path = Field(default=Path("../media"), validation_alias="MEDIA_ROOT")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_realtime_model: str = Field(
        default="gpt-realtime-2.1",
        min_length=1,
        max_length=128,
        validation_alias="OPENAI_REALTIME_MODEL",
    )
    conversation_max_seconds: PositiveInt = Field(
        default=300, validation_alias="CONVERSATION_MAX_SECONDS"
    )
    conversation_idle_seconds: PositiveInt = Field(
        default=45, validation_alias="CONVERSATION_IDLE_SECONDS"
    )
    audio_input_queue_frames: PositiveInt = Field(
        default=50, validation_alias="AUDIO_INPUT_QUEUE_FRAMES"
    )
    audio_output_queue_frames: PositiveInt = Field(
        default=100, validation_alias="AUDIO_OUTPUT_QUEUE_FRAMES"
    )
    openai_realtime_smoke: bool = Field(default=False, validation_alias="OPENAI_REALTIME_SMOKE")
    frontend_origin: str = Field(
        default="http://localhost:3000", validation_alias="FRONTEND_ORIGIN"
    )
    device_heartbeat_interval: PositiveInt = Field(
        default=15, validation_alias="DEVICE_HEARTBEAT_INTERVAL"
    )
    device_offline_timeout: PositiveInt = Field(
        default=45, validation_alias="DEVICE_OFFLINE_TIMEOUT"
    )
    command_timeout_seconds: PositiveInt = Field(
        default=30, validation_alias="COMMAND_TIMEOUT_SECONDS"
    )
    session_ttl_seconds: PositiveInt = Field(default=28800, validation_alias="SESSION_TTL_SECONDS")
    session_cookie_name: str = Field(
        default="portero_session", validation_alias="SESSION_COOKIE_NAME"
    )
    cookie_secure: bool = Field(default=False, validation_alias="COOKIE_SECURE")
    cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax", validation_alias="COOKIE_SAMESITE"
    )
    cookie_path: str = Field(default="/", validation_alias="COOKIE_PATH")
    max_upload_bytes: PositiveInt = Field(default=5_242_880, validation_alias="MAX_UPLOAD_BYTES")
    max_websocket_message_bytes: PositiveInt = Field(
        default=262_144, validation_alias="MAX_WEBSOCKET_MESSAGE_BYTES"
    )
    bootstrap_admin_username: str | None = Field(
        default=None, validation_alias="BOOTSTRAP_ADMIN_USERNAME"
    )
    bootstrap_admin_email: str | None = Field(
        default=None, validation_alias="BOOTSTRAP_ADMIN_EMAIL"
    )
    bootstrap_admin_password: SecretStr | None = Field(
        default=None, validation_alias="BOOTSTRAP_ADMIN_PASSWORD"
    )
    bootstrap_home_name: str = Field(default="Mi hogar", validation_alias="BOOTSTRAP_HOME_NAME")

    @field_validator("frontend_origin")
    @classmethod
    def normalize_frontend_origin(cls, value: str) -> str:
        """Remove trailing slashes so CORS comparisons are deterministic."""

        normalized = value.rstrip("/")
        return normalized or value

    @model_validator(mode="after")
    def validate_device_timeouts(self) -> "Settings":
        """Require the offline threshold to be longer than the heartbeat interval."""

        if self.device_offline_timeout <= self.device_heartbeat_interval:
            raise ValueError(
                "DEVICE_OFFLINE_TIMEOUT must be greater than DEVICE_HEARTBEAT_INTERVAL"
            )
        if self.conversation_idle_seconds >= self.conversation_max_seconds:
            raise ValueError("CONVERSATION_IDLE_SECONDS must be less than CONVERSATION_MAX_SECONDS")
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
