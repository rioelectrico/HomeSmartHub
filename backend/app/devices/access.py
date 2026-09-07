"""Publish device-access revocations only at the durable transaction boundary."""

from typing import cast
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.devices.connections import DeviceConnectionRegistry

_KEY = "device_access_revocations"
Pending = dict[SessionTransaction, set[tuple[DeviceConnectionRegistry, UUID]]]


def stage_access_revocation(
    db: AsyncSession, device_id: UUID, registry: DeviceConnectionRegistry
) -> None:
    """Stage alongside the credential/status mutation; never retire on flush alone.

    Works for service callers as well as HTTP routes. Savepoint commits merge into
    the parent, and rollback discards only that transaction's staged changes.
    """
    session = db.sync_session
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        raise RuntimeError("device access mutation requires a transaction")
    pending = cast(Pending, session.info.setdefault(_KEY, {}))
    pending.setdefault(transaction, set()).add((registry, device_id))


@event.listens_for(Session, "after_commit")
def _committed(session: Session) -> None:
    pending = cast(Pending, session.info.get(_KEY, {}))
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        return
    changes = pending.pop(transaction, set())
    if transaction.parent is not None:
        pending.setdefault(transaction.parent, set()).update(changes)
    else:
        for registry, device_id in changes:
            registry.revoke_access(device_id)


@event.listens_for(Session, "after_transaction_end")
def _transaction_ended(session: Session, transaction: SessionTransaction) -> None:
    pending = cast(Pending, session.info.get(_KEY, {}))
    pending.pop(transaction, None)
    if not pending:
        session.info.pop(_KEY, None)
