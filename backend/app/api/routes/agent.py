"""Home-scoped AI agent configuration routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import CurrentUser, DatabaseSession
from app.auth.rbac import PermissionName, require_permission
from app.models import AgentConfig
from app.schemas.agent import AgentConfigRequest, AgentConfigResponse
from app.services.agent import get_agent_config, save_agent_config

router = APIRouter(prefix="/api/homes/{home_id}/agent", tags=["agent"])

AgentViewPermission = Annotated[None, Depends(require_permission(PermissionName.AGENT_VIEW))]
AgentEditPermission = Annotated[None, Depends(require_permission(PermissionName.AGENT_EDIT))]
DEFAULT_SYSTEM_PROMPT = "Recibí a visitantes de forma cordial y pedí identificación."


def response_from_config(config: AgentConfig) -> AgentConfigResponse:
    """Map persistence fields without exposing internal identifiers."""

    return AgentConfigResponse(
        name=config.name,
        system_prompt=config.system_prompt or DEFAULT_SYSTEM_PROMPT,
        language=config.language,
        voice=config.voice,
        voice_speed=config.voice_speed,
        realtime_model=config.realtime_model,
        openai_session_options=config.openai_session_options or {},
        enabled=config.enabled,
        updated_at=config.updated_at,
    )


@router.get("", response_model=AgentConfigResponse)
async def get_agent(
    home_id: UUID,
    db: DatabaseSession,
    _: AgentViewPermission,
) -> AgentConfigResponse:
    """Return a configured agent or the safe, editable initial defaults."""

    config = await get_agent_config(db, home_id)
    if config is None:
        return AgentConfigResponse(
            name="Portero",
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            language="es-AR",
            voice="default",
            voice_speed=1.0,
            realtime_model="env-default",
            openai_session_options={},
            enabled=False,
            updated_at=None,
        )
    return response_from_config(config)


@router.put("", response_model=AgentConfigResponse)
async def update_agent(
    home_id: UUID,
    payload: AgentConfigRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    _: AgentEditPermission,
) -> AgentConfigResponse:
    """Persist the last valid full configuration for the authorized home."""

    config = await save_agent_config(
        db,
        actor_id=current_user.id,
        home_id=home_id,
        payload=payload,
    )
    await db.commit()
    await db.refresh(config)
    return response_from_config(config)
