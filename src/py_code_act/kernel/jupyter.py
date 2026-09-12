from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from jupyter_client.manager import AsyncKernelManager

from py_code_act.domain.execution import ErrorOutput, ExecutionOutput

from .base import KernelExecutionResult
from .bootstrap import bootstrap_source
from .outputs import output_from_iopub


class KernelDiedError(RuntimeError):
    pass


CREDENTIAL_ENVIRONMENT_VARIABLES = {
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_ACCESS_TOKEN",
    "OPENAI_REFRESH_TOKEN",
    "CHATGPT_ACCOUNT_ID",
}


class JupyterKernel:
    def __init__(
        self,
        cwd: str,
        *,
        rpc_host: str,
        rpc_port: int,
        rpc_token: str,
        rpc_timeout_seconds: float,
        rpc_token_rotator: Callable[[], str] | None = None,
        initial_generation: int = 1,
        interrupt_grace_seconds: float = 5.0,
        trace: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.cwd = cwd
        self.generation = initial_generation
        self._rpc_host = rpc_host
        self._rpc_port = rpc_port
        self._rpc_token = rpc_token
        self._rpc_timeout_seconds = rpc_timeout_seconds
        self._rpc_token_rotator = rpc_token_rotator
        self._interrupt_grace_seconds = interrupt_grace_seconds
        self._trace = trace
        self._manager: AsyncKernelManager | None = None
        self._client: Any = None
        self._lock = asyncio.Lock()

    @staticmethod
    def sanitized_environment() -> dict[str, str]:
        return {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in CREDENTIAL_ENVIRONMENT_VARIABLES
        }

    async def start(self) -> None:
        if self._manager is not None:
            return
        manager = AsyncKernelManager(kernel_name="python3")
        await manager.start_kernel(
            cwd=self.cwd,
            env=self.sanitized_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        client = manager.client()
        client.start_channels()
        try:
            await client.wait_for_ready(timeout=30)
        except Exception:
            client.stop_channels()
            await manager.shutdown_kernel(now=True)
            raise
        self._manager = manager
        self._client = client
        await self._install_tools()

    async def _install_tools(self) -> None:
        source = bootstrap_source(
            self._rpc_host,
            self._rpc_port,
            self._rpc_token,
            self._rpc_timeout_seconds,
        )
        message_id = self._client.execute(source, silent=True, store_history=False)
        await self._wait_for_idle(message_id, timeout_seconds=30)

    async def _wait_for_idle(self, message_id: str, timeout_seconds: float) -> None:
        async with asyncio.timeout(timeout_seconds):
            while True:
                message = await self._client.get_iopub_msg()
                if self._parent_id(message) != message_id:
                    continue
                if message.get("msg_type") == "status":
                    content = message.get("content", {})
                    if isinstance(content, Mapping) and content.get("execution_state") == "idle":
                        return

    @staticmethod
    def _parent_id(message: Mapping[str, Any]) -> str | None:
        parent = message.get("parent_header")
        return (
            str(parent.get("msg_id"))
            if isinstance(parent, Mapping) and parent.get("msg_id")
            else None
        )

    async def execute(
        self,
        code: str,
        *,
        timeout_seconds: float,
        on_output: Callable[[ExecutionOutput], Awaitable[None]] | None = None,
    ) -> KernelExecutionResult:
        async with self._lock:
            if self._manager is None or self._client is None:
                raise RuntimeError("kernel is not started")
            generation = self.generation
            outputs: list[ExecutionOutput] = []
            message_id = self._client.execute(code, allow_stdin=False, stop_on_error=False)
            if self._trace:
                self._trace("kernel.execute", {"message_id": message_id, "code": code})
            try:
                async with asyncio.timeout(timeout_seconds):
                    await self._collect_iopub(message_id, outputs, on_output)
                    reply = await self._matching_shell_reply(message_id)
                status = "error" if reply.get("content", {}).get("status") == "error" else "ok"
                if any(isinstance(output, ErrorOutput) for output in outputs):
                    status = "error"
                return KernelExecutionResult(status, tuple(outputs), generation)
            except TimeoutError:
                replaced = not await self._interrupt_and_wait(message_id)
                return KernelExecutionResult("timed_out", tuple(outputs), generation, replaced)
            except asyncio.CancelledError:
                replaced = not await asyncio.shield(self._interrupt_and_wait(message_id))
                return KernelExecutionResult("interrupted", tuple(outputs), generation, replaced)
            except Exception as error:
                alive = await self._manager.is_alive()
                if self._trace:
                    self._trace("kernel.failure", {"error": str(error), "alive": alive})
                if not alive:
                    await self._replace_kernel()
                    return KernelExecutionResult("kernel_lost", tuple(outputs), generation, True)
                raise

    async def _collect_iopub(
        self,
        message_id: str,
        outputs: list[ExecutionOutput],
        on_output: Callable[[ExecutionOutput], Awaitable[None]] | None,
    ) -> None:
        while True:
            try:
                async with asyncio.timeout(0.5):
                    message = await self._client.get_iopub_msg()
            except TimeoutError:
                if self._manager is not None and not await self._manager.is_alive():
                    raise KernelDiedError("kernel process exited during execution") from None
                continue
            if self._parent_id(message) != message_id:
                continue
            if self._trace:
                self._trace("kernel.iopub_message", message)
            output = output_from_iopub(message)
            if output is not None:
                outputs.append(output)
                if on_output is not None:
                    await on_output(output)
            if message.get("msg_type") == "status":
                content = message.get("content", {})
                if isinstance(content, Mapping) and content.get("execution_state") == "idle":
                    return

    async def _matching_shell_reply(self, message_id: str) -> Mapping[str, Any]:
        while True:
            reply = await self._client.get_shell_msg()
            if self._parent_id(reply) == message_id:
                if self._trace:
                    self._trace("kernel.shell_message", reply)
                return reply

    async def _interrupt_and_wait(self, message_id: str) -> bool:
        if self._manager is None:
            return False
        await self._manager.interrupt_kernel()
        try:
            await self._wait_for_idle(message_id, self._interrupt_grace_seconds)
            return True
        except TimeoutError:
            await self._replace_kernel()
            return False

    async def interrupt(self) -> None:
        if self._manager is not None:
            await self._manager.interrupt_kernel()

    async def restart(self) -> None:
        async with self._lock:
            await self._replace_kernel()

    async def _replace_kernel(self) -> None:
        if self._manager is None or self._client is None:
            raise RuntimeError("kernel is not started")
        self._client.stop_channels()
        await self._manager.restart_kernel(now=True)
        self._client = self._manager.client()
        self._client.start_channels()
        await self._client.wait_for_ready(timeout=30)
        self.generation += 1
        if self._rpc_token_rotator is not None:
            self._rpc_token = self._rpc_token_rotator()
        await self._install_tools()

    async def shutdown(self) -> None:
        async with self._lock:
            if self._manager is None:
                return
            if self._client is not None:
                self._client.stop_channels()
            try:
                await self._manager.shutdown_kernel(now=True)
            finally:
                self._manager = None
                self._client = None
