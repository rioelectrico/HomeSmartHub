"""Audit queries expose only authorized home records and bounded safe context."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete

from app.models import AuditLog, Home, Permission, RolePermission


async def test_audit_filters_and_stable_cursor(operational_client, db, home):
    when = datetime(2026, 9, 1, tzinfo=UTC)
    other = Home(name="Other")
    db.add(other)
    await db.flush()
    db.add_all(
        [
            AuditLog(
                id=UUID(int=i),
                home_id=home.id,
                action="device.disabled",
                created_at=when,
                context={
                    "device_id": "PI-000001",
                    "api_key": "do-not-expose",
                    "nested": {"token": "hidden"},
                },
            )
            for i in (1, 2, 3)
        ]
    )
    db.add_all(
        [
            AuditLog(home_id=other.id, action="device.disabled"),
            AuditLog(home_id=None, action="auth.login_failed"),
            AuditLog(home_id=home.id, action="device.created", created_at=when),
        ]
    )
    await db.commit()
    url = f"/api/homes/{home.id}/audit"
    params = {
        "action": "device.disabled",
        "date_from": "2026-09-01T00:00:00Z",
        "date_to": "2026-09-02T00:00:00Z",
        "limit": 2,
    }
    first = await operational_client.get(url, params=params)
    assert first.status_code == 200
    assert [item["id"] for item in first.json()["items"]] == [str(UUID(int=3)), str(UUID(int=2))]
    assert first.json()["items"][0]["context"] == {"device_id": "PI-000001"}
    second = await operational_client.get(
        url, params={**params, "cursor": first.json()["next_cursor"]}
    )
    assert [item["id"] for item in second.json()["items"]] == [str(UUID(int=1))]
    assert second.json()["next_cursor"] is None
    assert "do-not-expose" not in first.text


async def test_event_reader_cannot_read_administrator_audit(operational_client, db, home):
    await db.execute(
        delete(RolePermission).where(
            RolePermission.permission_id.in_(
                __import__("sqlalchemy")
                .select(Permission.id)
                .where(Permission.name == "audit.read")
            )
        )
    )
    await db.commit()
    assert (await operational_client.get(f"/api/homes/{home.id}/events")).status_code == 200
    assert (await operational_client.get(f"/api/homes/{home.id}/audit")).status_code == 403


async def test_audit_pagination_is_bounded(operational_client, home):
    assert (
        await operational_client.get(f"/api/homes/{home.id}/audit?limit=101")
    ).status_code == 422
