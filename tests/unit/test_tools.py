import asyncio
import json

import pytest

from py_code_act.tools.namespace import ToolNamespace, ToolRegistry
from py_code_act.tools.server import ToolRpcServer
from py_code_act.tools.todo import TodoService


def test_todo_validation_and_order() -> None:
    changes: list[tuple[str, object]] = []
    todo = TodoService(on_change=lambda operation, task: changes.append((operation, task)))
    assert todo.create("first") == {"name": "first", "status": "pending"}
    todo.create("second", "in_progress")
    assert [task["name"] for task in todo.list()] == ["first", "second"]
    assert todo.update("first", "done")["status"] == "done"
    with pytest.raises(ValueError, match="already"):
        todo.create("first")
    with pytest.raises(KeyError, match="unknown"):
        todo.get("missing")
    with pytest.raises(ValueError, match="status"):
        todo.update("first", "invalid")
    todo.clear()
    assert todo.list() == []
    assert [operation for operation, _ in changes] == ["create", "create", "update", "clear"]


def test_registry_prompt_and_allowlist() -> None:
    registry = ToolRegistry(
        (ToolNamespace("todo", TodoService(), "`tools.todo` keeps tasks.", ("list",)),)
    )
    assert registry.prompt_contribution() == "`tools.todo` keeps tasks."
    assert registry.invoke("todo.list", [], {}) == []
    with pytest.raises(KeyError):
        registry.invoke("todo.clear", [], {})


@pytest.mark.asyncio
async def test_rpc_server_authenticates_requests() -> None:
    registry = ToolRegistry((ToolNamespace("todo", TodoService(), "todo", ("create", "list")),))
    server = ToolRpcServer(registry)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(
            json.dumps(
                {
                    "id": "request",
                    "token": server.token,
                    "method": "todo.create",
                    "args": ["work"],
                    "kwargs": {},
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
        assert response["ok"] is True
        assert response["result"] == {"name": "work", "status": "pending"}

        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(b'{"id":"bad","token":"wrong","method":"todo.list","args":[],"kwargs":{}}\n')
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
        assert response["ok"] is False
        assert response["error"]["type"] == "PermissionError"
    finally:
        await server.close()
