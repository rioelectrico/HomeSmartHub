"""Bounded UUID correlation propagated through HTTP and logging context."""

from contextvars import ContextVar
from uuid import UUID, uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = Headers(scope=scope).get("x-correlation-id", "")
        try:
            value = str(UUID(supplied)) if len(supplied) == 36 else str(uuid4())
        except ValueError:
            value = str(uuid4())
        scope.setdefault("state", {})["correlation_id"] = value
        token = correlation_id.set(value)

        async def correlated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Correlation-ID"] = value
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        finally:
            correlation_id.reset(token)
