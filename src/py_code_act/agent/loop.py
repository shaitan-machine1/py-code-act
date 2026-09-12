from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from py_code_act.config import RunConfig
from py_code_act.domain.execution import ExecutionOutput
from py_code_act.domain.ids import new_id
from py_code_act.domain.messages import (
    AssistantMessage,
    ExecBlock,
    ExecutionMessage,
    KernelNoticeMessage,
    Message,
    ProtocolErrorMessage,
    ReasoningBlock,
    StopReason,
    UserMessage,
    utc_now,
)
from py_code_act.domain.usage import Usage
from py_code_act.kernel.base import Kernel, KernelExecutionResult
from py_code_act.protocol.exec_parser import ExecParser
from py_code_act.providers.base import ModelProvider, ModelRequest

from .session import AgentSession


@dataclass(slots=True)
class RestartController:
    requested: bool = False
    on_request: Callable[[], None] | None = None

    def request(self) -> None:
        first_request = not self.requested
        self.requested = True
        if first_request and self.on_request is not None:
            self.on_request()

    def consume(self) -> bool:
        requested = self.requested
        self.requested = False
        return requested


class AgentLoop:
    def __init__(
        self,
        config: RunConfig,
        provider: ModelProvider,
        kernel: Kernel,
        session: AgentSession,
        restart_controller: RestartController,
    ) -> None:
        self.config = config
        self.provider = provider
        self.kernel = kernel
        self.session = session
        self.restart_controller = restart_controller
        self._active_run_id: str | None = None
        self._active_turn_id: str | None = None
        self.restart_controller.on_request = self._emit_restart_requested

    async def run(self, user_text: str) -> None:
        if self.session.state.is_running:
            raise RuntimeError("an agent run is already active")
        if self.config.run_timeout_seconds is None:
            await self._run(user_text)
            return
        try:
            async with asyncio.timeout(self.config.run_timeout_seconds):
                await self._run(user_text)
        except TimeoutError:
            self.session.state.last_error = "run deadline exceeded"

    async def _run(self, user_text: str) -> None:
        state = self.session.state
        state.is_running = True
        state.last_error = None
        run_id = new_id("run")
        turn_id = new_id("turn")
        await self.session.emit("agent_start", run_id=run_id)
        await self.session.emit("turn_start", run_id=run_id, turn_id=turn_id)
        user = UserMessage(user_text)
        self.session.append_message(user)
        await self.session.emit(
            "message_start",
            run_id=run_id,
            turn_id=turn_id,
            message_id=user.id,
            payload={"role": "user"},
        )
        await self.session.emit(
            "message_end",
            run_id=run_id,
            turn_id=turn_id,
            message_id=user.id,
            payload={"role": "user", "message": user},
        )

        executions = 0
        repairs = 0
        try:
            for turn_number in range(self.config.max_turns):
                if turn_number > 0:
                    turn_id = new_id("turn")
                    await self.session.emit("turn_start", run_id=run_id, turn_id=turn_id)
                assistant, parser_error, exec_block = await self._stream_assistant(run_id, turn_id)
                self.session.append_message(assistant)
                await self.session.emit(
                    "message_end",
                    run_id=run_id,
                    turn_id=turn_id,
                    message_id=assistant.id,
                    payload={"role": "assistant", "message": assistant},
                )

                if assistant.stop_reason in ("error", "aborted"):
                    state.last_error = assistant.error_message
                    await self.session.emit("turn_end", run_id=run_id, turn_id=turn_id)
                    break

                protocol_error: tuple[str, str] | None = None
                if assistant.stop_reason == "length" and (exec_block is not None or parser_error):
                    protocol_error = (
                        "truncated_execution",
                        "The response ended at its output limit, so its Python block was not "
                        "executed. Retry with one complete, shorter <exec> block.",
                    )
                elif parser_error:
                    protocol_error = ("malformed_execution", parser_error)

                if protocol_error is not None:
                    repairs += 1
                    error_message = ProtocolErrorMessage(
                        error=protocol_error[0],
                        message=protocol_error[1],
                        assistant_message_id=assistant.id,
                    )
                    await self._append_observation(error_message, run_id, turn_id, "protocol_error")
                    await self.session.emit("turn_end", run_id=run_id, turn_id=turn_id)
                    if repairs > self.config.max_protocol_repairs:
                        state.last_error = "protocol repair limit exceeded"
                        break
                    continue

                if assistant.stop_reason == "length" or exec_block is None:
                    if assistant.stop_reason == "length":
                        state.last_error = "model response reached its output limit"
                    await self.session.emit("turn_end", run_id=run_id, turn_id=turn_id)
                    break
                if executions >= self.config.max_executions:
                    state.last_error = "execution limit exceeded"
                    await self.session.emit("turn_end", run_id=run_id, turn_id=turn_id)
                    break

                executions += 1
                result = await self._execute(exec_block.id, exec_block.code, run_id, turn_id)
                restart_requested = self.restart_controller.consume()
                if result.replaced_kernel:
                    await self._record_kernel_replacement(run_id, turn_id, "forced_replacement")
                elif restart_requested:
                    await self.kernel.restart()
                    await self._record_kernel_replacement(run_id, turn_id, "requested")
                await self.session.emit("turn_end", run_id=run_id, turn_id=turn_id)
            else:
                state.last_error = "turn limit exceeded"
        finally:
            state.is_running = False
            state.streaming_message_id = None
            state.active_execution_id = None
            await self.session.wait_until_idle()
            await self.session.emit("agent_end", run_id=run_id, payload={"error": state.last_error})

    async def _stream_assistant(
        self, run_id: str, turn_id: str
    ) -> tuple[AssistantMessage, str | None, ExecBlock | None]:
        state = self.session.state
        message_id = new_id("msg")
        request_id = new_id("request")
        state.streaming_message_id = message_id
        await self.session.emit(
            "message_start",
            run_id=run_id,
            turn_id=turn_id,
            request_id=request_id,
            message_id=message_id,
            payload={"role": "assistant"},
        )
        parser = ExecParser()
        reasoning_text: list[str] = []
        reasoning_items: list[dict[str, object]] = []
        usage = Usage()
        stop_reason: StopReason = "error"
        error_message: str | None = None
        response_id: str | None = None
        terminal = False
        request = ModelRequest(
            model=self.config.model,
            system_prompt=state.system_prompt,
            messages=tuple(state.messages),
            reasoning_level=self.config.reasoning_level,
            session_id=self.session.ledger.session_id,
            request_id=request_id,
        )
        try:
            async for event in self.provider.stream_response(request):
                if event.type == "text_delta":
                    for delta in parser.feed(event.delta):
                        event_type = (
                            "message_text_delta" if delta.kind == "text" else "exec_code_delta"
                        )
                        await self.session.emit(
                            event_type,
                            run_id=run_id,
                            turn_id=turn_id,
                            request_id=request_id,
                            message_id=message_id,
                            payload={"delta": delta.delta},
                        )
                elif event.type == "reasoning_delta":
                    reasoning_text.append(event.delta)
                    await self.session.emit(
                        "message_reasoning_delta",
                        run_id=run_id,
                        turn_id=turn_id,
                        request_id=request_id,
                        message_id=message_id,
                        payload={"delta": event.delta},
                    )
                elif event.type == "reasoning_item" and event.reasoning_item is not None:
                    reasoning_items.append(dict(event.reasoning_item))
                elif event.type == "usage":
                    usage = event.usage
                    await self.session.emit(
                        "usage_update",
                        run_id=run_id,
                        turn_id=turn_id,
                        request_id=request_id,
                        message_id=message_id,
                        payload={"usage": usage},
                    )
                elif event.type in ("done", "error"):
                    terminal = True
                    stop_reason = event.stop_reason or "error"
                    error_message = event.error_message
                    response_id = event.response_id
                    if event.usage.total_tokens:
                        usage = event.usage
        except asyncio.CancelledError:
            terminal = True
            stop_reason = "aborted"
            error_message = "request cancelled"
        if not terminal:
            stop_reason = "error"
            error_message = "provider stream ended without an authoritative terminal event"

        parsed = parser.finalize()
        for delta in parsed.final_deltas:
            event_type = "message_text_delta" if delta.kind == "text" else "exec_code_delta"
            await self.session.emit(
                event_type,
                run_id=run_id,
                turn_id=turn_id,
                request_id=request_id,
                message_id=message_id,
                payload={"delta": delta.delta},
            )
        blocks = list(parsed.blocks)
        if reasoning_text or reasoning_items:
            if reasoning_items:
                reasoning_blocks = [
                    ReasoningBlock("".join(reasoning_text) if index == 0 else "", item)
                    for index, item in enumerate(reasoning_items)
                ]
            else:
                reasoning_blocks = [ReasoningBlock("".join(reasoning_text))]
            blocks = [*reasoning_blocks, *blocks]
        assistant = AssistantMessage(
            id=message_id,
            provider=self.provider.name,
            model=self.config.model,
            content=tuple(blocks),
            usage=usage,
            stop_reason=stop_reason,
            error_message=error_message,
            provider_metadata={"response_id": response_id} if response_id else {},
        )
        state.usage_totals = state.usage_totals + usage
        state.streaming_message_id = None
        return assistant, parsed.error, parsed.exec_block

    async def _execute(
        self, exec_id: str, code: str, run_id: str, turn_id: str
    ) -> KernelExecutionResult:
        state = self.session.state
        generation = self.kernel.generation
        state.active_execution_id = exec_id
        self._active_run_id = run_id
        self._active_turn_id = turn_id
        started_at = utc_now()
        await self.session.emit(
            "exec_start",
            run_id=run_id,
            turn_id=turn_id,
            exec_id=exec_id,
            kernel_generation=generation,
            payload={"code": code},
        )

        async def on_output(output: ExecutionOutput) -> None:
            await self.session.emit(
                "exec_output",
                run_id=run_id,
                turn_id=turn_id,
                exec_id=exec_id,
                kernel_generation=generation,
                payload={"output": output},
            )

        result = await self.kernel.execute(
            code,
            timeout_seconds=self.config.execution_timeout_seconds,
            on_output=on_output,
        )
        message = ExecutionMessage(
            exec_id=exec_id,
            kernel_generation=result.kernel_generation,
            status=result.status,
            outputs=result.outputs,
            started_at=started_at,
            finished_at=utc_now(),
        )
        await self.session.emit(
            "exec_end",
            run_id=run_id,
            turn_id=turn_id,
            message_id=message.id,
            exec_id=exec_id,
            kernel_generation=result.kernel_generation,
            payload={"message": message},
        )
        await self._append_observation(message, run_id, turn_id, "execution")
        state.active_execution_id = None
        self._active_run_id = None
        self._active_turn_id = None
        return result

    def _emit_restart_requested(self) -> None:
        self.session.emit_background(
            "kernel_restart_requested",
            run_id=self._active_run_id,
            turn_id=self._active_turn_id,
            kernel_generation=self.kernel.generation,
        )

    async def _append_observation(
        self, message: Message, run_id: str, turn_id: str, role: str
    ) -> None:
        self.session.append_message(message)
        message_id = message.id
        await self.session.emit(
            "message_start",
            run_id=run_id,
            turn_id=turn_id,
            message_id=message_id,
            payload={"role": role},
        )
        await self.session.emit(
            "message_end",
            run_id=run_id,
            turn_id=turn_id,
            message_id=message_id,
            payload={"role": role, "message": message},
        )

    async def _record_kernel_replacement(self, run_id: str, turn_id: str, reason: str) -> None:
        generation = self.kernel.generation
        self.session.state.kernel_generation = generation
        self.session.ledger.append(
            "kernel_restart", {"kernel_generation": generation, "reason": reason}
        )
        if reason == "forced_replacement":
            await self.session.emit(
                "kernel_failed",
                run_id=run_id,
                turn_id=turn_id,
                kernel_generation=generation,
            )
        await self.session.emit(
            "kernel_restarted",
            run_id=run_id,
            turn_id=turn_id,
            kernel_generation=generation,
            payload={"reason": reason},
        )
        notice = KernelNoticeMessage(
            message=(
                "The Python kernel was replaced. Ordinary in-memory variables are gone; "
                "filesystem and external side effects may remain."
            ),
            kernel_generation=generation,
            reason=reason,
        )
        await self._append_observation(notice, run_id, turn_id, "kernel_notice")
