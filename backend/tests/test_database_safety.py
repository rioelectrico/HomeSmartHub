"""Safety checks protecting the PostgreSQL test database."""

import os
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.database_safety import escape_alembic_config_value, validate_test_database_url

BACKEND_ROOT = Path(__file__).parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _subprocess_environment(database_url: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "APP_SECRET_KEY": "a" * 48,
            "DATABASE_URL": database_url,
            "DEVICE_CREDENTIAL_ENCRYPTION_KEY": "b" * 48,
            "TEST_DATABASE_URL": database_url,
        }
    )
    return environment


def _assert_credential_absent(secret: str, completed: subprocess.CompletedProcess[str]) -> None:
    output = completed.stdout + completed.stderr
    if secret in output or quote(secret, safe="") in output:
        pytest.fail("subprocess output exposed the database credential sentinel")


def _run_verifier(database_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(REPO_ROOT / "scripts/verify.ps1"),
        ],
        cwd=REPO_ROOT,
        env=_subprocess_environment(database_url),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_test_database_url_rejects_non_postgresql_scheme() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        validate_test_database_url("sqlite+aiosqlite:///portero_test")


def test_test_database_url_requires_test_database_name() -> None:
    with pytest.raises(ValueError, match="_test"):
        validate_test_database_url("postgresql+psycopg://user:pass@localhost/portero")


def test_test_database_url_accepts_psycopg_postgresql_test_database() -> None:
    validate_test_database_url("postgresql+psycopg://user:pass@localhost/portero_test")


@pytest.mark.parametrize(
    "override",
    [
        "?dbname=portero",
        "?service=production",
        ";dbname=portero",
        "#service=production",
    ],
)
def test_test_database_url_rejects_connection_target_overrides(override: str) -> None:
    with pytest.raises(ValueError, match="override"):
        validate_test_database_url(
            f"postgresql+psycopg://user:password@localhost/guard_test{override}"
        )


def test_database_safety_cli_rejects_query_override_without_connecting() -> None:
    secret = secrets.token_urlsafe(18)
    database_url = f"postgresql+psycopg://user:{secret}@localhost/guard_test?dbname=portero"
    completed = subprocess.run(
        [sys.executable, "-m", "app.database_safety"],
        cwd=BACKEND_ROOT,
        env=_subprocess_environment(database_url),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode != 0
    _assert_credential_absent(secret, completed)


def test_effective_database_name_must_end_in_test() -> None:
    from app.database_safety import validate_effective_test_database_name

    with pytest.raises(ValueError, match="effective"):
        validate_effective_test_database_name("portero")


def test_conftest_rejects_query_override_during_collection() -> None:
    secret = secrets.token_urlsafe(18)
    database_url = f"postgresql+psycopg://user:{secret}@localhost/guard_test?dbname=portero"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/test_database_safety.py",
        ],
        cwd=BACKEND_ROOT,
        env=_subprocess_environment(database_url),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode != 0
    _assert_credential_absent(secret, completed)


def test_settings_validation_does_not_expose_malformed_url_credential() -> None:
    secret = secrets.token_urlsafe(18)
    database_url = f"postgresql+psycopg://user:{secret}@localhost:not-a-port/guard_test"
    completed = subprocess.run(
        [sys.executable, "-c", "from app.config import Settings; Settings()"],
        cwd=BACKEND_ROOT,
        env=_subprocess_environment(database_url),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode != 0
    _assert_credential_absent(secret, completed)


def test_alembic_failure_does_not_expose_percent_encoded_credential() -> None:
    secret = secrets.token_urlsafe(18)
    encoded_password = quote(f"{secret}@suffix", safe="")
    database_url = f"postgresql+psycopg://user:{encoded_password}@127.0.0.1:1/guard_test"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "missing_revision", "--sql"],
        cwd=BACKEND_ROOT,
        env=_subprocess_environment(database_url),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode != 0
    _assert_credential_absent(secret, completed)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell verifier is Windows-specific")
