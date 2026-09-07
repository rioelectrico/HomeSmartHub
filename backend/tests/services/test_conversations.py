"""Transactional persistence for SIM-1 voice conversations."""

from sqlalchemy import select

from app.models import (
    Conversation,
    ConversationMessage,
    ConversationOutcome,
    ConversationStatus,
    Device,
    MessageRole,
)
from app.services.conversations import (
    append_final_transcript,
    close_conversation,
    create_conversation,
)


async def make_device(db, home) -> Device:
    device = Device(home_id=home.id, device_id="PI-000001", name="Entrada")
    db.add(device)
    await db.commit()
    return device


async def test_create_conversation_is_caller_transactional(db, home) -> None:
    device = await make_device(db, home)

    conversation = await create_conversation(db, home_id=home.id, device_id=device.id)
    assert conversation.home_id == home.id
    assert conversation.device_id == device.id
    assert conversation.status is ConversationStatus.OPEN
    assert conversation.outcome is None

    await db.rollback()
    assert await db.get(Conversation, conversation.id) is None


async def test_final_transcript_is_idempotent(db, home) -> None:
    device = await make_device(db, home)
    conversation = await create_conversation(db, home_id=home.id, device_id=device.id)

    first = await append_final_transcript(
        db,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        text="Soy Juan",
        provider_item_id="item-1",
    )
    second = await append_final_transcript(
        db,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        text="Soy Juan",
        provider_item_id="item-1",
    )

    assert first is not None
    assert second is None
    messages = (await db.scalars(select(ConversationMessage))).all()
    assert messages == [first]


async def test_final_transcript_normalizes_and_limits_safe_content(db, home) -> None:
    device = await make_device(db, home)
    conversation = await create_conversation(db, home_id=home.id, device_id=device.id)

    message = await append_final_transcript(
        db,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        text=f"  {'x' * 8_100}  ",
        provider_item_id="provider-item",
    )

    assert message is not None
    assert message.content == "x" * 8_000
    assert message.metadata_ == {"provider_item_id": "provider-item"}


async def test_final_transcript_rejects_blank_content(db, home) -> None:
    device = await make_device(db, home)
    conversation = await create_conversation(db, home_id=home.id, device_id=device.id)

    message = await append_final_transcript(
        db,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        text=" \r\n\t ",
        provider_item_id="item-empty",
    )

    assert message is None
    assert (await db.scalars(select(ConversationMessage))).all() == []


async def test_close_conversation_sets_terminal_fields_without_committing(db, home) -> None:
    device = await make_device(db, home)
    conversation = await create_conversation(db, home_id=home.id, device_id=device.id)

    closed = await close_conversation(
        db,
        conversation_id=conversation.id,
        outcome=ConversationOutcome.REGISTERED,
    )

    assert closed is conversation
    assert closed.status is ConversationStatus.CLOSED
    assert closed.outcome is ConversationOutcome.REGISTERED
    assert closed.closed_at is not None
    await db.rollback()
    assert await db.get(Conversation, conversation.id) is None
