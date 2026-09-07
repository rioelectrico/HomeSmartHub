"""SQL aggregates for the home's current day and preceding six calendar days."""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import APIError
from app.models import Conversation, Device, Event, Home, Media
from app.schemas.statistics import ActivityCounts, DailyActivity, DeviceCounts, StatisticsResponse


async def statistics(
    db: AsyncSession, home_id: UUID, *, now: datetime | None = None
) -> StatisticsResponse:
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    home_timezone = await db.scalar(select(Home.timezone).where(Home.id == home_id))
    if home_timezone is None:
        raise APIError(404, "NOT_FOUND")
    timezone = ZoneInfo(home_timezone)
    local_today = checked_at.astimezone(timezone).date()
    start_date = local_today - timedelta(days=6)
    start = datetime.combine(start_date, time.min, timezone).astimezone(UTC)
    daily = [DailyActivity(date=start_date + timedelta(days=i)) for i in range(7)]
    totals = ActivityCounts()
    today_counts = ActivityCounts()
    for model, name in ((Event, "events"), (Conversation, "conversations"), (Media, "captures")):
        day = cast(func.timezone(home_timezone, model.created_at), Date)
        rows = (
            await db.execute(
                select(day, func.count())
                .where(
                    model.home_id == home_id,
                    model.created_at >= start,
                    model.created_at <= checked_at,
                )
                .group_by(day)
            )
        ).all()
        for date_value, count in rows:
            setattr(daily[(date_value - start_date).days], name, count)
        setattr(totals, name, sum(count for _, count in rows))
        setattr(today_counts, name, getattr(daily[-1], name))
    counts = (
        await db.execute(
            select(Device.status, func.count())
            .where(Device.home_id == home_id)
            .group_by(Device.status)
        )
    ).all()
    devices = DeviceCounts(total=sum(count for _, count in counts))
    for status, count in counts:
        setattr(devices, status.value, count)
    return StatisticsResponse(
        generated_at=checked_at,
        timezone=home_timezone,
        today=today_counts,
        last_7_days=totals,
        daily=daily,
        devices=devices,
    )
