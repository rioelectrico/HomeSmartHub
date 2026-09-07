"""Add SIM-1 conversation persistence fields and active-device exclusion.

Revision ID: 0006_sim1_conversations
Revises: 0005_command_lifecycle
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_sim1_conversations"
down_revision = "0005_command_lifecycle"
branch_labels = None
depends_on = None

conversation_outcome = postgresql.ENUM(
    "registered",
    "declined",
    "abandoned",
    "failed",
    name="conversation_outcome",
    create_type=False,
)


def upgrade() -> None:
    conversation_outcome.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "conversations",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("outcome", conversation_outcome, nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_device_id_devices",
        "conversations",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_conversations_active_device",
        "conversations",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND device_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_conversations_active_device", table_name="conversations")
    op.drop_constraint(
        "fk_conversations_device_id_devices",
        "conversations",
        type_="foreignkey",
    )
    op.drop_column("conversations", "outcome")
    op.drop_column("conversations", "device_id")
    conversation_outcome.drop(op.get_bind(), checkfirst=True)
