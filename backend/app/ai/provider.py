"""Safe configuration contract for OpenAI Realtime providers."""

from enum import StrEnum
from typing import Protocol

from app.config import Settings


class ProviderStatus(StrEnum):
    UNCONFIGURED = "unconfigured"
    CONFIGURED = "configured"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class AIRealtimeProvider(Protocol):
    def diagnose(self) -> ProviderStatus: ...


def resolve_realtime_model(agent_model: str, settings: Settings) -> str:
    """Resolve the environment default only when an agent explicitly requests it."""

    return settings.openai_realtime_model if agent_model == "env-default" else agent_model


class ConfiguredOpenAIRealtimeProvider:
    """Report credential configuration without opening a network connection.

    The real transport extends this safe initial diagnosis with observed
    availability after a connection attempt.
    """

    def __init__(self, settings: Settings) -> None:
        key = settings.openai_api_key
        key_value = key.get_secret_value().strip() if key is not None else ""
        self._status = (
            ProviderStatus.CONFIGURED
            if key_value and key_value != "sk-example-not-a-real-key"
            else ProviderStatus.UNCONFIGURED
        )

    def diagnose(self) -> ProviderStatus:
        return self._status
