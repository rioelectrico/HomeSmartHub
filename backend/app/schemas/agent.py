"""HTTP contracts for a home's agent configuration."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentConfigRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(min_length=1, max_length=8000)
    language: str = Field(min_length=2, max_length=32)
    voice: str = Field(min_length=1, max_length=128)
    voice_speed: float = Field(ge=0.25, le=1.5)
    realtime_model: str = Field(min_length=1, max_length=128)
    openai_session_options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool


class AgentConfigResponse(AgentConfigRequest):
    updated_at: datetime | None
