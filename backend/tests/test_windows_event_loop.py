"""Windows server-loop compatibility with psycopg async."""

import importlib.util
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.skipif(os.name != "nt", reason="Windows event-loop compatibility")
def test_documented_uvicorn_loop_can_query_postgresql(settings) -> None:
    """Removing the selector factory must break the Windows runtime database boundary."""

    spec = importlib.util.find_spec("app.event_loop")
    assert spec is not None, "the Windows-compatible Uvicorn loop factory is missing"

    from app.event_loop import selector_loop_factory

    loop = selector_loop_factory()
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)

    async def query_database() -> int:
        async with engine.connect() as connection:
            return int(await connection.scalar(text("SELECT 1")))

    try:
        assert loop.run_until_complete(query_database()) == 1
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()
