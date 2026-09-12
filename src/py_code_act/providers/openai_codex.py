from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from py_code_act.auth.storage import OAuthCredential

from .base import ModelRequest, ProviderEvent
from .openai import TraceCallback, normalize_openai_event
from .openai_conversion import build_codex_request

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


class OpenAICodexProvider:
    name = "openai-codex"

    def __init__(
        self,
        credential: OAuthCredential,
        *,
        timeout_seconds: float = 300.0,
        client: httpx.AsyncClient | None = None,
        trace: TraceCallback | None = None,
    ) -> None:
        self._credential = credential
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._trace = trace

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def stream_response(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        body = build_codex_request(request)
        headers = {
            "Authorization": f"Bearer {self._credential.access}",
            "chatgpt-account-id": self._credential.account_id,
            "originator": "pi",
            "User-Agent": "pi",
            "OpenAI-Beta": "responses=experimental",
            "accept": "text/event-stream",
            "content-type": "application/json",
            "session-id": request.session_id,
            "x-client-request-id": request.request_id,
        }
        if self._trace:
            self._trace("openai.request", {"url": CODEX_RESPONSES_URL, "body": body})
        yield ProviderEvent("start")
        terminal = False
        try:
            async with self._client.stream(
                "POST", CODEX_RESPONSES_URL, headers=headers, json=body
            ) as response:
                if not response.is_success:
                    detail = (await response.aread()).decode("utf-8", errors="replace")
                    yield ProviderEvent(
                        "error",
                        stop_reason="error",
                        error_message=(
                            f"OpenAI Codex request failed ({response.status_code}): "
                            f"{detail or response.reason_phrase}"
                        ),
                    )
                    return
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        for event in self._events_from_sse_data(data_lines):
                            if event.type in ("done", "error"):
                                terminal = True
                            yield event
                        data_lines.clear()
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if data_lines:
                    for event in self._events_from_sse_data(data_lines):
                        if event.type in ("done", "error"):
                            terminal = True
                        yield event
                if not terminal:
                    yield ProviderEvent(
                        "error",
                        stop_reason="error",
                        error_message="Codex SSE stream ended without a terminal response event",
                    )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as error:
            yield ProviderEvent("error", stop_reason="error", error_message=str(error))

    def _events_from_sse_data(self, lines: list[str]) -> tuple[ProviderEvent, ...]:
        if not lines:
            return ()
        data = "\n".join(lines)
        if data == "[DONE]":
            return ()
        try:
            raw: Any = json.loads(data)
        except json.JSONDecodeError:
            return (
                ProviderEvent(
                    "error",
                    stop_reason="error",
                    error_message="invalid JSON in Codex SSE event",
                ),
            )
        if self._trace:
            self._trace("openai.raw_event", raw if isinstance(raw, dict) else {"value": raw})
        return normalize_openai_event(raw)
