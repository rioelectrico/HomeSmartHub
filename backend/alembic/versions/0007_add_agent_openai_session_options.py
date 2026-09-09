"""Persist advanced OpenAI Realtime session overrides for each home agent.

Revision ID: 0007_agent_options
Revises: 0006_sim1_conversations
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0007_agent_options"
down_revision = "0006_sim1_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_configs",
        sa.Column(
            "openai_session_options",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "openai_session_options")
