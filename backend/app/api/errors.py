"""Consistent, non-sensitive API error responses."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.schemas.errors import ErrorBody, ErrorResponse, ValidationDetail


class APIError(Exception):
    """An expected API error with a stable machine-readable code."""

    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code


def error_response(
    request: Request, status_code: int, code: str, details: list[ValidationDetail] | None = None
) -> JSONResponse:
    messages = {
        "VALIDATION_ERROR": "The request contains invalid values.",
        "NOT_AUTHENTICATED": "Authentication is required.",
        "PERMISSION_DENIED": "You do not have permission for this operation.",
        "NOT_FOUND": "The requested resource was not found.",
        "INTERNAL_ERROR": "The request could not be completed.",
    }
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=messages.get(code, "The operation could not be completed."),
            details=details or [],
            correlation_id=getattr(request.state, "correlation_id", ""),
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def api_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Render known domain errors without disclosing database details."""

    assert isinstance(error, APIError)
    return error_response(request, error.status_code, error.code)


async def validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Render malformed requests with a stable code and standard validation context."""

    assert isinstance(error, RequestValidationError)
    return error_response(
        request,
        422,
        "VALIDATION_ERROR",
        [
            ValidationDetail(location=list(item["loc"]), type=item["type"])
            for item in error.errors()
        ],
    )


async def http_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, HTTPException)
    code = {
        401: "NOT_AUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
    }.get(error.status_code, "REQUEST_ERROR")
    return error_response(request, error.status_code, code)


async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
    import logging

    # Exception text, request URL/query, bodies, and traceback may contain credentials.
    logging.getLogger("portero").error(
        "request_failed",
        extra={
            "correlation_id": getattr(request.state, "correlation_id", ""),
            "operation": "http_request",
        },
    )
    return error_response(request, 500, "INTERNAL_ERROR")


class ErrorBoundaryMiddleware:
    """Handle failures inside CORS/correlation so safe 500s receive both headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as error:
            response = await unexpected_error_handler(Request(scope), error)
            if not started:
                await response(scope, receive, send)


def register_exception_handlers(application: FastAPI) -> None:
    """Install API error contracts on an application that includes these routers."""

    application.add_exception_handler(APIError, api_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(HTTPException, http_error_handler)
    application.add_exception_handler(Exception, unexpected_error_handler)
