from __future__ import annotations

from collections.abc import Callable


class KernelService:
    def __init__(self, request_restart: Callable[[], None]) -> None:
        self._request_restart = request_restart

    def restart(self) -> dict[str, bool]:
        """Schedule kernel replacement after the current cell finishes."""

        self._request_restart()
        return {"scheduled": True}
