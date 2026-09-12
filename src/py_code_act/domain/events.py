from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .messages import utc_now

EventType = Literal[
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_text_delta",
    "message_reasoning_delta",
    "exec_code_delta",
    "message_end",
    "exec_start",
    "exec_output",
    "exec_end",
    "kernel_start",
    "kernel_restart_requested",
    "kernel_restarted",
    "kernel_failed",
    "usage_update",
    "todo_changed",
]


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    type: EventType
    sequence: int
    occurred_at: str = field(default_factory=utc_now)
    session_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    message_id: str | None = None
    exec_id: str | None = None
    kernel_generation: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


class EventFactory:
    """Generate monotonically sequenced immutable runtime events."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._sequence = 0

    def create(self, event_type: EventType, **kwargs: Any) -> RuntimeEvent:
        self._sequence += 1
        return RuntimeEvent(
            type=event_type,
            sequence=self._sequence,
            session_id=self._session_id,
            **kwargs,
        )
