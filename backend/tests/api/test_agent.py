"""Home-scoped AI agent configuration contracts."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bootstrap import bootstrap_database
from app.config import Settings
from app.models import AgentConfig, AuditLog, Home, HomeUser, Permission, RolePermission
from app.schemas.agent import AgentConfigRequest
from app.services.agent import save_agent_config


async def grant_agent_administration(db, admin, home, roles) -> None:
    """Grant the exact read and write permissions used by the agent resource."""

    permissions = [Permission(name="agent.view"), Permission(name="agent.edit")]
    db.add_all(
        [
            *permissions,
            HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id),
        ]
    )
    await db.flush()
    db.add_all(
        [
            RolePermission(role_id=roles["owner"].id, permission_id=permission.id)
            for permission in permissions
        ]
    )
    await db.commit()


async def login_as_admin(client, admin) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"identifier": admin.username, "password": "ValidPass!42"},
    )
    assert response.status_code == 204


async def test_fresh_bootstrap_administrator_can_view_and_edit_agent(client, db, settings) -> None:
    """A bootstrap that omits exact agent grants must fail at the real API boundary."""

    configured = Settings(
        DATABASE_URL=str(settings.database_url),
        APP_SECRET_KEY=settings.app_secret_key.get_secret_value(),
        DEVICE_CREDENTIAL_ENCRYPTION_KEY=(
            settings.device_credential_encryption_key.get_secret_value()
        ),
        MEDIA_ROOT=settings.media_root,
        BOOTSTRAP_ADMIN_USERNAME="bootstrap-admin",
        BOOTSTRAP_ADMIN_EMAIL="bootstrap@example.test",
        BOOTSTRAP_ADMIN_PASSWORD="ValidPass!42",
        BOOTSTRAP_HOME_NAME="Bootstrap home",
    )
    await bootstrap_database(db, configured)
    home = await db.scalar(select(Home))
    assert home is not None
    login = await client.post(
        "/api/auth/login",
        json={"identifier": "bootstrap-admin", "password": "ValidPass!42"},
    )
    assert login.status_code == 204
    payload = {
        "name": "Bootstrap agent",
        "system_prompt": "Request identification.",
        "language": "en-US",
        "voice": "configured",
        "voice_speed": 1.0,
        "realtime_model": "configured",
        "enabled": True,
    }

    updated = await client.put(f"/api/homes/{home.id}/agent", json=payload)
    restored = await client.get(f"/api/homes/{home.id}/agent")

    assert updated.status_code == 200
    assert restored.status_code == 200
    assert restored.json()["name"] == "Bootstrap agent"


async def test_agent_update_persists_full_config_and_sanitizes_audit(
    client, admin, db, home, roles
) -> None:
    """Dropping an agent field or recording its prompt must be caught at the HTTP boundary."""

    await grant_agent_administration(db, admin, home, roles)
    await login_as_admin(client, admin)
    payload = {
        "name": "Amalia",
        "system_prompt": "Saludá y pedí identificación.",
        "language": "es-AR",
        "voice": "marin",
        "voice_speed": 1.1,
        "realtime_model": "env-default",
        "enabled": True,
    }

    saved = await client.put(f"/api/homes/{home.id}/agent", json=payload)
    restored = await client.get(f"/api/homes/{home.id}/agent")

    assert saved.status_code == 200
    assert saved.json()["updated_at"]
    assert restored.status_code == 200
    for field, value in payload.items():
        assert restored.json()[field] == value
    config = await db.scalar(select(AgentConfig).where(AgentConfig.home_id == home.id))
    assert config is not None
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "agent.updated"))
    assert audit is not None
    assert payload["system_prompt"] not in (audit.detail or "")
    assert payload["system_prompt"] not in str(audit.context)
    assert "system_prompt" not in audit.context


async def test_agent_update_is_last_write_wins_and_returns_new_timestamp(
    client, admin, db, home, roles
) -> None:
    """Adding a version precondition or retaining stale values must break the MVP1 contract."""

    await grant_agent_administration(db, admin, home, roles)
    await login_as_admin(client, admin)
    initial_payload = {
        "name": "Amalia",
        "system_prompt": "Pedí identificación.",
        "language": "es-AR",
        "voice": "marin",
        "voice_speed": 1.0,
        "realtime_model": "env-default",
        "enabled": True,
    }
    replacement_payload = {
        **initial_payload,
        "name": "Ada",
        "system_prompt": "Recibí visitantes.",
        "voice_speed": 1.2,
    }

    first = await client.put(f"/api/homes/{home.id}/agent", json=initial_payload)
    second = await client.put(f"/api/homes/{home.id}/agent", json=replacement_payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["updated_at"] > first.json()["updated_at"]
    restored = await client.get(f"/api/homes/{home.id}/agent")
    assert restored.status_code == 200
    for field, value in replacement_payload.items():
        assert restored.json()[field] == value


async def test_agent_get_supplies_prompt_for_legacy_null_config(
    client, admin, db, home, roles
) -> None:
    """Rows valid under the initial schema must remain readable after the API is introduced."""

    await grant_agent_administration(db, admin, home, roles)
    db.add(AgentConfig(home_id=home.id, system_prompt=None))
    await db.commit()
    await login_as_admin(client, admin)

    response = await client.get(f"/api/homes/{home.id}/agent")

    assert response.status_code == 200
    assert response.json()["system_prompt"]


async def test_concurrent_initial_agent_updates_use_one_last_write_wins_row(
    async_engine, admin, home
) -> None:
    """Replacing the atomic upsert with select-then-insert must expose a unique-key race."""

    payloads = [
        AgentConfigRequest(
            name="First",
            system_prompt="First prompt",
            language="es-AR",
            voice="first-voice",
            voice_speed=0.9,
            realtime_model="first-model",
            enabled=False,
        ),
        AgentConfigRequest(
            name="Second",
            system_prompt="Second prompt",
            language="en-US",
            voice="second-voice",
            voice_speed=1.1,
            realtime_model="second-model",
            enabled=True,
        ),
    ]
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def persist(payload: AgentConfigRequest) -> None:
        async with factory() as session:
            await save_agent_config(
                session,
                actor_id=admin.id,
                home_id=home.id,
                payload=payload,
            )
            await session.commit()

    results = await asyncio.gather(
        *(persist(payload) for payload in payloads), return_exceptions=True
    )

    assert not [result for result in results if isinstance(result, BaseException)], results
    async with factory() as verification_session:
        configs = (
            await verification_session.scalars(
                select(AgentConfig).where(AgentConfig.home_id == home.id)
            )
        ).all()
        audits = (
            await verification_session.scalars(
                select(AuditLog).where(
                    AuditLog.home_id == home.id,
                    AuditLog.action == "agent.updated",
                )
            )
        ).all()

    assert len(configs) == 1
    persisted = configs[0]
    persisted_values = {
        field: getattr(persisted, field)
        for field in (
            "name",
            "system_prompt",
            "language",
            "voice",
            "voice_speed",
            "realtime_model",
            "enabled",
        )
    }
    assert persisted_values in [payload.model_dump() for payload in payloads]
    assert len(audits) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("system_prompt", ""),
        ("system_prompt", "p" * 8001),
        ("voice", ""),
        ("voice", "v" * 129),
        ("voice_speed", 0.49),
        ("voice_speed", 2.01),
        ("name", "n" * 129),
        ("language", "l" * 33),
        ("realtime_model", "m" * 129),
    ],
    ids=[
        "empty-prompt",
        "long-prompt",
        "empty-voice",
        "long-voice",
        "slow-voice",
        "fast-voice",
        "long-name",
        "long-language",
        "long-model",
    ],
)
async def test_agent_rejects_invalid_prompt_voice_and_speed(
    client, admin, db, home, roles, field, value
) -> None:
    """Removing payload bounds must reject invalid configuration before it reaches persistence."""

    await grant_agent_administration(db, admin, home, roles)
    await login_as_admin(client, admin)
    payload = {
        "name": "Amalia",
        "system_prompt": "Saludá y pedí identificación.",
        "language": "es-AR",
        "voice": "marin",
        "voice_speed": 1.0,
        "realtime_model": "env-default",
        "enabled": True,
    }
    payload[field] = value

    response = await client.put(f"/api/homes/{home.id}/agent", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await db.scalar(select(AgentConfig).where(AgentConfig.home_id == home.id)) is None
