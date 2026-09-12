from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from py_code_act.agent.loop import AgentLoop, RestartController
from py_code_act.agent.session import AgentSession
from py_code_act.agent.state import AgentState
from py_code_act.config import RunConfig
from py_code_act.domain.events import RuntimeEvent
from py_code_act.domain.messages import AssistantMessage, ExecutionMessage
from py_code_act.kernel.jupyter import JupyterKernel
from py_code_act.providers.base import ModelRequest, ProviderEvent
from py_code_act.storage.session_jsonl import SessionLedger
from py_code_act.tools.namespace import ToolNamespace, ToolRegistry
from py_code_act.tools.protocol import KernelService
from py_code_act.tools.server import ToolRpcServer
from py_code_act.tools.skills import SkillsService
from py_code_act.tools.todo import TodoService


class ScriptedProvider:
    name = "fake-openai"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_response(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        yield ProviderEvent("start")
        if self.calls == 1:
            text = (
                "I will create the file.\n<exec>\n"
                "from pathlib import Path\n"
                "Path('made-by-agent.txt').write_text('complete')\n"
                "</exec>\nThe cell is ready."
            )
        else:
            assert any(isinstance(message, ExecutionMessage) for message in request.messages)
            text = "The file was created."
        for character in text:
            yield ProviderEvent("text_delta", delta=character)
        yield ProviderEvent("done", stop_reason="stop")


@pytest.mark.asyncio
async def test_scripted_provider_real_kernel_and_durable_session(tmp_path: Path) -> None:
    config = RunConfig(
        model="test-model",
        auth_mode="api_key",
        api_key="unused",
        cwd=tmp_path,
        session_path=tmp_path / "session.jsonl",
        execution_timeout_seconds=10,
    )
    ledger = SessionLedger(config.session_path)
    controller = RestartController()
    registry = ToolRegistry(
        (
            ToolNamespace(
                "todo",
                TodoService(),
                "`tools.todo` maintains a simple task list.",
                ("create", "get", "update", "list", "clear"),
            ),
            ToolNamespace(
                "skills",
                SkillsService(()),
                "`tools.skills` loads skills.",
                ("list", "load"),
            ),
            ToolNamespace(
                "kernel",
                KernelService(controller.request),
                "`tools.kernel.restart()` schedules a restart.",
                ("restart",),
            ),
        )
    )
    server = ToolRpcServer(registry)
    await server.start()
    kernel = JupyterKernel(
        str(tmp_path),
        rpc_host="127.0.0.1",
        rpc_port=server.port,
        rpc_token=server.token,
        rpc_timeout_seconds=5,
        rpc_token_rotator=server.rotate_token,
    )
    await kernel.start()
    provider = ScriptedProvider()
    session = AgentSession(
        ledger,
        AgentState("system", config.model, config.reasoning_level, ledger.messages()),
    )
    events: list[RuntimeEvent] = []

    async def capture(event: RuntimeEvent) -> None:
        events.append(event)

    session.subscribe(capture)
    try:
        await AgentLoop(config, provider, kernel, session, controller).run("create a file")
    finally:
        await kernel.shutdown()
        await server.close()

    assert (tmp_path / "made-by-agent.txt").read_text(encoding="utf-8") == "complete"
    assert provider.calls == 2
    assert any(isinstance(message, ExecutionMessage) for message in ledger.messages())
    assistants = [message for message in ledger.messages() if isinstance(message, AssistantMessage)]
    assert len(assistants) == 2
    assert events[0].type == "agent_start"
    assert events[-1].type == "agent_end"
