"""FastAPI composition. Run MVP1 with one Uvicorn worker (in-memory socket ownership)."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.conversation_coordinator import ConversationCoordinator
from app.ai.openai_realtime import OpenAIRealtimeProvider
from app.api.errors import ErrorBoundaryMiddleware, register_exception_handlers
from app.api.routes import (
    agent_router,
    commands_router,
    devices_router,
    homes_router,
    media_router,
    users_router,
)
from app.api.routes.audit import router as audit_router
from app.api.routes.auth import router as auth_router
from app.api.routes.diagnostics import router as diagnostics_router
from app.api.routes.events import router as events_router
from app.api.routes.statistics import router as statistics_router
from app.config import Settings, get_settings
from app.database import get_db
from app.devices import commands, presence
from app.devices.connections import device_connections
from app.logging import configure_logging
from app.middleware.correlation import CorrelationMiddleware, correlation_id
from app.schemas.errors import ErrorResponse
from app.services.diagnostics import postgresql_ready
from app.websocket.device import router as websocket_router

SHUTDOWN_TIMEOUT_SECONDS = 5.0


async def maintenance_loop(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    operation: str,
    callback: Callable[[AsyncSession, Settings], Awaitable[int]],
    interval: float,
) -> None:
    while True:
        identifier = str(uuid4())
        token = correlation_id.set(identifier)
        try:
            async with factory() as session:
                await callback(session, settings)
        except Exception:
            logging.getLogger("portero").error(
                "maintenance_iteration_failed",
                extra={
                    "correlation_id": identifier,
                    "operation": operation,
                },
            )
        finally:
            correlation_id.reset(token)
        await asyncio.sleep(interval)


async def cancel_maintenance_tasks(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel maintenance without allowing a non-cooperative callback to block shutdown."""

    for task in tasks:
        task.cancel()
    _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT_SECONDS)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=min(SHUTDOWN_TIMEOUT_SECONDS, 0.1))


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings if settings is not None else get_settings()
    engine = create_async_engine(str(configured.database_url), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        configure_logging(production=configured.app_env == "production")
        tasks = [
            asyncio.create_task(
                maintenance_loop(
                    factory,
                    configured,
                    "presence_offline",
                    presence.mark_stale_devices_offline,
                    configured.device_heartbeat_interval,
                ),
                name="presence-offline",
            ),
            asyncio.create_task(
                maintenance_loop(
                    factory,
                    configured,
                    "command_expiration",
                    commands.expire_commands,
                    min(configured.command_timeout_seconds, 5),
                ),
                name="command-expiration",
            ),
        ]
        application.state.maintenance_tasks = tasks
        try:
            yield
        finally:
            await cancel_maintenance_tasks(tasks)
            try:
                try:
                    await application.state.conversation_coordinator.shutdown()
                finally:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(
                            application.state.device_connections.close_all(),
                            SHUTDOWN_TIMEOUT_SECONDS,
                        )
            finally:
                await engine.dispose()

    application = FastAPI(
        title="Portero Inteligente",
        lifespan=lifespan,
        responses={
            status: {"model": ErrorResponse} for status in (400, 401, 403, 404, 409, 422, 500)
        },
    )
    application.state.settings = configured
    application.state.engine = engine
    application.state.session_factory = factory
    application.state.ai_provider = OpenAIRealtimeProvider(configured)
    application.state.device_connections = device_connections
    application.state.conversation_coordinator = ConversationCoordinator(
        factory, application.state.ai_provider, configured, device_connections
    )

    async def application_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise

    application.dependency_overrides[get_settings] = lambda: configured
    application.dependency_overrides[get_db] = application_db
    register_exception_handlers(application)
    application.add_middleware(ErrorBoundaryMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[configured.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID"],
    )
    application.add_middleware(CorrelationMiddleware)
    for router in (
        auth_router,
        homes_router,
        users_router,
        agent_router,
        devices_router,
        commands_router,
        media_router,
        websocket_router,
        events_router,
        audit_router,
        statistics_router,
        diagnostics_router,
    ):
        application.include_router(router)

    @application.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    @application.get("/ready", tags=["health"])
    async def ready() -> JSONResponse:
        if not await postgresql_ready(engine):
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "checks": {"postgresql": "down"},
                },
            )
        return JSONResponse(content={"status": "ready", "checks": {"postgresql": "up"}})

    return application


app = create_app()
