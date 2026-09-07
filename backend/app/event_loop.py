"""Event-loop factories for runtime dependencies with platform constraints."""

import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Return the selector loop required by psycopg async on Windows."""

    return asyncio.SelectorEventLoop()
