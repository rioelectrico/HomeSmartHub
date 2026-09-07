"""Deterministic event keyset pagination, filtering, and permission isolation."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.models import Device, Event, Home


async def test_events_cursor_handles_ties_and_new_inserts(operational_client, db, home):
    now = datetime(2026, 9, 1, tzinfo=UTC)
    db.add_all(
        [
            Event(id=UUID(int=i), home_id=home.id, event_type="device_online", created_at=now)
            for i in (1, 2, 3)
        ]
    )
    await db.commit()
    url = f"/api/homes/{home.id}/events"
    first = await operational_client.get(url, params={"limit": 2})
    assert first.status_code == 200
    assert [item["id"] for item in first.json()["items"]] == [str(UUID(int=3)), str(UUID(int=2))]
    db.add(Event(home_id=home.id, event_type="device_online", created_at=now + timedelta(days=1)))
    await db.commit()
    second = await operational_client.get(
        url, params={"limit": 2, "cursor": first.json()["next_cursor"]}
    )
    assert [item["id"] for item in second.json()["items"]] == [str(UUID(int=1))]
    assert second.json()["next_cursor"] is None


async def test_events_filter_and_detail_are_scoped_and_sanitized(operational_client, db, home):
    device = Device(home_id=home.id, device_id="PI-000001", name="Entry")
    other = Home(name="Other")
    db.add_all([device, other])
    await db.flush()
    when = datetime(2026, 9, 1, tzinfo=UTC)
    target = Event(
        home_id=home.id,
        device_id=device.id,
        event_type="device_status",
        created_at=when,
        payload={"camera": "ready", "token": "secret-token", "nested": {"api_key": "secret-key"}},
    )
    foreign = Event(home_id=other.id, event_type="device_status", created_at=when)
    db.add_all(
        [
            target,
            foreign,
            Event(home_id=home.id, event_type="device_offline", created_at=when),
            Event(
                home_id=home.id,
                device_id=device.id,
                event_type="device_status",
                created_at=when - timedelta(days=2),
            ),
        ]
    )
    await db.commit()
    url = f"/api/homes/{home.id}/events"
    response = await operational_client.get(
        url,
        params={
            "device_id": str(device.id),
            "event_type": "device_status",
            "date_from": "2026-09-01T00:00:00Z",
            "date_to": "2026-09-02T00:00:00Z",
        },
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(target.id)]
    detail = await operational_client.get(f"{url}/{target.id}")
    assert detail.status_code == 200
    assert detail.json()["payload"] == {"camera": "ready"}
    assert "secret" not in response.text + detail.text
    assert (await operational_client.get(f"{url}/{foreign.id}")).status_code == 404
    assert (await operational_client.get(f"/api/homes/{other.id}/events")).status_code == 403


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"cursor": "bad"},
        {"device_id": "bad"},
        {"date_from": "2026-09-01"},
        {"date_from": "2026-09-02T00:00:00Z", "date_to": "2026-09-01T00:00:00Z"},
    ],
)
async def test_event_query_rejects_invalid_bounds(operational_client, home, params):
    response = await operational_client.get(f"/api/homes/{home.id}/events", params=params)
    assert response.status_code == 422


@pytest.mark.parametrize("resource", ["events", "audit", "statistics", "diagnostics"])
async def test_operational_resources_require_user_home_and_permission(
    app_client, operational_client, home, resource
):
    url = f"/api/homes/{uuid4()}/{resource}"
    assert (await operational_client.get(url)).status_code == 403
    operational_client.cookies.clear()
    assert (await app_client.get(f"/api/homes/{home.id}/{resource}")).status_code == 401
