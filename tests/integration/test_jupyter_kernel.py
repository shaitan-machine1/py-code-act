from pathlib import Path

import pytest

from py_code_act.domain.execution import DisplayOutput, ErrorOutput, StreamOutput, ValueOutput
from py_code_act.kernel.jupyter import JupyterKernel
from py_code_act.tools.namespace import ToolNamespace, ToolRegistry
from py_code_act.tools.protocol import KernelService
from py_code_act.tools.server import ToolRpcServer
from py_code_act.tools.skills import SkillsService
from py_code_act.tools.todo import TodoService


@pytest.fixture
async def running_kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    restart_requests: list[bool] = []
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
                "`tools.skills` loads Agent Skills.",
                ("list", "load"),
            ),
            ToolNamespace(
                "kernel",
                KernelService(lambda: restart_requests.append(True)),
                "`tools.kernel.restart()` schedules a restart.",
                ("restart",),
            ),
        )
    )
    server = ToolRpcServer(registry)
    await server.start()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    kernel = JupyterKernel(
        str(tmp_path),
        rpc_host="127.0.0.1",
        rpc_port=server.port,
        rpc_token=server.token,
        rpc_timeout_seconds=5,
        rpc_token_rotator=server.rotate_token,
        interrupt_grace_seconds=3,
    )
    await kernel.start()
    try:
        yield kernel, restart_requests
    finally:
        await kernel.shutdown()
        await server.close()


@pytest.mark.asyncio
async def test_persistence_outputs_environment_and_tools(running_kernel) -> None:
    kernel, restart_requests = running_kernel
    result = await kernel.execute(
        """\
import logging, os, sys
x = 21
print("out")
print("err", file=sys.stderr)
logging.warning("logged")
print(os.getenv("OPENAI_API_KEY"))
tools.todo.create("inspect", status="in_progress")
x * 2
""",
        timeout_seconds=10,
    )

    assert result.status == "ok"
    assert any(isinstance(item, StreamOutput) and "out" in item.text for item in result.outputs)
    assert any(isinstance(item, StreamOutput) and "err" in item.text for item in result.outputs)
    assert any(isinstance(item, StreamOutput) and "logged" in item.text for item in result.outputs)
    assert any(isinstance(item, StreamOutput) and "None" in item.text for item in result.outputs)
    assert isinstance(result.outputs[-1], ValueOutput)
    assert result.outputs[-1].data == "42"

    second = await kernel.execute("x + 1", timeout_seconds=10)
    assert second.outputs[-1] == ValueOutput("text/plain", "22")
    displayed = await kernel.execute(
        'from IPython.display import display; display("shown")', timeout_seconds=10
    )
    assert displayed.outputs == (DisplayOutput("text/plain", "'shown'"),)
    no_value = await kernel.execute("None", timeout_seconds=10)
    assert no_value.outputs == ()

    deferred = await kernel.execute(
        'tools.kernel.restart(); print("still old", x)', timeout_seconds=10
    )
    assert deferred.status == "ok"
    assert restart_requests == [True]
    assert any(
        isinstance(item, StreamOutput) and "still old 21" in item.text for item in deferred.outputs
    )


@pytest.mark.asyncio
async def test_restart_changes_generation_and_clears_memory(running_kernel) -> None:
    kernel, _ = running_kernel
    await kernel.execute("remembered = 1", timeout_seconds=10)
    generation = kernel.generation
    await kernel.restart()
    assert kernel.generation == generation + 1

    result = await kernel.execute("remembered", timeout_seconds=10)
    assert result.status == "error"
    assert any(
        isinstance(item, ErrorOutput) and item.name == "NameError" for item in result.outputs
    )
    tools_result = await kernel.execute("type(tools).__name__", timeout_seconds=10)
    assert tools_result.outputs[-1] == ValueOutput("text/plain", "'_Tools'")


@pytest.mark.asyncio
async def test_timeout_interrupts_infinite_cell(running_kernel) -> None:
    kernel, _ = running_kernel
    result = await kernel.execute("while True: pass", timeout_seconds=0.2)
    assert result.status == "timed_out"


@pytest.mark.asyncio
async def test_unexpected_kernel_death_is_detected_and_replaced(running_kernel) -> None:
    kernel, _ = running_kernel
    generation = kernel.generation
    result = await kernel.execute("import os; os._exit(17)", timeout_seconds=5)
    assert result.status == "kernel_lost"
    assert result.replaced_kernel
    assert result.kernel_generation == generation
    assert kernel.generation == generation + 1
    ready = await kernel.execute("1 + 1", timeout_seconds=5)
    assert ready.outputs[-1] == ValueOutput("text/plain", "2")
