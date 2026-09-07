"""Alembic environment configured from the application settings."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401 -- register all metadata before migration discovery
from alembic import context
from app.config import get_settings
from app.database.base import Base
from app.database_safety import escape_alembic_config_value

config = context.config
# Settings is the only source of the runtime URL. Tests set DATABASE_URL from a
# separately validated TEST_DATABASE_URL before Alembic loads this module.
config.set_main_option(
    "sqlalchemy.url", escape_alembic_config_value(str(get_settings().database_url))
)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
