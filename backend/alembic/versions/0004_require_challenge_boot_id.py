"""Require every authentication challenge to be bound to a boot.

Revision ID: 0004_require_challenge_boot_id
Revises: 0003_add_device_auth_tables
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_require_challenge_boot_id"
down_revision = "0003_add_device_auth_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Challenges are intentionally short-lived and single-use. An old unbound
    # row cannot be made trustworthy, so discard it instead of inventing a boot.
    op.execute("DELETE FROM device_auth_challenges WHERE boot_id IS NULL")
    op.alter_column(
        "device_auth_challenges",
        "boot_id",
        existing_type=sa.String(length=128),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "device_auth_challenges",
        "boot_id",
        existing_type=sa.String(length=128),
        nullable=True,
    )
