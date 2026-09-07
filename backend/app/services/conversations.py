"""Transactional persistence operations for SIM-1 voice conversations."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Conversation,
    ConversationMessage,
    ConversationOutcome,
    ConversationStatus,
    MessageRole,
)

MAX_TRANSCRIPT_LENGTH = 8_000


async def create_conversation(
    db: AsyncSession,
    *,
    home_id: UUID,
    device_id: UUID,
) -> Conversation:
    """Stage one open conversation in the caller's transaction."""

    conversation = Conversation(home_id=home_id, device_id=device_id)
    db.add(conversation)
    await db.flush()
    return conversation


async def append_final_transcript(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    role: MessageRole,
    text: str,
    provider_item_id: str,
) -> ConversationMessage | None:
    """Stage one normalized final transcript unless its provider item already exists."""

    content = text.strip()[:MAX_TRANSCRIPT_LENGTH]
    if not content:
        return None
    existing_id = await db.scalar(
        select(ConversationMessage.id).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.metadata_["provider_item_id"].astext == provider_item_id,
        )
    )
    if existing_id is not None:
        return None
    message = ConversationMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        metadata_={"provider_item_id": provider_item_id},
    )
    db.add(message)
    await db.flush()
    return message


async def close_conversation(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    outcome: ConversationOutcome,
) -> Conversation:
    """Lock and stage the terminal state in the caller's transaction."""

    conversation = (
        await db.scalars(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        )
    ).one()
    conversation.status = ConversationStatus.CLOSED
    conversation.outcome = outcome
    conversation.closed_at = datetime.now(UTC)
    await db.flush()
    return conversation
