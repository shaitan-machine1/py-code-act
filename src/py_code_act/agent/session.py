from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from py_code_act.domain.events import EventFactory, EventType, RuntimeEvent
from py_code_act.domain.messages import Message
from py_code_act.storage.session_jsonl import SessionLedger

from .state import AgentState

type EventSink = Callable[[RuntimeEvent], Awaitable[None]]


class AgentSession:
    """Own state reduction, semantic persistence, and runtime event delivery."""

    def __init__(self, ledger: SessionLedger, state: AgentState) -> None:
        self.ledger = ledger
        self.state = state
        self.events = EventFactory(ledger.session_id)
        self._sinks: list[EventSink] = []
        self._delivery_tail: asyncio.Task[None] | None = None

    def subscribe(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    async def emit(self, event_type: EventType, **kwargs: object) -> RuntimeEvent:
        await self.wait_until_idle()
        event = self.events.create(event_type, **kwargs)
        for sink in tuple(self._sinks):
            await sink(event)
        return event

    def emit_background(self, event_type: EventType, **kwargs: object) -> None:
        event = self.events.create(event_type, **kwargs)
        previous = self._delivery_tail

        async def deliver() -> None:
            if previous is not None:
                await previous
            for sink in tuple(self._sinks):
                await sink(event)

        self._delivery_tail = asyncio.create_task(deliver())

    def append_message(self, message: Message) -> None:
        self.ledger.append_message(message)
        self.state.messages.append(message)

    async def wait_until_idle(self) -> None:
        tail = self._delivery_tail
        if tail is not None:
            await tail
            if self._delivery_tail is tail:
                self._delivery_tail = None
