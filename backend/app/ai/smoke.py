"""Explicit, bounded OpenAI Realtime connectivity smoke command."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from app.ai.realtime import AIRealtimeProvider, AIRealtimeSession, RealtimeSessionConfig
    from app.config import Settings

_SMOKE_TIMEOUT_SECONDS = 10.0
_AUDIO_SEND_TIMEOUT_SECONDS = 2.0
_CLOSE_TIMEOUT_SECONDS = 5.0
_PCM16_SAMPLE_RATE = 24_000
_PCM16_BYTES_PER_SAMPLE = 2
_SMOKE_AUDIO_MILLISECONDS = 20


class _SmokeGate(BaseSettings):
    """Load only the opt-in flag before importing application infrastructure."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    openai_realtime_smoke: bool = Field(
        default=False,
        validation_alias="OPENAI_REALTIME_SMOKE",
    )


class _ProviderFactory(Protocol):
    def __call__(
        self,
        settings: Settings,
        *,
        open_timeout: float,
    ) -> AIRealtimeProvider: ...


def _provider_factory() -> _ProviderFactory:
    # This import reaches application models and database initialization, so it
    # must remain behind the independently validated opt-in gate.
    from app.ai.openai_realtime import OpenAIRealtimeProvider

    return OpenAIRealtimeProvider


def _open_provider(settings: Settings) -> AIRealtimeProvider:
    return _provider_factory()(settings, open_timeout=_SMOKE_TIMEOUT_SECONDS)


def _session_config() -> RealtimeSessionConfig:
    from app.ai.realtime import RealtimeSessionConfig

    return RealtimeSessionConfig(
        model="env-default",
        instructions="Responde brevemente en español como portero.",
        voice="default",
        language="es-AR",
    )


def _is_ready(event: object) -> bool:
    from app.ai.realtime import SessionReady

    return isinstance(event, SessionReady)


def _zero_pcm() -> bytes:
    sample_count = _PCM16_SAMPLE_RATE * _SMOKE_AUDIO_MILLISECONDS // 1_000
    return bytes(sample_count * _PCM16_BYTES_PER_SAMPLE)


async def _close_session(session: AIRealtimeSession) -> None:
    async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
        await session.close()


async def _await_cleanup(cleanup: asyncio.Task[None]) -> None:
    """Finish cleanup despite repeated caller cancellation, then propagate it."""

    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True

    if cleanup.cancelled():
        raise asyncio.CancelledError
    error = cleanup.exception()
    if error is not None:
        raise error
    if interrupted:
        raise asyncio.CancelledError


async def _run_smoke(settings: Settings) -> None:
    provider = _open_provider(settings)
    session: AIRealtimeSession | None = None
    try:
        # A single deadline covers both transport opening/configuration and the
        # normalized readiness event observed through the public provider contract.
        async with asyncio.timeout(_SMOKE_TIMEOUT_SECONDS):
            session = await provider.open_session(_session_config())
            ready = await anext(session.events())
            if not _is_ready(ready):
                raise RuntimeError("AI_UNAVAILABLE")

        # One 20 ms frame is enough to exercise the audio boundary and remains
        # well below the normative one-second ceiling. Its own deadline prevents
        # transport backpressure from blocking cleanup indefinitely.
        async with asyncio.timeout(_AUDIO_SEND_TIMEOUT_SECONDS):
            await session.send_audio(_zero_pcm())
    finally:
        if session is not None:
            cleanup = asyncio.create_task(_close_session(session), name="openai-smoke-close")
            await _await_cleanup(cleanup)


def _print_refused(correlation_id: UUID) -> int:
    print(
        "status=refused reason=opt_in_required "
        f"required=OPENAI_REALTIME_SMOKE=true correlation_id={correlation_id}",
        file=sys.stderr,
    )
    return 2


def _print_unavailable(correlation_id: UUID, *, reason: str | None = None) -> int:
    reason_field = f" reason={reason}" if reason is not None else ""
    print(
        f"status=unavailable{reason_field} correlation_id={correlation_id}",
        file=sys.stderr,
    )
    return 1


def run_smoke(settings: Settings, *, correlation_id: UUID | None = None) -> int:
    """Run one real session only after explicit opt-in; return a process exit code."""

    stable_correlation_id = correlation_id or uuid4()
    if not settings.openai_realtime_smoke:
        return _print_refused(stable_correlation_id)

    try:
        asyncio.run(_run_smoke(settings))
    except Exception:
        # Provider and transport details may contain credentials or raw payloads.
        # Only the stable public status and local correlation identifier escape.
        return _print_unavailable(stable_correlation_id)

    print(f"status=available correlation_id={stable_correlation_id}")
    return 0


def main() -> int:
    correlation_id = uuid4()
    try:
        gate = _SmokeGate()
    except Exception:
        return _print_unavailable(correlation_id, reason="configuration_invalid")
    if not gate.openai_realtime_smoke:
        return _print_refused(correlation_id)

    try:
        # Full settings and provider imports are deliberately deferred until the
        # explicit gate has succeeded, and their diagnostics are never printed.
        from app.config import get_settings

        settings = get_settings()
    except Exception:
        return _print_unavailable(correlation_id, reason="configuration_invalid")
    return run_smoke(settings, correlation_id=correlation_id)


if __name__ == "__main__":
    raise SystemExit(main())
