from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Literal

TaskStatus = Literal["pending", "in_progress", "done"]


@dataclass(frozen=True, slots=True)
class Task:
    name: str
    status: TaskStatus = "pending"


class TodoService:
    def __init__(
        self,
        initial: Mapping[str, str] | None = None,
        on_change: Callable[[str, Task | None], None] | None = None,
    ) -> None:
        self._tasks: dict[str, Task] = {}
        self._on_change = on_change
        for name, status in (initial or {}).items():
            self._tasks[name] = Task(name, self._validate_status(status))

    @staticmethod
    def _validate_status(status: str) -> TaskStatus:
        if status not in ("pending", "in_progress", "done"):
            raise ValueError("status must be pending, in_progress, or done")
        return status

    def create(self, name: str, status: str = "pending") -> dict[str, str]:
        """Create a uniquely named task with pending, in_progress, or done status."""

        if not isinstance(name, str) or not name.strip():
            raise ValueError("task name must be a non-empty string")
        if name in self._tasks:
            raise ValueError(f"task already exists: {name}")
        task = Task(name, self._validate_status(status))
        self._tasks[name] = task
        if self._on_change:
            self._on_change("create", task)
        return asdict(task)

    def get(self, name: str) -> dict[str, str]:
        """Return one task by its unique name."""

        try:
            return asdict(self._tasks[name])
        except KeyError:
            raise KeyError(f"unknown task: {name}") from None

    def update(self, name: str, status: str) -> dict[str, str]:
        """Update and return an existing task."""

        if name not in self._tasks:
            raise KeyError(f"unknown task: {name}")
        task = Task(name, self._validate_status(status))
        self._tasks[name] = task
        if self._on_change:
            self._on_change("update", task)
        return asdict(task)

    def list(self) -> list[dict[str, str]]:
        """Return tasks in stable insertion order."""

        return [asdict(task) for task in self._tasks.values()]

    def clear(self) -> None:
        """Remove every task."""

        self._tasks.clear()
        if self._on_change:
            self._on_change("clear", None)
