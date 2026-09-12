from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolNamespace:
    name: str
    service: object
    prompt: str
    methods: tuple[str, ...]


class ToolRegistry:
    def __init__(self, namespaces: Iterable[ToolNamespace]) -> None:
        self._namespaces = {namespace.name: namespace for namespace in namespaces}

    @property
    def namespaces(self) -> tuple[ToolNamespace, ...]:
        return tuple(self._namespaces.values())

    def prompt_contribution(self) -> str:
        return "\n".join(namespace.prompt for namespace in self._namespaces.values())

    def invoke(self, dotted_method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        try:
            namespace_name, method_name = dotted_method.split(".", 1)
            namespace = self._namespaces[namespace_name]
        except ValueError, KeyError:
            raise KeyError(f"unknown tool method: {dotted_method}") from None
        if method_name not in namespace.methods:
            raise KeyError(f"unknown tool method: {dotted_method}")
        method = getattr(namespace.service, method_name)
        inspect.signature(method).bind(*args, **kwargs)
        return method(*args, **kwargs)
