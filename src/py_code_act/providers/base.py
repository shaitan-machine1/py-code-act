from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from py_code_act.domain.messages import Message, StopReason
from py_code_act.domain.usage import Usage

ProviderEventType = Literal[
    "start",
    "text_delta",
    "reasoning_delta",
    "reasoning_item",
    "usage",
    "done",
    "error",
]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    model: str
    system_prompt: str
    messages: tuple[Message, ...]
    reasoning_level: str
    session_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    type: ProviderEventType
    delta: str = ""
    reasoning_item: Mapping[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason | None = None
    error_message: str | None = None
    response_id: str | None = None


class ModelProvider(Protocol):
    name: str

    def stream_response(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]: ...
