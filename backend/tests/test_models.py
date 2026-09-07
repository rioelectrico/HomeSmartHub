"""PostgreSQL constraints for the persistence model."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Conversation, ConversationStatus, Device, HomeUser


async def test_same_user_can_have_different_roles_per_home(db, user, two_homes, roles):
    db.add_all(
        [
            HomeUser(home_id=two_homes[0].id, user_id=user.id, role_id=roles["owner"].id),
            HomeUser(
                home_id=two_homes[1].id,
                user_id=user.id,
                role_id=roles["read_only"].id,
            ),
        ]
    )
    await db.commit()

    links = (await db.scalars(select(HomeUser).where(HomeUser.user_id == user.id))).all()
    assert {link.role_id for link in links} == {roles["owner"].id, roles["read_only"].id}


async def test_device_id_is_unique(db, home):
    db.add_all(
        [
            Device(home_id=home.id, device_id="PI-000001", name="Entrada"),
            Device(home_id=home.id, device_id="PI-000001", name="Cochera"),
        ]
    )
    with pytest.raises(IntegrityError):
        await db.commit()


async def test_only_one_open_conversation_is_allowed_per_device(db, home):
    device = Device(home_id=home.id, device_id="PI-000001", name="Entrada")
    db.add(device)
    await db.flush()
    db.add_all(
        [
            Conversation(home_id=home.id, device_id=device.id),
            Conversation(home_id=home.id, device_id=device.id),
        ]
    )

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_open_historical_conversations_without_device_remain_allowed(db, home):
    db.add_all(
        [
            Conversation(home_id=home.id, device_id=None),
            Conversation(home_id=home.id, device_id=None),
        ]
    )

    await db.commit()

    conversations = (await db.scalars(select(Conversation))).all()
    assert len(conversations) == 2
    assert all(conversation.status is ConversationStatus.OPEN for conversation in conversations)


async def test_deleting_device_preserves_conversation_history(db, home):
    device = Device(home_id=home.id, device_id="PI-000001", name="Entrada")
    db.add(device)
    await db.flush()
    conversation = Conversation(
        home_id=home.id,
        device_id=device.id,
        status=ConversationStatus.CLOSED,
    )
    db.add(conversation)
    await db.commit()

    await db.delete(device)
    await db.commit()
    await db.refresh(conversation)

    assert conversation.device_id is None
