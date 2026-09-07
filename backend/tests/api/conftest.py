"""Composed application clients for operational API tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.models import HomeUser, Permission, RolePermission


@pytest.fixture
def application(settings):
    from app.main import create_app

    return create_app(settings)


@pytest.fixture
async def app_client(application):
    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        yield client
    await application.state.engine.dispose()


@pytest.fixture
async def operational_client(application, app_client, db, admin, home, roles):
    async def override_db():
        yield db

    application.dependency_overrides[get_db] = override_db
    names = ["events.read", "audit.read", "devices.read", "camera.view", "homes.read"]
    permissions = [Permission(name=name) for name in names]
    db.add_all(
        [*permissions, HomeUser(home_id=home.id, user_id=admin.id, role_id=roles["owner"].id)]
    )
    await db.flush()
    db.add_all([RolePermission(role_id=roles["owner"].id, permission_id=p.id) for p in permissions])
    await db.commit()
    login = await app_client.post(
        "/api/auth/login",
        json={
            "identifier": admin.username,
            "password": "ValidPass!42",
        },
    )
    assert login.status_code == 204
    return app_client
