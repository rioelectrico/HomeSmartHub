"""Application composition, readiness, correlation, and safe error contracts."""

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from app.api.errors import APIError


async def test_health_does_not_depend_on_database(application, app_client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("postgresql://secret:password@private")

    monkeypatch.setattr(type(application.state.engine), "connect", unavailable)
    response = await app_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_ready_reports_database_failure(application, app_client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("postgresql://secret:password@private")

    monkeypatch.setattr(type(application.state.engine), "connect", unavailable)
    response = await app_client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["postgresql"] == "down"
    assert "secret" not in response.text


async def test_ready_uses_configured_postgresql(app_client):
    response = await app_client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"postgresql": "up"}}


async def test_composed_routers_require_authentication(application, app_client):
    for path in (
        "/api/auth/me",
        "/api/homes",
        "/api/homes/00000000-0000-0000-0000-000000000001/devices",
    ):
        response = await app_client.get(path)
        assert response.status_code == 401, (path, response.text)
        assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"
    paths = application.openapi()["paths"]
    assert "/api/device/media" in paths
    assert "/api/homes/{home_id}/agent" in paths
    assert "/api/homes/{home_id}/users" in paths
    assert "/api/homes/{home_id}/devices/{device_id}/commands" in paths
    assert str(application.url_path_for("device_socket")) == "/ws/device"


async def test_correlation_is_validated_and_returned(app_client):
    supplied = "79cd1515-59d4-47bf-a679-f2c3f210eaf6"
    response = await app_client.get("/health", headers={"X-Correlation-ID": supplied})
    assert response.headers["x-correlation-id"] == supplied
    invalid = await app_client.get(
        "/missing", headers={"X-Correlation-ID": "sensitive-arbitrary-text"}
    )
    assert UUID(invalid.headers["x-correlation-id"])
    assert invalid.json()["error"]["correlation_id"] == invalid.headers["x-correlation-id"]
    assert "sensitive-arbitrary-text" not in invalid.text


async def test_validation_domain_http_and_unexpected_errors_are_safe(
    application, app_client, caplog
):
    @application.get("/domain")
    async def domain():
        raise APIError(409, "CONFLICT")

    @application.get("/crash")
    async def crash():
        raise RuntimeError("secret-password-do-not-leak")

    @application.get("/validate")
    async def validate(value: int):
        return value

    for path, status, code in (
        ("/domain", 409, "CONFLICT"),
        ("/crash", 500, "INTERNAL_ERROR"),
        ("/validate?value=secret-password-do-not-leak", 422, "VALIDATION_ERROR"),
        ("/missing", 404, "NOT_FOUND"),
    ):
        response = await app_client.get(path)
        assert response.status_code == status
        error = response.json()["error"]
        assert set(error) == {"code", "message", "details", "correlation_id"}
        assert error["code"] == code
        assert error["message"]
        assert error["correlation_id"] == response.headers["x-correlation-id"]
        assert "secret-password-do-not-leak" not in response.text
    assert "secret-password-do-not-leak" not in caplog.text


async def test_cors_accepts_only_exact_frontend_origin(app_client, settings):
    for origin, allowed in ((settings.frontend_origin, True), ("https://evil.test", False)):
        response = await app_client.options(
            "/api/auth/login",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        )
        assert (response.status_code == 200) is allowed
        assert (response.headers.get("access-control-allow-origin") == origin) is allowed
    response = await app_client.get("/missing", headers={"Origin": settings.frontend_origin})
    assert response.headers["access-control-allow-origin"] == settings.frontend_origin


async def test_malformed_home_id_is_validation_error(operational_client):
    response = await operational_client.get("/api/homes/invalid-home/events")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_openapi_describes_common_error_envelope(application):
    responses = application.openapi()["paths"]["/api/homes/{home_id}/events"]["get"]["responses"]
    for status in ("401", "403", "404", "422", "500"):
        assert status in responses
        assert (
            responses[status]["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/ErrorResponse"
        )


async def test_ready_times_out_a_stalled_database(application, app_client, monkeypatch):
    from app.services import diagnostics

    @asynccontextmanager
    async def stalled(*args, **kwargs):
        await asyncio.sleep(10)
        yield

    monkeypatch.setattr(type(application.state.engine), "connect", stalled)
    monkeypatch.setattr(diagnostics, "READINESS_TIMEOUT_SECONDS", 0.02, raising=False)
    # Keep a wide outer guard on Windows CI: SelectorEventLoop timer scheduling can
    # exceed 200 ms under load even though the inner readiness timeout is 20 ms.
    response = await asyncio.wait_for(app_client.get("/ready"), timeout=1.0)
    assert response.status_code == 503
    assert response.json()["checks"]["postgresql"] == "down"
