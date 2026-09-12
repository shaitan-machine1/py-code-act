from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .execution import ExecutionOutput, output_from_dict, output_to_dict
from .ids import new_id
from .usage import Usage

StopReason = Literal["stop", "length", "error", "aborted"]
ExecutionStatus = Literal["ok", "error", "interrupted", "timed_out", "kernel_lost"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ReasoningBlock:
    text: str
    provider_item: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ExecBlock:
    code: str
    id: str = field(default_factory=lambda: new_id("exec"))


type AssistantBlock = TextBlock | ReasoningBlock | ExecBlock


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str
    id: str = field(default_factory=lambda: new_id("msg"))
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    provider: str
    model: str
    content: tuple[AssistantBlock, ...]
    stop_reason: StopReason
    id: str = field(default_factory=lambda: new_id("msg"))
    usage: Usage = field(default_factory=Usage)
    error_message: str | None = None
    created_at: str = field(default_factory=utc_now)
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionMessage:
    exec_id: str
    kernel_generation: int
    status: ExecutionStatus
    outputs: tuple[ExecutionOutput, ...]
    started_at: str
    finished_at: str
    id: str = field(default_factory=lambda: new_id("msg"))


@dataclass(frozen=True, slots=True)
class ProtocolErrorMessage:
    error: str
    message: str
    assistant_message_id: str
    id: str = field(default_factory=lambda: new_id("msg"))
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class KernelNoticeMessage:
    message: str
    kernel_generation: int
    reason: str
    id: str = field(default_factory=lambda: new_id("msg"))
    created_at: str = field(default_factory=utc_now)


type Message = (
    UserMessage | AssistantMessage | ExecutionMessage | ProtocolErrorMessage | KernelNoticeMessage
)


def block_to_dict(block: AssistantBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"kind": "text", "text": block.text}
    if isinstance(block, ReasoningBlock):
        return {
            "kind": "reasoning",
            "text": block.text,
            "provider_item": dict(block.provider_item) if block.provider_item is not None else None,
        }
    return {"kind": "exec", "id": block.id, "code": block.code}


def block_from_dict(value: Mapping[str, Any]) -> AssistantBlock:
    kind = value.get("kind")
    if kind == "text":
        return TextBlock(str(value.get("text", "")))
    if kind == "reasoning":
        raw_item = value.get("provider_item")
        item = dict(raw_item) if isinstance(raw_item, Mapping) else None
        return ReasoningBlock(str(value.get("text", "")), item)
    if kind == "exec":
        return ExecBlock(code=str(value.get("code", "")), id=str(value.get("id") or new_id("exec")))
    raise ValueError(f"unknown assistant block kind: {kind!r}")


def message_to_dict(message: Message) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        return {
            "kind": "user",
            "id": message.id,
            "text": message.text,
            "created_at": message.created_at,
        }
    if isinstance(message, AssistantMessage):
        return {
            "kind": "assistant",
            "id": message.id,
            "provider": message.provider,
            "model": message.model,
            "content": [block_to_dict(block) for block in message.content],
            "usage": message.usage.to_dict(),
            "stop_reason": message.stop_reason,
            "error_message": message.error_message,
            "created_at": message.created_at,
            "provider_metadata": dict(message.provider_metadata),
        }
    if isinstance(message, ExecutionMessage):
        return {
            "kind": "execution",
            "id": message.id,
            "exec_id": message.exec_id,
            "kernel_generation": message.kernel_generation,
            "status": message.status,
            "outputs": [output_to_dict(output) for output in message.outputs],
            "started_at": message.started_at,
            "finished_at": message.finished_at,
        }
    if isinstance(message, ProtocolErrorMessage):
        return {
            "kind": "protocol_error",
            "id": message.id,
            "error": message.error,
            "message": message.message,
            "assistant_message_id": message.assistant_message_id,
            "created_at": message.created_at,
        }
    return {
        "kind": "kernel_notice",
        "id": message.id,
        "message": message.message,
        "kernel_generation": message.kernel_generation,
        "reason": message.reason,
        "created_at": message.created_at,
    }


def message_from_dict(value: Mapping[str, Any]) -> Message:
    kind = value.get("kind")
    message_id = str(value.get("id") or new_id("msg"))
    created_at = str(value.get("created_at") or utc_now())
    if kind == "user":
        return UserMessage(text=str(value.get("text", "")), id=message_id, created_at=created_at)
    if kind == "assistant":
        raw_content = value.get("content", ())
        if not isinstance(raw_content, (list, tuple)):
            raise ValueError("assistant content must be a list")
        stop_reason = str(value.get("stop_reason", "error"))
        if stop_reason not in ("stop", "length", "error", "aborted"):
            stop_reason = "error"
        raw_metadata = value.get("provider_metadata")
        return AssistantMessage(
            id=message_id,
            provider=str(value.get("provider", "unknown")),
            model=str(value.get("model", "unknown")),
            content=tuple(
                block_from_dict(item) for item in raw_content if isinstance(item, Mapping)
            ),
            usage=Usage.from_dict(
                value.get("usage") if isinstance(value.get("usage"), Mapping) else None
            ),
            stop_reason=stop_reason,
            error_message=(
                str(value["error_message"]) if value.get("error_message") is not None else None
            ),
            created_at=created_at,
            provider_metadata=dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {},
        )
    if kind == "execution":
        raw_outputs = value.get("outputs", ())
        if not isinstance(raw_outputs, (list, tuple)):
            raise ValueError("execution outputs must be a list")
        status = str(value.get("status", "error"))
        if status not in ("ok", "error", "interrupted", "timed_out", "kernel_lost"):
            status = "error"
        return ExecutionMessage(
            id=message_id,
            exec_id=str(value.get("exec_id", "")),
            kernel_generation=int(value.get("kernel_generation", 0)),
            status=status,
            outputs=tuple(
                output_from_dict(item) for item in raw_outputs if isinstance(item, Mapping)
            ),
            started_at=str(value.get("started_at", created_at)),
            finished_at=str(value.get("finished_at", created_at)),
        )
    if kind == "protocol_error":
        return ProtocolErrorMessage(
            id=message_id,
            error=str(value.get("error", "protocol_error")),
            message=str(value.get("message", "")),
            assistant_message_id=str(value.get("assistant_message_id", "")),
            created_at=created_at,
        )
    if kind == "kernel_notice":
        return KernelNoticeMessage(
            id=message_id,
            message=str(value.get("message", "")),
            kernel_generation=int(value.get("kernel_generation", 0)),
            reason=str(value.get("reason", "unknown")),
            created_at=created_at,
        )
    raise ValueError(f"unknown message kind: {kind!r}")
