from __future__ import annotations

import asyncio
from typing import Any

from rich.console import Console

from py_code_act.domain.events import RuntimeEvent
from py_code_act.domain.execution import DisplayOutput, ErrorOutput, StreamOutput, ValueOutput
from py_code_act.domain.messages import KernelNoticeMessage, ProtocolErrorMessage
from py_code_act.domain.usage import Usage


class TerminalRenderer:
    def __init__(self, *, display_reasoning: bool = False, console: Console | None = None) -> None:
        self.console = console or Console()
        self.display_reasoning = display_reasoning
        self._assistant_started = False
        self._code_started = False

    async def handle(self, event: RuntimeEvent) -> None:
        payload: Any = event.payload
        if event.type == "message_start" and payload.get("role") == "assistant":
            self.console.print("Assistant: ", style="bold cyan", end="")
            self._assistant_started = True
            self._code_started = False
        elif event.type == "message_text_delta":
            if self._code_started:
                self.console.print("\n", end="")
                self._code_started = False
            self.console.print(str(payload.get("delta", "")), markup=False, end="")
        elif event.type == "message_reasoning_delta" and self.display_reasoning:
            self.console.print(str(payload.get("delta", "")), style="dim", markup=False, end="")
        elif event.type == "exec_code_delta":
            if not self._code_started:
                self.console.print("\nPython:\n", style="bold magenta", end="")
                self._code_started = True
            self.console.print(str(payload.get("delta", "")), markup=False, end="")
        elif event.type == "message_end" and self._assistant_started:
            self.console.print()
            self._assistant_started = False
            self._code_started = False
        elif event.type == "message_end" and payload.get("role") == "kernel_notice":
            message = payload.get("message")
            if isinstance(message, KernelNoticeMessage):
                self.console.print(message.message, style="yellow")
        elif event.type == "message_end" and payload.get("role") == "protocol_error":
            message = payload.get("message")
            if isinstance(message, ProtocolErrorMessage):
                self.console.print(f"Protocol repair: {message.message}", style="yellow")
        elif event.type == "exec_output":
            self._render_output(payload.get("output"))
        elif event.type == "kernel_restart_requested":
            self.console.print(
                "Kernel restart requested; finishing the current cell.", style="yellow"
            )
        elif event.type == "kernel_restarted":
            self.console.print(
                f"Kernel restarted (generation {event.kernel_generation}).", style="yellow"
            )
        elif event.type == "kernel_failed":
            self.console.print("Kernel failed; in-memory Python state was lost.", style="bold red")
        elif event.type == "usage_update" and isinstance(payload.get("usage"), Usage):
            usage = payload["usage"]
            self.console.print(
                f"[tokens: {usage.input_tokens} in, {usage.output_tokens} out]", style="dim"
            )

    def _render_output(self, output: object) -> None:
        if isinstance(output, StreamOutput):
            style = "red" if output.channel == "stderr" else None
            self.console.print(output.text, style=style, markup=False, end="")
        elif isinstance(output, (ValueOutput, DisplayOutput)):
            self.console.print(output.data, markup=False)
        elif isinstance(output, ErrorOutput):
            if output.traceback:
                self.console.print("\n".join(output.traceback), style="red", markup=False)
            else:
                self.console.print(f"{output.name}: {output.message}", style="red", markup=False)

    def notify(self, message: str) -> None:
        self.console.print(message, markup=False)

    async def prompt(self, prompt: str = "You: ") -> str:
        return await asyncio.to_thread(self.console.input, prompt)

    def security_warning(self) -> None:
        self.console.print(
            "Warning: model-generated Python runs with your user permissions and is not sandboxed.",
            style="bold yellow",
        )
