from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from py_code_act.domain.execution import (
    DisplayOutput,
    ErrorOutput,
    ExecutionOutput,
    StreamOutput,
    ValueOutput,
)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def output_from_iopub(message: Mapping[str, Any]) -> ExecutionOutput | None:
    message_type = message.get("msg_type")
    content = message.get("content")
    if not isinstance(content, Mapping):
        return None
    if message_type == "stream":
        name = str(content.get("name", "stdout"))
        channel = "stderr" if name == "stderr" else "stdout"
        return StreamOutput(channel, str(content.get("text", "")))
    if message_type in ("execute_result", "display_data"):
        data = content.get("data")
        if not isinstance(data, Mapping) or "text/plain" not in data:
            return None
        text = str(data["text/plain"])
        if message_type == "execute_result":
            return ValueOutput("text/plain", text)
        return DisplayOutput("text/plain", text)
    if message_type == "error":
        traceback = content.get("traceback")
        lines = (
            tuple(ANSI_ESCAPE.sub("", str(line)) for line in traceback)
            if isinstance(traceback, (list, tuple))
            else ()
        )
        return ErrorOutput(
            name=str(content.get("ename", "Error")),
            message=str(content.get("evalue", "")),
            traceback=lines,
        )
    return None
