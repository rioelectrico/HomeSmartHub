from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.provider import (
    ConfiguredOpenAIRealtimeProvider,
    ProviderStatus,
)
from app.config import Settings


def valid_env(tmp_path: Path) -> dict[str, object]:
    return {
        "_env_file": None,
        "DATABASE_URL": "postgresql+psycopg://portero:test@localhost:5432/portero_test",
        "APP_SECRET_KEY": "a" * 48,
        "DEVICE_CREDENTIAL_ENCRYPTION_KEY": ("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="),
        "MEDIA_ROOT": tmp_path,
    }


def test_settings_reject_offline_timeout_not_greater_than_heartbeat(
    tmp_path: Path,
) -> None:
    values = valid_env(tmp_path) | {
        "DEVICE_HEARTBEAT_INTERVAL": 30,
        "DEVICE_OFFLINE_TIMEOUT": 30,
    }
    with pytest.raises(ValidationError):
        Settings(**values)


def test_settings_normalize_frontend_origin(tmp_path: Path) -> None:
    settings = Settings(
        **valid_env(tmp_path),
        FRONTEND_ORIGIN="http://localhost:3000/",
    )
    assert settings.frontend_origin == "http://localhost:3000"


def test_settings_require_secure_session_cookie_in_production(tmp_path: Path) -> None:
    """Allowing an insecure production session cookie would expose bearer credentials."""

    with pytest.raises(ValidationError):
        Settings(**valid_env(tmp_path), APP_ENV="production", COOKIE_SECURE="false")


def test_settings_reads_websocket_message_limit(tmp_path: Path) -> None:
    """Ignoring the configured byte limit would leave handshake memory use unbounded."""

    settings = Settings(
        **valid_env(tmp_path),
        MAX_WEBSOCKET_MESSAGE_BYTES=1234,
    )

    assert settings.max_websocket_message_bytes == 1234


def test_sim1_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defaults must bound a SIM-1 voice conversation without an environment override."""

    for name in (
        "OPENAI_REALTIME_MODEL",
        "CONVERSATION_MAX_SECONDS",
        "CONVERSATION_IDLE_SECONDS",
        "AUDIO_INPUT_QUEUE_FRAMES",
        "AUDIO_OUTPUT_QUEUE_FRAMES",
        "OPENAI_REALTIME_SMOKE",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(**valid_env(tmp_path))

    assert settings.openai_realtime_model == "gpt-realtime-2.1"
    assert settings.conversation_max_seconds == 300
    assert settings.conversation_idle_seconds == 45
    assert settings.audio_input_queue_frames == 50
    assert settings.audio_output_queue_frames == 100
    assert settings.openai_realtime_smoke is False


def test_settings_reject_idle_limit_that_is_not_less_than_conversation_limit(
    tmp_path: Path,
) -> None:
    """An idle timeout equal to the total timeout would make the idle guard ineffective."""

    with pytest.raises(ValidationError):
        Settings(
            **valid_env(tmp_path),
            CONVERSATION_MAX_SECONDS=45,
            CONVERSATION_IDLE_SECONDS=45,
        )


def test_placeholder_openai_key_is_unconfigured(tmp_path: Path) -> None:
    """The documented example credential must never appear usable in diagnostics."""

    settings = Settings(**valid_env(tmp_path), OPENAI_API_KEY="sk-example-not-a-real-key")

    assert ConfiguredOpenAIRealtimeProvider(settings).diagnose() == ProviderStatus.UNCONFIGURED


def test_realtime_model_uses_environment_default_only_when_requested(tmp_path: Path) -> None:
    """A persisted model must take precedence unless the agent explicitly selects env-default."""

    from app.ai.provider import resolve_realtime_model

    settings = Settings(**valid_env(tmp_path), OPENAI_REALTIME_MODEL="environment-model")

    assert resolve_realtime_model("env-default", settings) == "environment-model"
    assert resolve_realtime_model("agent-model", settings) == "agent-model"


def test_provider_status_includes_runtime_availability_states() -> None:
    """Realtime adapters need to distinguish configured credentials from observed availability."""

    assert ProviderStatus.AVAILABLE == "available"
    assert ProviderStatus.UNAVAILABLE == "unavailable"
