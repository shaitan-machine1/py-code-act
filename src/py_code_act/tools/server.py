from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .namespace import ToolRegistry


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"tool returned a non-serializable {type(value).__name__}")


class ToolRpcServer:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        trace: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.registry = registry
        self.token = secrets.token_urlsafe(32)
        self._server: asyncio.Server | None = None
        self._trace = trace

    def rotate_token(self) -> str:
        """Invalidate the previous kernel generation's RPC credential."""

        self.token = secrets.token_urlsafe(32)
        return self.token

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("tool RPC server is not started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        response: dict[str, Any]
        request_id: Any = None
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("request must be an object")
            request_id = raw.get("id")
            supplied_token = raw.get("token")
            if not isinstance(supplied_token, str) or not secrets.compare_digest(
                supplied_token, self.token
            ):
                raise PermissionError("invalid tool RPC token")
            method = raw.get("method")
            args = raw.get("args", [])
            kwargs = raw.get("kwargs", {})
            if (
                not isinstance(method, str)
                or not isinstance(args, list)
                or not isinstance(kwargs, dict)
            ):
                raise ValueError("invalid tool RPC request fields")
            if self._trace:
                self._trace(
                    "tools.rpc_request",
                    {"id": request_id, "method": method, "args": args, "kwargs": kwargs},
                )
            result = self.registry.invoke(method, args, kwargs)
            response = {"id": request_id, "ok": True, "result": _jsonable(result)}
        except Exception as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"type": type(error).__name__, "message": str(error)},
            }
        if self._trace:
            self._trace("tools.rpc_response", response)
        try:
            writer.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
