from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from py_code_act.domain.execution import ExecutionOutput
from py_code_act.domain.messages import ExecutionStatus


@dataclass(frozen=True, slots=True)
class KernelExecutionResult:
    status: ExecutionStatus
    outputs: tuple[ExecutionOutput, ...]
    kernel_generation: int
    replaced_kernel: bool = False


class Kernel(Protocol):
    generation: int

    async def start(self) -> None: ...

    async def execute(
        self,
        code: str,
        *,
        timeout_seconds: float,
        on_output: Callable[[ExecutionOutput], Awaitable[None]] | None = None,
    ) -> KernelExecutionResult: ...

    async def interrupt(self) -> None: ...

    async def restart(self) -> None: ...

    async def shutdown(self) -> None: ...
