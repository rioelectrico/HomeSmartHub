"""Persist the complete home agent configuration.

Revision ID: 0002_add_agent_config_fields
Revises: 0001_initial_schema
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_add_agent_config_fields"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_configs",
        sa.Column("name", sa.String(128), nullable=False, server_default="Portero"),
    )
    op.add_column(
        "agent_configs",
        sa.Column("language", sa.String(32), nullable=False, server_default="es-AR"),
    )
    op.add_column(
        "agent_configs",
        sa.Column("voice", sa.String(128), nullable=False, server_default="default"),
    )
    op.add_column(
        "agent_configs",
        sa.Column("realtime_model", sa.String(128), nullable=False, server_default="env-default"),
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "realtime_model")
    op.drop_column("agent_configs", "voice")
    op.drop_column("agent_configs", "language")
    op.drop_column("agent_configs", "name")
