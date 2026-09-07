"""Home-local calendar windows and SQL aggregates over events, conversations, and captures."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import event

from app.models import Conversation, Device, DeviceStatus, Event, Home, Media
from app.models.activity import MediaType


async def test_statistics_aggregate_home_windows_without_loading_rows(
    operational_client, db, home, async_engine
):
    home.timezone = "UTC"
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    other = Home(name="Other")
    db.add(other)
    await db.flush()
    for scope, moments in (
        (
            home.id,
            [
                midnight,
                midnight - timedelta(days=1),
                midnight - timedelta(days=6),
                midnight - timedelta(days=7),
                now + timedelta(days=1),
            ],
        ),
        (other.id, [midnight]),
    ):
        for i, moment in enumerate(moments):
            db.add_all(
                [
                    Event(home_id=scope, event_type="camera_capture", created_at=moment),
                    Conversation(home_id=scope, created_at=moment),
                    Media(
                        home_id=scope,
                        media_type=MediaType.IMAGE,
                        content_type="image/jpeg",
                        path=f"{scope}/{i}.jpg",
                        size_bytes=16,
                        created_at=moment,
                    ),
                ]
            )
    db.add_all(
        [
            Device(
                home_id=home.id, name="Online", device_id="PI-000001", status=DeviceStatus.ONLINE
            ),
            Device(
                home_id=home.id, name="Offline", device_id="PI-000002", status=DeviceStatus.OFFLINE
            ),
            Device(
                home_id=other.id, name="Foreign", device_id="PI-000003", status=DeviceStatus.ONLINE
            ),
        ]
    )
    await db.commit()
    statements = []

    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(async_engine.sync_engine, "before_cursor_execute", collect)
    try:
        response = await operational_client.get(f"/api/homes/{home.id}/statistics")
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", collect)
    assert response.status_code == 200
    result = response.json()
    assert result["timezone"] == "UTC"
    assert result["today"] == {"events": 1, "conversations": 1, "captures": 1}
    assert result["last_7_days"] == {"events": 3, "conversations": 3, "captures": 3}
    assert result["devices"] == {
        "total": 2,
        "online": 1,
        "offline": 1,
        "disabled": 0,
        "provisioning": 0,
    }
    assert len(result["daily"]) == 7
    assert [day["events"] for day in result["daily"]] == [1, 0, 0, 0, 0, 1, 1]
    activity_queries = [
        sql
        for sql in statements
        if any(f"from {name}" in sql for name in ("events", "conversations", "media"))
    ]
    assert activity_queries
    assert all("count(" in sql for sql in activity_queries)


async def test_empty_statistics_report_zero(operational_client, home):
    response = await operational_client.get(f"/api/homes/{home.id}/statistics")
    assert response.status_code == 200
    assert response.json()["last_7_days"] == {"events": 0, "conversations": 0, "captures": 0}


async def test_statistics_use_the_home_calendar_day(operational_client, db, home):
    home.timezone = "America/Argentina/Buenos_Aires"
    local_now = datetime.now(UTC).astimezone(ZoneInfo(home.timezone))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_midnight_utc = local_midnight.astimezone(UTC)
    db.add_all(
        [
            Event(
                home_id=home.id,
                event_type="local_today",
                created_at=local_midnight_utc,
            ),
            Event(
                home_id=home.id,
                event_type="local_yesterday",
                created_at=local_midnight_utc - timedelta(microseconds=1),
            ),
        ]
    )
    await db.commit()

    response = await operational_client.get(f"/api/homes/{home.id}/statistics")

    assert response.status_code == 200
    assert response.json()["timezone"] == home.timezone
    assert response.json()["today"]["events"] == 1


async def test_statistics_index_positive_offset_first_day_and_today(operational_client, db, home):
    home.timezone = "Asia/Tokyo"
    timezone = ZoneInfo(home.timezone)
    local_now = datetime.now(UTC).astimezone(timezone)
    local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    first_day = local_today - timedelta(days=6)
    db.add_all(
        [
            Event(home_id=home.id, event_type="first_day", created_at=first_day.astimezone(UTC)),
            Event(home_id=home.id, event_type="today", created_at=local_today.astimezone(UTC)),
        ]
    )
    await db.commit()

    response = await operational_client.get(f"/api/homes/{home.id}/statistics")

    assert response.status_code == 200
    assert response.json()["timezone"] == home.timezone
    assert [day["events"] for day in response.json()["daily"]] == [1, 0, 0, 0, 0, 0, 1]
    assert response.json()["today"]["events"] == 1
