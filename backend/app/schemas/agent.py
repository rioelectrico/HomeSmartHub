"""HTTP contracts for a home's agent configuration."""

from datetime import datetime

from pydantic import BaseModel, Field


class AgentConfigRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(min_length=1, max_length=8000)
    language: str = Field(min_length=2, max_length=32)
    voice: str = Field(min_length=1, max_length=128)
    voice_speed: float = Field(ge=0.5, le=2.0)
    realtime_model: str = Field(min_length=1, max_length=128)
    enabled: bool


class AgentConfigResponse(AgentConfigRequest):
    updated_at: datetime | None
