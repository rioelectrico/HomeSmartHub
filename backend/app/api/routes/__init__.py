"""HTTP route modules."""

from app.api.routes.agent import router as agent_router
from app.api.routes.commands import router as commands_router
from app.api.routes.devices import router as devices_router
from app.api.routes.homes import router as homes_router
from app.api.routes.media import router as media_router
from app.api.routes.users import router as users_router

__all__ = [
    "agent_router",
    "commands_router",
    "devices_router",
    "homes_router",
    "media_router",
    "users_router",
]
