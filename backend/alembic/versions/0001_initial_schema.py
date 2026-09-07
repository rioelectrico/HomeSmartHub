"""Initial Portero PostgreSQL schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

device_status = postgresql.ENUM(
    "online", "offline", "provisioning", "disabled", name="device_status", create_type=False
)
command_status = postgresql.ENUM(
    "pending",
    "sent",
    "acknowledged",
    "completed",
    "failed",
    "expired",
    name="command_status",
    create_type=False,
)
conversation_status = postgresql.ENUM(
    "open", "closed", name="conversation_status", create_type=False
)
message_role = postgresql.ENUM(
    "user", "assistant", "system", name="message_role", create_type=False
)
media_type = postgresql.ENUM("image", name="media_type", create_type=False)


def uuid_pk() -> sa.Column[sa.UUID]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )


def timestamp(name: str = "created_at") -> sa.Column[sa.DateTime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    for enum in (device_status, command_status, conversation_status, message_role, media_type):
        enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        uuid_pk(),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        timestamp(),
        timestamp("updated_at"),
    )
    op.create_table(
        "roles",
        uuid_pk(),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
    )
    op.create_table(
        "permissions",
        uuid_pk(),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
    )
    op.create_table(
        "homes",
        uuid_pk(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default="America/Argentina/Buenos_Aires",
        ),
        timestamp(),
    )
    op.create_table(
        "role_permissions",
        uuid_pk(),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
    )
    op.create_table(
        "home_users",
        uuid_pk(),
        sa.Column(
            "home_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("homes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        timestamp(),
        sa.UniqueConstraint("home_id", "user_id", name="uq_home_users_home_user"),
    )
    op.create_table(
        "agent_configs",
        uuid_pk(),
        sa.Column(
            "home_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("homes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("voice_speed", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("system_prompt", sa.Text()),
        timestamp("updated_at"),
        sa.UniqueConstraint("home_id", name="uq_agent_configs_home"),
    )
    op.create_table(
        "devices",
        uuid_pk(),
        sa.Column(
            "home_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("homes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", device_status, nullable=False, server_default="offline"),
        sa.Column("credential_encrypted", sa.Text()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        timestamp(),
        timestamp("updated_at"),
    )
    op.create_index("ix_devices_last_seen_at", "devices", ["last_seen_at"])
    op.create_table(
        "device_commands",
        uuid_pk(),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("command", sa.String(128), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", command_status, nullable=False, server_default="pending"),
        sa.Column("result", postgresql.JSONB()),
        timestamp(),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_device_commands_device_id_created_at", "device_commands", ["device_id", "created_at"]
    )
    op.create_table(
        "events",
        uuid_pk(),
        sa.Column(
            "home_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("homes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
        ),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        timestamp(),
    )
    op.create_index("ix_events_home_id_created_at", "events", ["home_id", "created_at"])
    op.create_table(
        "conversations",
        uuid_pk(),
        sa.Column(
            "home_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("homes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("status", conversation_status, nullable=False, server_default="open"),
        timestamp(),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "conversation_messages",
        uuid_pk(),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", message_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        timestamp(),
    )
    op.create_table(
        "media",
        uuid_pk(),
        sa.Column(
            "home_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("homes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "command_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_commands.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
        ),
        sa.Column("media_type", media_type, nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("path", sa.String(512), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        timestamp(),
    )
    op.create_table(
        "auth_sessions",
        uuid_pk(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        timestamp(),
    )
    op.create_table(
        "audit_logs",
        uuid_pk(),
        sa.Column(
            "home_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("homes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column(
            "context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("ip_address", sa.String(64)),
        timestamp(),
    )


def downgrade() -> None:
    for table in (
        "audit_logs",
        "auth_sessions",
        "media",
        "conversation_messages",
        "conversations",
        "events",
        "device_commands",
        "devices",
        "agent_configs",
        "home_users",
        "role_permissions",
        "homes",
        "permissions",
        "roles",
        "users",
    ):
        op.drop_table(table)
    for enum in (media_type, message_role, conversation_status, command_status, device_status):
        enum.drop(op.get_bind(), checkfirst=True)
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