def test_verifier_failure_does_not_expose_malformed_url_credential() -> None:
    secret = secrets.token_urlsafe(18)
    database_url = f"postgresql+psycopg://user:{secret}@localhost:not-a-port/guard_test"
    completed = _run_verifier(database_url)

    assert completed.returncode != 0
    _assert_credential_absent(secret, completed)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell verifier is Windows-specific")
def test_verifier_rejects_query_override_without_exposing_credential() -> None:
    secret = secrets.token_urlsafe(18)
    database_url = f"postgresql+psycopg://user:{secret}@localhost/guard_test?dbname=portero"
    completed = _run_verifier(database_url)

    assert completed.returncode != 0
    _assert_credential_absent(secret, completed)


def test_live_preflight_confirms_effective_database_is_a_test_database() -> None:
    from app.database_safety import verify_effective_test_database

    database_name = verify_effective_test_database(os.environ["TEST_DATABASE_URL"])

    assert database_name.endswith("_test")


def test_initial_migration_downgrade_removes_pgcrypto() -> None:
    migration = Path(__file__).parents[1] / "alembic/versions/0001_initial_schema.py"
    assert 'op.execute("DROP EXTENSION IF EXISTS pgcrypto")' in migration.read_text()


def test_command_status_migration_converts_expired_and_round_trips_exact_enum() -> None:
    """Upgrade/downgrade must preserve rows while exposing only the versioned states."""

    test_url = os.environ["TEST_DATABASE_URL"]
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", escape_alembic_config_value(test_url))
    engine = create_engine(test_url)
    home_id = uuid4()
    device_id = uuid4()
    command_id = uuid4()

    def enum_labels() -> list[str]:
        with engine.connect() as connection:
            return list(
                connection.scalars(
                    text(
                        "SELECT enumlabel FROM pg_enum "
                        "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                        "WHERE typname = 'command_status' ORDER BY enumsortorder"
                    )
                )
            )

    try:
        command.downgrade(config, "0004_require_challenge_boot_id")
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO homes (id, name) VALUES (:id, 'Migration home')"),
                {"id": home_id},
            )
            connection.execute(
                text(
                    "INSERT INTO devices (id, home_id, device_id, name) "
                    "VALUES (:id, :home_id, 'PI-999999', 'Migration device')"
                ),
                {"id": device_id, "home_id": home_id},
            )
            connection.execute(
                text(
                    "INSERT INTO device_commands (id, device_id, command, status) "
                    "VALUES (:id, :device_id, 'camera.capture', 'expired')"
                ),
                {"id": command_id, "device_id": device_id},
            )
        assert enum_labels() == [
            "pending",
            "sent",
            "acknowledged",
            "completed",
            "failed",
            "expired",
        ]

        command.upgrade(config, "head")
        with engine.connect() as connection:
            upgraded = connection.execute(
                text("SELECT status::text, timeout_at FROM device_commands WHERE id = :id"),
                {"id": command_id},
            ).one()
        assert upgraded[0] == "timeout"
        assert enum_labels() == [
            "pending",
            "sent",
            "acknowledged",
            "completed",
            "failed",
            "timeout",
        ]

        command.downgrade(config, "0004_require_challenge_boot_id")
        with engine.connect() as connection:
            downgraded = connection.scalar(
                text("SELECT status::text FROM device_commands WHERE id = :id"),
                {"id": command_id},
            )
            timestamp_columns = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'device_commands' "
                    "AND column_name IN ('sent_at', 'failed_at', 'timeout_at')"
                )
            )
        assert downgraded == "expired"
        assert timestamp_columns == 0
        assert enum_labels() == [
            "pending",
            "sent",
            "acknowledged",
            "completed",
            "failed",
            "expired",
        ]
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM device_commands WHERE id = :id"), {"id": command_id}
            )
            connection.execute(text("DELETE FROM devices WHERE id = :id"), {"id": device_id})
            connection.execute(text("DELETE FROM homes WHERE id = :id"), {"id": home_id})
        engine.dispose()
