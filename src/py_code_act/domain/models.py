from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Model:
    provider: str
    id: str
    context_window: int = 200_000
    maximum_output: int = 100_000
    reasoning: bool = True
