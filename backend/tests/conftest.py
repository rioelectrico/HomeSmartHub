"""PostgreSQL-only isolated database fixtures."""

import asyncio
import inspect
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.database_safety import (
    escape_alembic_config_value,
    validate_effective_test_database_name,
    verify_effective_test_database,
)

_test_url = os.environ.get("TEST_DATABASE_URL")
if not _test_url:
    pytest.exit("TEST_DATABASE_URL is required; refusing to run database tests without it.")
try:
    verify_effective_test_database(_test_url)
except Exception:
    pytest.exit("TEST_DATABASE_URL failed safety preflight; refusing unsafe database.")

# Application code receives its database URL only through Settings.database_url.
os.environ["DATABASE_URL"] = _test_url

from app.database.base import Base  # noqa: E402
from app.models import Device, Home, Permission, Role, User  # noqa: E402,F401


@pytest.hookimpl
def pytest_asyncio_loop_factories(config, item):
    """Use selector loops on Windows without overriding pytest-asyncio's policy fixture."""

    if os.name == "nt":
        return {"windows_selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(scope="session", autouse=True)
def migrate_test_database() -> None:
    """Bring the explicitly guarded PostgreSQL test database to head once."""

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", escape_alembic_config_value(_test_url))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def async_engine():
    engine = create_async_engine(_test_url, pool_pre_ping=True)
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
async def clean_database(request, async_engine) -> AsyncGenerator[None, None]:
    """Reset all application tables between tests without ever touching non-test DBs."""

    if not inspect.iscoroutinefunction(request.node.obj) and "db" not in request.fixturenames:
        yield
        return
    names = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
    async with async_engine.begin() as connection:
        database_name = await connection.scalar(text("SELECT current_database()"))
        try:
            validate_effective_test_database_name(database_name)
        except ValueError:
            pytest.exit("Connected database failed safety preflight; refusing table cleanup.")
        await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def db(async_engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def user(db: AsyncSession) -> User:
    entity = User(username="user", email="user@example.test", password_hash="not-a-password")
    db.add(entity)
    await db.commit()
    return entity


@pytest.fixture
async def roles(db: AsyncSession) -> dict[str, Role]:
    owner = Role(name="owner")
    read_only = Role(name="read_only")
    db.add_all([owner, read_only])
    await db.commit()
    return {"owner": owner, "read_only": read_only}


@pytest.fixture
async def home(db: AsyncSession) -> Home:
    entity = Home(name="Casa")
    db.add(entity)
    await db.commit()
    return entity


@pytest.fixture
async def two_homes(db: AsyncSession) -> list[Home]:
    entities = [Home(name="Casa"), Home(name="Oficina")]
    db.add_all(entities)
    await db.commit()
    return entities


@pytest.fixture
def settings(tmp_path: Path):
    """Use explicit non-production settings for auth contracts."""

    from app.config import Settings

    return Settings(
        DATABASE_URL=_test_url,
        APP_SECRET_KEY="a" * 48,
        DEVICE_CREDENTIAL_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        MEDIA_ROOT=tmp_path,
    )


@pytest.fixture
async def admin(db: AsyncSession) -> User:
    from app.auth.passwords import hash_password

    entity = User(
        username="admin",
        email="admin@example.test",
        password_hash=hash_password("ValidPass!42"),
    )
    db.add(entity)
    await db.commit()
    return entity


@pytest.fixture
async def client(db: AsyncSession, settings):
    """Exercise the router against the isolated PostgreSQL session."""

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.errors import register_exception_handlers
    from app.api.routes import (
        agent_router,
        commands_router,
        devices_router,
        homes_router,
        media_router,
        users_router,
    )
    from app.api.routes.auth import router as auth_router
    from app.config import get_settings
    from app.database import get_db

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(auth_router)
    application.include_router(homes_router)
    application.include_router(users_router)
    application.include_router(agent_router)
    application.include_router(devices_router)
    application.include_router(commands_router)
    application.include_router(media_router)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as test_client:
        yield test_client
