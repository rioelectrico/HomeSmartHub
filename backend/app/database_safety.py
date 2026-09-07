"""Validation for destructive database-test operations."""

import os
import sys
from urllib.parse import unquote, urlparse

from sqlalchemy import create_engine, text


def validate_test_database_url(database_url: str) -> None:
    """Accept only an explicitly named PostgreSQL test database URL."""

    try:
        parsed = urlparse(database_url)
        # Force urllib to validate bracketed hosts and integer ports. Accessing
        # these properties is deliberately part of the safety check.
        _ = (parsed.hostname, parsed.port)
    except (TypeError, ValueError):
        raise ValueError("TEST_DATABASE_URL is not a valid PostgreSQL URL") from None

    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("TEST_DATABASE_URL must use a PostgreSQL URL scheme")

    if parsed.params or parsed.query or parsed.fragment or ";" in parsed.path:
        raise ValueError("TEST_DATABASE_URL connection target overrides are not permitted")

    database_name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    if not database_name.endswith("_test"):
        raise ValueError("TEST_DATABASE_URL database name must end in '_test'")


def verify_effective_test_database(database_url: str) -> str:
    """Connect and prove PostgreSQL resolved the URL to an explicit test database."""

    validate_test_database_url(database_url)
    engine = None
    try:
        engine = create_engine(database_url, connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            database_name = connection.scalar(text("SELECT current_database()"))
    except Exception:
        raise ValueError("TEST_DATABASE_URL effective database could not be verified") from None
    finally:
        if engine is not None:
            engine.dispose()

    return validate_effective_test_database_name(database_name)


def validate_effective_test_database_name(database_name: object) -> str:
    """Require a server-reported database name reserved for automated tests."""

    if not isinstance(database_name, str) or not database_name.endswith("_test"):
        raise ValueError("TEST_DATABASE_URL effective database name must end in '_test'")
    return database_name


def escape_alembic_config_value(value: str) -> str:
    """Escape ConfigParser interpolation tokens before setting an Alembic option."""

    return value.replace("%", "%%")


def main() -> int:
    """Run a credential-safe static and live preflight for the verification script."""

    database_url = os.environ.get("TEST_DATABASE_URL")
    try:
        if not database_url:
            raise ValueError("missing TEST_DATABASE_URL")
        verify_effective_test_database(database_url)
    except Exception:
        print("TEST_DATABASE_URL failed safety preflight.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
