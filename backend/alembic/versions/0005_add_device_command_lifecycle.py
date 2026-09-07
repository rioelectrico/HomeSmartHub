"""Add durable device command lifecycle fields.

Revision ID: 0005_command_lifecycle
Revises: 0004_require_challenge_boot_id
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_command_lifecycle"
down_revision = "0004_require_challenge_boot_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device_commands", sa.Column("sent_at", sa.DateTime(timezone=True)))
    op.add_column("device_commands", sa.Column("failed_at", sa.DateTime(timezone=True)))
    op.add_column("device_commands", sa.Column("timeout_at", sa.DateTime(timezone=True)))
    op.execute("ALTER TABLE device_commands ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE command_status RENAME TO command_status_legacy")
    op.execute(
        "CREATE TYPE command_status AS ENUM "
        "('pending', 'sent', 'acknowledged', 'completed', 'failed', 'timeout')"
    )
    op.execute(
        "ALTER TABLE device_commands ALTER COLUMN status TYPE command_status "
        "USING (CASE WHEN status::text = 'expired' THEN 'timeout' "
        "ELSE status::text END)::command_status"
    )
    op.execute("DROP TYPE command_status_legacy")
    op.execute("ALTER TABLE device_commands ALTER COLUMN status SET DEFAULT 'pending'")


def downgrade() -> None:
    op.execute("ALTER TABLE device_commands ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE command_status RENAME TO command_status_current")
    op.execute(
        "CREATE TYPE command_status AS ENUM "
        "('pending', 'sent', 'acknowledged', 'completed', 'failed', 'expired')"
    )
    op.execute(
        "ALTER TABLE device_commands ALTER COLUMN status TYPE command_status "
        "USING (CASE WHEN status::text = 'timeout' THEN 'expired' "
        "ELSE status::text END)::command_status"
    )
    op.execute("DROP TYPE command_status_current")
    op.execute("ALTER TABLE device_commands ALTER COLUMN status SET DEFAULT 'pending'")
    op.drop_column("device_commands", "timeout_at")
    op.drop_column("device_commands", "failed_at")
    op.drop_column("device_commands", "sent_at")
