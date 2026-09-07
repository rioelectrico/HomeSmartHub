"""Events, conversations, and stored media."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.devices import enum_values


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ConversationOutcome(StrEnum):
    REGISTERED = "registered"
    DECLINED = "declined"
    ABANDONED = "abandoned"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MediaType(StrEnum):
    IMAGE = "image"


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_home_id_created_at", "home_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    home_id: Mapped[UUID] = mapped_column(
        ForeignKey("homes.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "uq_conversations_active_device",
            "device_id",
            unique=True,
            postgresql_where="status = 'open' AND device_id IS NOT NULL",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    home_id: Mapped[UUID] = mapped_column(
        ForeignKey("homes.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status", values_callable=enum_values),
        nullable=False,
        default=ConversationStatus.OPEN,
    )
    outcome: Mapped[ConversationOutcome | None] = mapped_column(
        Enum(ConversationOutcome, name="conversation_outcome", values_callable=enum_values)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", values_callable=enum_values), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Media(Base):
    __tablename__ = "media"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    home_id: Mapped[UUID] = mapped_column(
        ForeignKey("homes.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    command_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("device_commands.id", ondelete="SET NULL")
    )
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    media_type: Mapped[MediaType] = mapped_column(
        Enum(MediaType, name="media_type", values_callable=enum_values), nullable=False
    )
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    path: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
