"""Device credential encryption and lifecycle contracts."""

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.devices.connections import device_connections
from app.devices.credentials import (
    decrypt_secret,
    provision_device,
    rotate_device_secret,
)
from app.models import DeviceCredential, DeviceStatus


async def test_provision_returns_secret_once_and_stores_ciphertext(db, home, settings) -> None:
    """Persisting plaintext or omitting the one-time return breaks provisioning security."""

    result = await provision_device(db, home.id, "PI-000001", "Entrada", settings)
    credential = await db.scalar(
        select(DeviceCredential).where(DeviceCredential.device_id == result.device.id)
    )

    assert credential is not None
    assert result.device.status == DeviceStatus.PROVISIONING
    assert result.secret.encode() not in credential.encrypted_secret
    assert decrypt_secret(credential.encrypted_secret, settings) == result.secret


async def test_rotation_revokes_previous_credential(db, home, settings) -> None:
    """Reusing the old credential after rotation must be impossible."""

    provisioned = await provision_device(db, home.id, "PI-000002", "Cochera", settings)
    rotated = await rotate_device_secret(db, provisioned.device, settings)
    credentials = (
        await db.scalars(
            select(DeviceCredential)
            .where(DeviceCredential.device_id == provisioned.device.id)
            .order_by(DeviceCredential.created_at)
        )
    ).all()

    assert rotated.secret != provisioned.secret
    assert len(credentials) == 2
    assert credentials[0].revoked_at is not None
    assert credentials[1].revoked_at is None
    assert decrypt_secret(credentials[1].encrypted_secret, settings) == rotated.secret


async def test_provision_rejects_invalid_fernet_key(db, home, settings) -> None:
    """Accepting a malformed encryption key would defer a configuration error into runtime."""

    settings.device_credential_encryption_key = SecretStr("not-a-fernet-key")

    with pytest.raises(ValueError, match="DEVICE_CREDENTIAL_ENCRYPTION_KEY"):
        await provision_device(db, home.id, "PI-000003", "Patio", settings)


async def test_rotation_commit_revokes_direct_service_lease_but_rollback_does_not(
    db, home, settings
) -> None:
    """Rotation called without the HTTP route must revoke only after its commit."""
    from unittest.mock import AsyncMock

    provisioned = await provision_device(db, home.id, "PI-000001", "Entrada", settings)
    await db.commit()
    device_id = provisioned.device.id
    lease = await device_connections.claim(device_id, AsyncMock())
    try:
        await rotate_device_secret(db, provisioned.device, settings)
        assert await device_connections.is_current(lease)
        await db.rollback()
        await db.refresh(provisioned.device)
        assert await device_connections.is_current(lease)
        # A later unrelated commit cannot publish the rolled-back revocation.
        await db.commit()
        assert await device_connections.is_current(lease)
        await rotate_device_secret(db, provisioned.device, settings)
        await db.commit()
        assert not await device_connections.is_current(lease)
        await device_connections.wait_revocations()
        assert lease.socket.close.await_args.kwargs == {"code": 4002, "reason": "AUTH_FAILED"}
    finally:
        await device_connections.unregister(lease)


@pytest.mark.parametrize("commit_savepoint", [False, True])
async def test_access_revocation_obeys_savepoint_and_outer_commit(
    db, home, settings, commit_savepoint
):
    """Releasing a savepoint cannot revoke before the real commit; rollback cannot leak intent."""
    from unittest.mock import AsyncMock

    from app.devices.connections import DeviceConnectionRegistry
    from app.devices.credentials import set_device_enabled

    provisioned = await provision_device(db, home.id, "PI-000001", "Entrada", settings)
    await db.commit()
    registry = DeviceConnectionRegistry()
    lease = await registry.claim(provisioned.device.id, AsyncMock())
    nested = await db.begin_nested()
    await set_device_enabled(db, provisioned.device, enabled=False, registry=registry)
    if commit_savepoint:
        await nested.commit()
    else:
        await nested.rollback()
    assert await registry.is_current(lease)
    await db.commit()
    assert await registry.is_current(lease) is not commit_savepoint
    await registry.wait_revocations()
    await registry.unregister(lease)


async def test_failed_commit_does_not_revoke_device_access(db, home, settings):
    """An audit/constraint failure after staging a rotation must preserve the old live identity."""
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import IntegrityError

    from app.devices.connections import DeviceConnectionRegistry
    from app.models import Device

    provisioned = await provision_device(db, home.id, "PI-000001", "Entrada", settings)
    await db.commit()
    registry = DeviceConnectionRegistry()
    lease = await registry.claim(provisioned.device.id, AsyncMock())
    await rotate_device_secret(db, provisioned.device, settings, registry=registry)
    db.add(Device(home_id=home.id, device_id="PI-000001", name="Duplicate violates constraint"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    assert await registry.is_current(lease)
    await db.commit()
    assert await registry.is_current(lease)
    await registry.unregister(lease)
