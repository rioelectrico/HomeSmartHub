"""Behavioral checks for the opt-in OpenAI Realtime smoke command."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.ai import smoke
from app.ai.realtime import RealtimeEvent, SessionReady


def test_smoke_refuses_without_opt_in(settings, capsys) -> None:
    settings.openai_realtime_smoke = False

    assert smoke.run_smoke(settings) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "OPENAI_REALTIME_SMOKE=true" in captured.err


@dataclass
class _Session:
    emitted: list[RealtimeEvent] = field(default_factory=lambda: [SessionReady()])
    audio: list[bytes] = field(default_factory=list)
    close_count: int = 0
    send_error: Exception | None = None

    async def send_audio(self, pcm16: bytes) -> None:
        self.audio.append(pcm16)
        if self.send_error is not None:
            raise self.send_error

    async def _events(self) -> AsyncIterator[RealtimeEvent]:
        for event in self.emitted:
            yield event

    def events(self) -> AsyncIterator[RealtimeEvent]:
        return self._events()

    async def cancel_response(self) -> None:
        return None

    async def close(self) -> None:
        self.close_count += 1


class _NeverReadySession(_Session):
    async def _events(self) -> AsyncIterator[RealtimeEvent]:
        await asyncio.Event().wait()
        yield SessionReady()  # pragma: no cover - makes this an async generator


class _BlockedSendSession(_Session):
    def __init__(self) -> None:
        super().__init__()
        self.send_started = asyncio.Event()
        self.send_cancelled = False

    async def send_audio(self, pcm16: bytes) -> None:
        self.audio.append(pcm16)
        self.send_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.send_cancelled = True


class _CancellationSafeCloseSession(_BlockedSendSession):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_completed = False
        self.close_cancelled = 0

    async def close(self) -> None:
        self.close_count += 1
        self.close_started.set()
        try:
            await self.close_release.wait()
        except asyncio.CancelledError:
            self.close_cancelled += 1
            raise
        self.close_completed = True


@dataclass
class _Provider:
    session: _Session
    configs: list[object] = field(default_factory=list)

    async def open_session(self, config) -> _Session:
        self.configs.append(config)
        return self.session


def test_opted_in_smoke_uses_normalized_ready_and_bounded_zero_pcm(
    settings, monkeypatch, capsys
) -> None:
    settings.openai_realtime_smoke = True
    session = _Session()
    provider = _Provider(session)
    constructor_calls: list[tuple[object, float]] = []

    def build_provider(received_settings, *, open_timeout: float):
        constructor_calls.append((received_settings, open_timeout))
        return provider

    monkeypatch.setattr(smoke, "_provider_factory", lambda: build_provider)

    assert smoke.run_smoke(settings) == 0

    assert constructor_calls == [(settings, 10.0)]
    assert len(provider.configs) == 1
    assert len(session.audio) == 1
    assert 0 < len(session.audio[0]) <= 24_000 * 2
    assert set(session.audio[0]) == {0}
    assert session.close_count == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "status=available" in captured.out
    assert "correlation_id=" in captured.out


def test_smoke_failure_is_sanitized_and_session_always_closes(
    settings, monkeypatch, capsys
) -> None:
    settings.openai_realtime_smoke = True
    secret = "sk-test-must-stay-secret"
    session = _Session(send_error=RuntimeError(f"provider leaked {secret}"))
    provider = _Provider(session)
    monkeypatch.setattr(
        smoke,
        "_open_provider",
        lambda received_settings: provider,
    )

    assert smoke.run_smoke(settings) == 1

    assert session.close_count == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "status=unavailable" in output.err
    assert "correlation_id=" in output.err
    assert secret not in output.err


def test_readiness_timeout_closes_the_open_session(settings, monkeypatch, capsys) -> None:
    settings.openai_realtime_smoke = True
    session = _NeverReadySession()
    provider = _Provider(session)
    monkeypatch.setattr(smoke, "_SMOKE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        smoke,
        "_open_provider",
        lambda received_settings: provider,
    )

    assert smoke.run_smoke(settings) == 1

    assert session.close_count == 1
    assert "status=unavailable" in capsys.readouterr().err


async def test_blocked_audio_send_times_out_closes_and_leaves_no_tasks(
    settings, monkeypatch
) -> None:
    settings.openai_realtime_smoke = True
    session = _BlockedSendSession()
    provider = _Provider(session)
    monkeypatch.setattr(smoke, "_AUDIO_SEND_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(smoke, "_open_provider", lambda received_settings: provider, raising=False)
    baseline = set(asyncio.all_tasks())
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(smoke._run_smoke(settings), timeout=0.5)

    assert loop.time() - started < 0.2
    assert session.send_cancelled is True
    assert session.close_count == 1
    await asyncio.sleep(0)
    assert {task for task in asyncio.all_tasks() if task not in baseline} == set()


async def test_repeated_cancellation_cannot_interrupt_session_cleanup(
    settings, monkeypatch
) -> None:
    settings.openai_realtime_smoke = True
    session = _CancellationSafeCloseSession()
    provider = _Provider(session)
    monkeypatch.setattr(smoke, "_open_provider", lambda received_settings: provider, raising=False)
    baseline = set(asyncio.all_tasks())
    task = asyncio.create_task(smoke._run_smoke(settings))
    await session.send_started.wait()

    task.cancel()
    await session.close_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    session.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.close_count == 1
    assert session.close_cancelled == 0
    assert session.close_completed is True
    await asyncio.sleep(0)
    assert {pending for pending in asyncio.all_tasks() if pending not in baseline} == set()


_STABLE_STATUS = re.compile(
    r"^status=(?:refused|unavailable)(?: reason=[a-z_]+)?"
    r"(?: required=OPENAI_REALTIME_SMOKE=true)? correlation_id="
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\n$"
)


def _run_cli(overrides: dict[str, str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in (
        "DATABASE_URL",
        "APP_SECRET_KEY",
        "DEVICE_CREDENTIAL_ENCRYPTION_KEY",
        "OPENAI_API_KEY",
        "OPENAI_REALTIME_SMOKE",
    ):
        env.pop(name, None)
    env.update(overrides)
    backend_root = Path(__file__).resolve().parents[2]
    inherited_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        os.pathsep.join((str(backend_root), inherited_pythonpath))
        if inherited_pythonpath
        else str(backend_root)
    )
    return subprocess.run(
        [sys.executable, "-m", "app.ai.smoke"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


@pytest.mark.parametrize("database_url", [None, "not-a-database-url"])
def test_cli_refuses_before_missing_or_invalid_application_settings(
    database_url, tmp_path: Path
) -> None:
    overrides = {"OPENAI_REALTIME_SMOKE": "false"}
    if database_url is not None:
        overrides["DATABASE_URL"] = database_url

    result = _run_cli(overrides, cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert _STABLE_STATUS.fullmatch(result.stderr)
    assert "Traceback" not in result.stderr
    assert "ValidationError" not in result.stderr


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "OPENAI_REALTIME_SMOKE": "true",
            "DATABASE_URL": "postgresql+psycopg://private-user:private-password@localhost/not-valid",
        },
        {"OPENAI_REALTIME_SMOKE": "not-a-boolean"},
    ],
)
def test_cli_sanitizes_enabled_or_invalid_gate_initialization_without_network(
    overrides, tmp_path: Path
) -> None:
    result = _run_cli(overrides, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert _STABLE_STATUS.fullmatch(result.stderr)
    assert "reason=configuration_invalid" in result.stderr
    assert "Traceback" not in result.stderr
    assert "ValidationError" not in result.stderr
    assert "private-user" not in result.stderr
    assert "private-password" not in result.stderr
