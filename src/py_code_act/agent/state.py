from __future__ import annotations

from dataclasses import dataclass, field

from py_code_act.domain.messages import Message
from py_code_act.domain.usage import Usage


@dataclass(slots=True)
class AgentState:
    system_prompt: str
    model: str
    reasoning_level: str
    messages: list[Message] = field(default_factory=list)
    is_running: bool = False
    streaming_message_id: str | None = None
    active_execution_id: str | None = None
    kernel_generation: int = 0
    last_error: str | None = None
    usage_totals: Usage = field(default_factory=Usage)
