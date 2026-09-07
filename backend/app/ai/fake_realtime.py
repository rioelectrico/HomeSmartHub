"""Deterministic, in-memory Realtime provider for contract tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

from app.ai.provider import ProviderStatus
from app.ai.realtime import RealtimeEvent, RealtimeSessionConfig

_CLOSED = object()


class FakeRealtimeSession:
    """A feedable session that records input and yields queued internal events."""

    def __init__(self, config: RealtimeSessionConfig) -> None:
        self.config = config
        self.received_audio: list[bytes] = []
        self.cancel_count = 0
        self._closed = False
        self._event_queue: asyncio.Queue[RealtimeEvent | object] = asyncio.Queue()
        self._events = self._event_stream()

    async def send_audio(self, pcm16: bytes) -> None:
        self._ensure_open()
        self.received_audio.append(pcm16)

    def events(self) -> AsyncIterator[RealtimeEvent]:
        return self._events

    async def cancel_response(self) -> None:
        self._ensure_open()
        self.cancel_count += 1

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._event_queue.put_nowait(_CLOSED)

    async def emit(self, event: RealtimeEvent) -> None:
        """Queue a contract event, allowing tests to model provider behavior."""
        self._ensure_open()
        self._event_queue.put_nowait(event)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("session is closed")

    async def _event_stream(self) -> AsyncIterator[RealtimeEvent]:
        while True:
            event = await self._event_queue.get()
            if event is _CLOSED:
                return
            yield cast(RealtimeEvent, event)


class FakeRealtimeProvider:
    """In-memory provider with controlled opening failure for deterministic tests."""

    def __init__(self, *, fail_open: bool = False) -> None:
        self.fail_open = fail_open
        self.sessions: list[FakeRealtimeSession] = []

    def diagnose(self) -> ProviderStatus:
        return ProviderStatus.UNAVAILABLE if self.fail_open else ProviderStatus.AVAILABLE

    async def open_session(self, config: RealtimeSessionConfig) -> FakeRealtimeSession:
        if self.fail_open:
            raise RuntimeError("fake provider configured to fail opening sessions")
        session = FakeRealtimeSession(config)
        self.sessions.append(session)
        return session
