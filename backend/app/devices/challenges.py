"""Atomic single-use HMAC challenge authentication."""

import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.devices.credentials import decrypt_secret
from app.models import Device, DeviceAuthChallenge, DeviceCredential, DeviceStatus


class DeviceAuthError(RuntimeError):
    """A deliberately opaque device-authentication failure."""

    def __init__(self) -> None:
        super().__init__("AUTH_FAILED")


def canonical_hmac_message(device_id: str, boot_id: str, nonce: str) -> bytes:
    """Build the byte-exact protocol V1 authentication message."""

    return f"v1\n{device_id}\n{boot_id}\n{nonce}".encode()


async def create_challenge(
    db: AsyncSession,
    device_id: UUID,
    boot_id: str,
    *,
    ttl_seconds: int = 15,
) -> DeviceAuthChallenge:
    """Stage a cryptographically random challenge bound to one device boot."""

    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    challenge = DeviceAuthChallenge(
        device_id=device_id,
        boot_id=boot_id,
        nonce=token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    db.add(challenge)
    await db.flush()
    return challenge


async def verify_challenge(
    db: AsyncSession,
    device: Device,
    boot_id: str,
    nonce: str,
    digest: str,
    settings: Settings | None = None,
) -> bool:
    """Atomically verify and durably consume a challenge.

    Once an eligible nonce reaches digest verification this function owns and
    commits the authentication transaction before returning or raising. A
    surrounding request rollback therefore cannot make a failed nonce reusable.
    """

    stored_device = await db.scalar(select(Device).where(Device.id == device.id).with_for_update())
    if stored_device is None or stored_device.status == DeviceStatus.DISABLED:
        raise DeviceAuthError

    challenge = await db.scalar(
        select(DeviceAuthChallenge).where(DeviceAuthChallenge.nonce == nonce).with_for_update()
    )
    if (
        challenge is None
        or challenge.device_id != stored_device.id
        or challenge.consumed_at is not None
        or challenge.boot_id != boot_id
    ):
        raise DeviceAuthError

    credential = await db.scalar(
        select(DeviceCredential)
        .where(
            DeviceCredential.device_id == stored_device.id,
            DeviceCredential.revoked_at.is_(None),
        )
        .with_for_update()
    )
    # Capture time only after every potentially blocking authentication lock.
    now = datetime.now(UTC)
    if challenge.expires_at <= now:
        raise DeviceAuthError
    challenge.consumed_at = now
    if credential is None:
        await db.flush()
        await db.commit()
        raise DeviceAuthError

    active_settings = settings if settings is not None else get_settings()
    secret = decrypt_secret(credential.encrypted_secret, active_settings)
    expected = hmac.new(
        secret.encode("utf-8"),
        canonical_hmac_message(stored_device.device_id, boot_id, nonce),
        sha256,
    ).hexdigest()
    verified = hmac.compare_digest(expected, digest)
    await db.flush()
    await db.commit()
    if not verified:
        raise DeviceAuthError
    return True
