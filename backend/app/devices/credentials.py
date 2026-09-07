"""Authenticated encryption and rotation of device credentials."""

from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.devices.access import stage_access_revocation
from app.devices.connections import DeviceConnectionRegistry, device_connections
from app.models import Device, DeviceCredential, DeviceStatus


@dataclass(frozen=True, slots=True)
class ProvisionedDevice:
    """A device paired with its one-time plaintext provisioning secret."""

    device: Device
    secret: str


def _fernet(settings: Settings) -> Fernet:
    key = settings.device_credential_encryption_key.get_secret_value().encode("ascii")
    try:
        return Fernet(key)
    except (ValueError, TypeError) as error:
        raise ValueError("DEVICE_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from error


def encrypt_secret(secret: str, settings: Settings) -> bytes:
    """Encrypt a plaintext secret with authenticated Fernet encryption."""

    return _fernet(settings).encrypt(secret.encode("utf-8"))


def decrypt_secret(ciphertext: bytes, settings: Settings) -> str:
    """Decrypt a credential only at the authentication boundary."""

    return _fernet(settings).decrypt(ciphertext).decode("utf-8")


async def provision_device(
    db: AsyncSession,
    home_id: UUID,
    device_id: str,
    name: str,
    settings: Settings,
) -> ProvisionedDevice:
    """Create and provision a device, returning its secret exactly once."""

    secret = token_urlsafe(32)
    encrypted_secret = encrypt_secret(secret, settings)
    device = Device(
        home_id=home_id,
        device_id=device_id,
        name=name,
        status=DeviceStatus.PROVISIONING,
    )
    db.add(device)
    await db.flush()
    db.add(DeviceCredential(device_id=device.id, encrypted_secret=encrypted_secret))
    await db.flush()
    return ProvisionedDevice(device=device, secret=secret)


async def rotate_device_secret(
    db: AsyncSession,
    device: Device,
    settings: Settings,
    *,
    registry: DeviceConnectionRegistry = device_connections,
) -> ProvisionedDevice:
    """Revoke the current secret and return a newly generated secret once."""

    # Serialize with authentication and presence in the same device->credential
    # lock order; stale ORM snapshots must not overwrite a concurrent mutation.
    stored = await db.scalar(
        select(Device)
        .where(Device.id == device.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if stored is None:
        raise LookupError("device no longer exists")
    credentials = (
        await db.scalars(
            select(DeviceCredential)
            .where(
                DeviceCredential.device_id == device.id,
                DeviceCredential.revoked_at.is_(None),
            )
            .with_for_update()
        )
    ).all()
    now = datetime.now(UTC)
    for credential in credentials:
        credential.revoked_at = now
    secret = token_urlsafe(32)
    db.add(
        DeviceCredential(
            device_id=device.id,
            encrypted_secret=encrypt_secret(secret, settings),
        )
    )
    device.status = DeviceStatus.PROVISIONING
    await db.flush()
    stage_access_revocation(db, device.id, registry)
    return ProvisionedDevice(device=device, secret=secret)


async def set_device_enabled(
    db: AsyncSession,
    device: Device,
    *,
    enabled: bool,
    registry: DeviceConnectionRegistry = device_connections,
) -> None:
    """Stage enablement and, when disabling, post-commit lease revocation."""
    stored = await db.scalar(
        select(Device)
        .where(Device.id == device.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if stored is None:
        raise LookupError("device no longer exists")
    stored.status = DeviceStatus.OFFLINE if enabled else DeviceStatus.DISABLED
    await db.flush()
    if not enabled:
        stage_access_revocation(db, stored.id, registry)
