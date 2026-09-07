"""One-use HMAC challenge authentication contracts."""

import asyncio
import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.devices.challenges import (
    DeviceAuthError,
    canonical_hmac_message,
    create_challenge,
    verify_challenge,
)
from app.devices.credentials import provision_device
from app.models import Device, DeviceAuthChallenge, DeviceStatus


async def test_nonce_is_single_use(db, home, settings) -> None:
    """Accepting the same nonce twice would permit replay authentication."""

    provisioned = await provision_device(db, home.id, "PI-000001", "Entrada", settings)
    challenge = await create_challenge(db, provisioned.device.id, boot_id="boot-a", ttl_seconds=15)
    digest = hmac.new(
        provisioned.secret.encode(),
        b"v1\nPI-000001\nboot-a\n" + challenge.nonce.encode(),
        sha256,
    ).hexdigest()

    assert await verify_challenge(
        db, provisioned.device, "boot-a", challenge.nonce, digest, settings
    )
    with pytest.raises(DeviceAuthError, match="AUTH_FAILED"):
        await verify_challenge(db, provisioned.device, "boot-a", challenge.nonce, digest, settings)


def test_canonical_hmac_message_is_exact_bytes() -> None:
    """Changing field order or separators would break protocol interoperability."""

    assert canonical_hmac_message("PI-000001", "boot-a", "nonce-a") == (
        b"v1\nPI-000001\nboot-a\nnonce-a"
    )


async def test_challenge_rejects_wrong_boot_expiry_bad_digest_and_disabled_device(
    db, home, settings
) -> None:
    """Weakening any challenge binding must not authenticate a device."""

    provisioned = await provision_device(db, home.id, "PI-000002", "Cochera", settings)

    wrong_boot = await create_challenge(db, provisioned.device.id, boot_id="boot-a", ttl_seconds=15)
    digest = hmac.new(
        provisioned.secret.encode(),
        canonical_hmac_message("PI-000002", "boot-b", wrong_boot.nonce),
        sha256,
    ).hexdigest()
    with pytest.raises(DeviceAuthError, match="AUTH_FAILED"):
        await verify_challenge(db, provisioned.device, "boot-b", wrong_boot.nonce, digest, settings)

    expired = await create_challenge(db, provisioned.device.id, boot_id="boot-a", ttl_seconds=15)
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.flush()
    with pytest.raises(DeviceAuthError, match="AUTH_FAILED"):
        await verify_challenge(db, provisioned.device, "boot-a", expired.nonce, "0" * 64, settings)

    bad_digest = await create_challenge(db, provisioned.device.id, boot_id="boot-a", ttl_seconds=15)
    with pytest.raises(DeviceAuthError, match="AUTH_FAILED"):
        await verify_challenge(
            db, provisioned.device, "boot-a", bad_digest.nonce, "0" * 64, settings
        )
    assert bad_digest.consumed_at is not None

    disabled = await create_challenge(db, provisioned.device.id, boot_id="boot-a", ttl_seconds=15)
    provisioned.device.status = DeviceStatus.DISABLED
    await db.flush()
    disabled_digest = hmac.new(
        provisioned.secret.encode(),
        canonical_hmac_message("PI-000002", "boot-a", disabled.nonce),
        sha256,
    ).hexdigest()
    with pytest.raises(DeviceAuthError, match="AUTH_FAILED"):
        await verify_challenge(
            db,
            provisioned.device,
            "boot-a",
            disabled.nonce,
            disabled_digest,
            settings,
        )


async def test_failed_digest_remains_consumed_after_request_rollback(
    db, async_engine, home, settings
) -> None:
    """Rolling back a failed request must not make its attempted nonce reusable."""

    provisioned = await provision_device(db, home.id, "PI-000003", "Patio", settings)
    challenge = await create_challenge(db, provisioned.device.id, boot_id="boot-a", ttl_seconds=15)
    device_pk = provisioned.device.id
    nonce = challenge.nonce
    valid_digest = hmac.new(
        provisioned.secret.encode(),
        canonical_hmac_message("PI-000003", "boot-a", nonce),
        sha256,
    ).hexdigest()
    await db.commit()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async with factory() as failed_request:
        device = await failed_request.get(Device, device_pk)
        assert device is not None
        with pytest.raises(DeviceAuthError, match="AUTH_FAILED"):
            await verify_challenge(failed_request, device, "boot-a", nonce, "0" * 64, settings)
        await failed_request.rollback()

    async with factory() as retry_request:
        device = await retry_request.get(Device, device_pk)
        assert device is not None
        with pytest.raises(DeviceAuthError, match="AUTH_FAILED"):
            await verify_challenge(retry_request, device, "boot-a", nonce, valid_digest, settings)
        persisted = await retry_request.scalar(
            select(DeviceAuthChallenge).where(DeviceAuthChallenge.nonce == nonce)
        )
        assert persisted is not None
        assert persisted.consumed_at is not None


async def test_challenge_creation_requires_boot_binding(db, home, settings) -> None:
    """Allowing an unbound challenge lets the first verifier choose its boot identity."""

    provisioned = await provision_device(db, home.id, "PI-000004", "Taller", settings)

    with pytest.raises(TypeError):
        await create_challenge(db, provisioned.device.id, ttl_seconds=15)  # type: ignore[call-arg]


async def test_expiry_is_checked_after_waiting_for_challenge_lock(
    db, async_engine, home, settings
) -> None:
    """A lock wait crossing expiry must not authenticate with a stale time snapshot."""

    provisioned = await provision_device(db, home.id, "PI-000005", "Porton", settings)
    challenge = await create_challenge(db, provisioned.device.id, boot_id="boot-a", ttl_seconds=1)
    device_pk = provisioned.device.id
    nonce = challenge.nonce
    digest = hmac.new(
        provisioned.secret.encode(),
        canonical_hmac_message("PI-000005", "boot-a", nonce),
        sha256,
    ).hexdigest()
    await db.commit()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def authenticate_after_lock() -> bool:
        async with factory() as verifier:
            device = await verifier.get(Device, device_pk)
            assert device is not None
            return await verify_challenge(verifier, device, "boot-a", nonce, digest, settings)

    async with factory() as blocker, blocker.begin():
        locked = await blocker.scalar(
            select(DeviceAuthChallenge).where(DeviceAuthChallenge.nonce == nonce).with_for_update()
        )
        assert locked is not None
        verification = asyncio.create_task(authenticate_after_lock())
        await asyncio.sleep(1.1)

    with pytest.raises(DeviceAuthError, match="AUTH_FAILED"):
        await verification
