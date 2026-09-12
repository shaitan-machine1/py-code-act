from __future__ import annotations

from typing import Any

from py_code_act.domain.messages import (
    AssistantMessage,
    ExecBlock,
    ExecutionMessage,
    KernelNoticeMessage,
    ProtocolErrorMessage,
    ReasoningBlock,
    TextBlock,
    UserMessage,
)
from py_code_act.protocol.exec_result import (
    format_execution_result,
    format_kernel_notice,
    format_protocol_error,
)

from .base import ModelRequest


def serialize_assistant_text(message: AssistantMessage) -> str:
    pieces: list[str] = []
    previous_was_exec = False
    for block in message.content:
        if isinstance(block, TextBlock):
            if previous_was_exec and block.text and not block.text.startswith(("\n", "\r")):
                pieces.append("\n")
            pieces.append(block.text)
            previous_was_exec = False
        elif isinstance(block, ExecBlock):
            code = block.code
            if code and not code.endswith(("\n", "\r")):
                code += "\n"
            pieces.append(f"<exec>\n{code}</exec>")
            previous_was_exec = True
    return "".join(pieces)


def convert_messages(messages: tuple[object, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, UserMessage):
            result.append(
                {"role": "user", "content": [{"type": "input_text", "text": message.text}]}
            )
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ReasoningBlock) and block.provider_item is not None:
                    result.append(dict(block.provider_item))
            text = serialize_assistant_text(message)
            if text:
                result.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
        elif isinstance(message, ExecutionMessage):
            result.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": format_execution_result(message)}],
                }
            )
        elif isinstance(message, ProtocolErrorMessage):
            result.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": format_protocol_error(message)}],
                }
            )
        elif isinstance(message, KernelNoticeMessage):
            result.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": format_kernel_notice(message)}],
                }
            )
        else:
            raise TypeError(f"unsupported transcript message: {type(message).__name__}")
    return result


def build_public_request(request: ModelRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "stream": True,
        "instructions": request.system_prompt,
        "input": convert_messages(request.messages),
        "include": ["reasoning.encrypted_content"],
    }
    if request.reasoning_level != "none":
        body["reasoning"] = {"effort": request.reasoning_level, "summary": "auto"}
    return body


def build_codex_request(request: ModelRequest) -> dict[str, Any]:
    body = build_public_request(request)
    body.update(
        {
            "store": False,
            "text": {"verbosity": "low"},
            "prompt_cache_key": request.session_id,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
    )
    return body
