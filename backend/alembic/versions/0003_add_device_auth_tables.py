"""Add versioned device credentials and one-use authentication challenges.

Revision ID: 0003_add_device_auth_tables
Revises: 0002_add_agent_config_fields
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_add_device_auth_tables"
down_revision = "0002_add_agent_config_fields"
branch_labels = None
depends_on = None


def uuid_pk() -> sa.Column[sa.UUID]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "device_credentials",
        uuid_pk(),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_device_credentials_active_device",
        "device_credentials",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "device_auth_challenges",
        uuid_pk(),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("boot_id", sa.String(128)),
        sa.Column("nonce", sa.String(128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_device_auth_challenges_expires_at",
        "device_auth_challenges",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("device_auth_challenges")
    op.drop_table("device_credentials")
