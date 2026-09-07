"""Home-local calendar aggregate responses."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class ActivityCounts(BaseModel):
    events: int = Field(default=0, ge=0)
    conversations: int = Field(default=0, ge=0)
    captures: int = Field(default=0, ge=0)


class DailyActivity(ActivityCounts):
    date: date


class DeviceCounts(BaseModel):
    total: int = 0
    online: int = 0
    offline: int = 0
    disabled: int = 0
    provisioning: int = 0


class StatisticsResponse(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)
    generated_at: datetime
    today: ActivityCounts
    last_7_days: ActivityCounts
    daily: list[DailyActivity] = Field(min_length=7, max_length=7)
    devices: DeviceCounts
