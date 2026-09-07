"""Safe home diagnostics and non-networked optional AI configuration status."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.models import Device, DeviceStatus, Event, Home, Permission, RolePermission


@pytest.mark.parametrize(
    "key,expected",
    [
        (None, "unconfigured"),
        ("", "unconfigured"),
        ("  ", "unconfigured"),
        ("sk-private-do-not-expose", "configured"),
    ],
)
async def test_ai_diagnostics_only_report_configuration(
    operational_client, application, settings, home, key, expected
):
    from app.config import Settings

    configured = Settings(
        **{
            **settings.model_dump(exclude={"openai_api_key", "openai_realtime_model"}),
            "OPENAI_API_KEY": key,
            "OPENAI_REALTIME_MODEL": "test-realtime-model",
        }
    )
    # The provider is injected at the application boundary; no OpenAI request is needed.
    import importlib.util

    if importlib.util.find_spec("app.ai") is not None:
        from app.ai.provider import ConfiguredOpenAIRealtimeProvider

        application.state.ai_provider = ConfiguredOpenAIRealtimeProvider(configured)
    response = await operational_client.get(f"/api/homes/{home.id}/diagnostics")
    assert response.status_code == 200
    assert response.json()["services"] == {"backend": "up", "postgresql": "up", "openai": expected}
    assert "sk-private" not in response.text
    assert "test-realtime-model" not in response.text
    assert (await operational_client.get("/ready")).status_code == 200


async def test_diagnostics_latest_snapshot_is_home_scoped_and_paginated(
    operational_client, db, home
):
    other = Home(name="Other")
    db.add(other)
    await db.flush()
    now = datetime.now(UTC)
    target = Device(
        home_id=home.id,
        device_id="PI-000001",
        name="Entry",
        status=DeviceStatus.ONLINE,
        last_seen_at=now,
        credential_encrypted="encrypted-secret",
    )
    second = Device(
        home_id=home.id, device_id="PI-000002", name="Back", created_at=now - timedelta(days=1)
    )
    foreign = Device(home_id=other.id, device_id="PI-000003", name="Foreign")
    db.add_all([target, second, foreign])
    await db.flush()
    db.add_all(
        [
            Event(
                home_id=home.id,
                device_id=target.id,
                event_type="device_status",
                created_at=now - timedelta(seconds=1),
                payload={"camera": "error"},
            ),
            Event(
                home_id=home.id,
                device_id=target.id,
                event_type="device_status",
                created_at=now,
                payload={
                    "camera": "ready",
                    "microphone": "unavailable",
                    "firmware_version": "1.0",
                    "hardware_model": "sim",
                    "api_key": "secret-key",
                },
            ),
            Event(
                home_id=home.id,
                device_id=target.id,
                event_type="device_online",
                created_at=now + timedelta(seconds=1),
                payload={},
            ),
            Event(
                home_id=other.id,
                device_id=foreign.id,
                event_type="device_status",
                payload={"camera": "error"},
            ),
        ]
    )
    await db.commit()
    url = f"/api/homes/{home.id}/diagnostics"
    first = await operational_client.get(url, params={"limit": 1})
    assert first.status_code == 200
    item = first.json()["devices"]["items"][0]
    assert item["id"] == str(target.id)
    assert item["status"] == "online"
    assert item["connected"] is False
    assert item["snapshot"]["camera"] == "ready"
    assert item["snapshot"]["microphone"] == "unavailable"
    assert (
        "secret" not in first.text
        and "credential" not in first.text
        and "Foreign" not in first.text
    )
    second_page = await operational_client.get(
        url, params={"limit": 1, "cursor": first.json()["devices"]["next_cursor"]}
    )
    assert [item["id"] for item in second_page.json()["devices"]["items"]] == [str(second.id)]
    assert second_page.json()["devices"]["items"][0]["snapshot"] is None
    assert second_page.json()["devices"]["next_cursor"] is None
    assert (await operational_client.get(url, params={"limit": 101})).status_code == 422


async def test_diagnostics_need_device_read_permission(operational_client, db, home):
    await db.execute(
        delete(RolePermission).where(
            RolePermission.permission_id.in_(
                select(Permission.id).where(Permission.name == "devices.read")
            )
        )
    )
    await db.commit()
    assert (await operational_client.get(f"/api/homes/{home.id}/diagnostics")).status_code == 403
