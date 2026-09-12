from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class StreamOutput:
    channel: Literal["stdout", "stderr"]
    text: str


@dataclass(frozen=True, slots=True)
class ValueOutput:
    mime_type: str
    data: str


@dataclass(frozen=True, slots=True)
class DisplayOutput:
    mime_type: str
    data: str


@dataclass(frozen=True, slots=True)
class ErrorOutput:
    name: str
    message: str
    traceback: tuple[str, ...]


type ExecutionOutput = StreamOutput | ValueOutput | DisplayOutput | ErrorOutput


def output_to_dict(output: ExecutionOutput) -> dict[str, Any]:
    if isinstance(output, StreamOutput):
        return {"kind": "stream", "channel": output.channel, "text": output.text}
    if isinstance(output, ValueOutput):
        return {"kind": "value", "mime_type": output.mime_type, "data": output.data}
    if isinstance(output, DisplayOutput):
        return {"kind": "display", "mime_type": output.mime_type, "data": output.data}
    return {
        "kind": "error",
        "name": output.name,
        "message": output.message,
        "traceback": list(output.traceback),
    }


def output_from_dict(value: Mapping[str, Any]) -> ExecutionOutput:
    kind = value.get("kind")
    if kind == "stream":
        channel = str(value.get("channel", "stdout"))
        if channel not in ("stdout", "stderr"):
            raise ValueError(f"invalid stream channel: {channel}")
        return StreamOutput(channel=channel, text=str(value.get("text", "")))
    if kind == "value":
        return ValueOutput(str(value.get("mime_type", "text/plain")), str(value.get("data", "")))
    if kind == "display":
        return DisplayOutput(str(value.get("mime_type", "text/plain")), str(value.get("data", "")))
    if kind == "error":
        traceback = value.get("traceback", ())
        if not isinstance(traceback, (list, tuple)):
            traceback = ()
        return ErrorOutput(
            name=str(value.get("name", "Error")),
            message=str(value.get("message", "")),
            traceback=tuple(str(line) for line in traceback),
        )
    raise ValueError(f"unknown execution output kind: {kind!r}")
