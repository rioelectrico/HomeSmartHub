"""Transactional operations for home agent configuration."""

from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit
from app.models import AgentConfig
from app.schemas.agent import AgentConfigRequest


async def get_agent_config(db: AsyncSession, home_id: UUID) -> AgentConfig | None:
    """Return the single persisted agent configuration for a home, if configured."""

    return cast(
        AgentConfig | None,
        await db.scalar(select(AgentConfig).where(AgentConfig.home_id == home_id)),
    )


async def save_agent_config(
    db: AsyncSession,
    *,
    actor_id: UUID,
    home_id: UUID,
    payload: AgentConfigRequest,
) -> AgentConfig:
    """Persist the last valid full configuration and audit metadata only."""

    values = payload.model_dump()
    statement = insert(AgentConfig).values(home_id=home_id, **values)
    config = (
        await db.scalars(
            statement.on_conflict_do_update(
                constraint="uq_agent_configs_home",
                set_={
                    **{field: getattr(statement.excluded, field) for field in values},
                    "updated_at": func.clock_timestamp(),
                },
            )
            .returning(AgentConfig)
            .execution_options(populate_existing=True)
        )
    ).one()
    write_audit(
        db,
        action="agent.updated",
        user_id=actor_id,
        home_id=home_id,
        detail="Agent configuration updated",
        context={"enabled": payload.enabled, "voice": payload.voice},
    )
    return config
