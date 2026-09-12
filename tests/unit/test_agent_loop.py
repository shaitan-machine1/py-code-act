from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest

from py_code_act.agent.loop import AgentLoop, RestartController
from py_code_act.agent.session import AgentSession
from py_code_act.agent.state import AgentState
from py_code_act.config import RunConfig
from py_code_act.domain.events import RuntimeEvent
from py_code_act.domain.execution import ExecutionOutput, StreamOutput, ValueOutput
from py_code_act.domain.messages import ExecutionMessage, ProtocolErrorMessage
from py_code_act.kernel.base import KernelExecutionResult
from py_code_act.providers.base import ModelRequest, ProviderEvent
from py_code_act.storage.session_jsonl import SessionLedger


class FakeProvider:
    name = "fake-openai"

    def __init__(self, responses: list[list[ProviderEvent]]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []
        self.finished = 0

    async def stream_response(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        response = self.responses[len(self.requests) - 1]
        for event in response:
            yield event
        self.finished += 1


class FakeKernel:
    def __init__(self, provider: FakeProvider) -> None:
        self.generation = 1
        self.provider = provider
        self.executed: list[str] = []
        self.restarts = 0
        self.request_restart: Callable[[], None] | None = None

    async def start(self) -> None:
        pass

    async def execute(
        self,
        code: str,
        *,
        timeout_seconds: float,
        on_output: Callable[[ExecutionOutput], Awaitable[None]] | None = None,
    ) -> KernelExecutionResult:
        assert self.provider.finished == len(self.provider.requests), "execution began before drain"
        self.executed.append(code)
        if self.request_restart is not None:
            self.request_restart()
        output = StreamOutput("stdout", "ran\n")
        if on_output:
            await on_output(output)
        return KernelExecutionResult(
            "ok", (output, ValueOutput("text/plain", "2")), self.generation
        )

    async def interrupt(self) -> None:
        pass

    async def restart(self) -> None:
        self.restarts += 1
        self.generation += 1

    async def shutdown(self) -> None:
        pass


def _events(text: str, reason: str = "stop") -> list[ProviderEvent]:
    return [
        ProviderEvent("start"),
        ProviderEvent("text_delta", delta=text[:3]),
        ProviderEvent("text_delta", delta=text[3:]),
        ProviderEvent("done", stop_reason=reason),  # type: ignore[arg-type]
    ]


def _agent(
    tmp_path: Path, responses: list[list[ProviderEvent]]
) -> tuple[
    AgentLoop, FakeProvider, FakeKernel, AgentSession, list[RuntimeEvent], RestartController
]:
    config = RunConfig(
        model="test-model",
        auth_mode="api_key",
        api_key="test",
        cwd=tmp_path,
        session_path=tmp_path / "session.jsonl",
        max_turns=5,
        max_executions=3,
        max_protocol_repairs=1,
    )
    ledger = SessionLedger(config.session_path)
    state = AgentState("system", config.model, config.reasoning_level, ledger.messages())
    session = AgentSession(ledger, state)
    captured: list[RuntimeEvent] = []

    async def capture(event: RuntimeEvent) -> None:
        captured.append(event)

    session.subscribe(capture)
    provider = FakeProvider(responses)
    kernel = FakeKernel(provider)
    controller = RestartController()
    return (
        AgentLoop(config, provider, kernel, session, controller),
        provider,
        kernel,
        session,
        captured,
        controller,
    )


@pytest.mark.asyncio
async def test_execution_waits_for_terminal_then_continues(tmp_path: Path) -> None:
    agent, provider, kernel, session, events, _ = _agent(
        tmp_path,
        [
            _events("before\n<exec>\n1 + 1\n</exec>\nafter"),
            _events("finished"),
        ],
    )

    await agent.run("do it")

    assert kernel.executed == ["1 + 1\n"]
    assert len(provider.requests) == 2
    assert any(isinstance(message, ExecutionMessage) for message in session.state.messages)
    types = [event.type for event in events]
    assert types.index("exec_start") > types.index("message_end")
    assert "after" in "".join(
        str(event.payload.get("delta", ""))
        for event in events
        if event.type == "message_text_delta"
    )


@pytest.mark.asyncio
async def test_length_response_with_complete_block_is_never_executed(tmp_path: Path) -> None:
    agent, provider, kernel, session, events, _ = _agent(
        tmp_path,
        [_events("<exec>\ndanger()\n</exec>", "length"), _events("safe final")],
    )

    await agent.run("do it")

    assert kernel.executed == []
    assert len(provider.requests) == 2
    assert any(isinstance(message, ProtocolErrorMessage) for message in session.state.messages)
    assert "exec_start" not in [event.type for event in events]


@pytest.mark.asyncio
async def test_malformed_block_repairs_without_fake_execution(tmp_path: Path) -> None:
    agent, _, kernel, session, events, _ = _agent(
        tmp_path,
        [_events("<exec>\nunfinished"), _events("repaired without code")],
    )
    await agent.run("do it")
    assert kernel.executed == []
    assert any(isinstance(message, ProtocolErrorMessage) for message in session.state.messages)
    assert "exec_end" not in [event.type for event in events]


@pytest.mark.asyncio
async def test_requested_restart_occurs_after_old_generation_execution(tmp_path: Path) -> None:
    agent, _, kernel, session, events, controller = _agent(
        tmp_path,
        [_events("<exec>\n1\n</exec>"), _events("done")],
    )
    kernel.request_restart = controller.request
    await agent.run("restart")

    execution = next(
        message for message in session.state.messages if isinstance(message, ExecutionMessage)
    )
    assert execution.kernel_generation == 1
    assert kernel.generation == 2
    assert kernel.restarts == 1
    assert "kernel_restarted" in [event.type for event in events]
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)
    requested_index = next(
        index for index, event in enumerate(events) if event.type == "kernel_restart_requested"
    )
    exec_end_index = next(index for index, event in enumerate(events) if event.type == "exec_end")
    assert requested_index < exec_end_index
