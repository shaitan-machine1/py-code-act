from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from openai import AsyncOpenAI

from py_code_act.domain.usage import Usage

from .base import ModelRequest, ProviderEvent
from .openai_conversion import build_public_request

type TraceCallback = Callable[[str, Mapping[str, Any]], None]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _usage(response: Mapping[str, Any]) -> Usage:
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        return Usage()
    output_details = raw.get("output_tokens_details")
    input_details = raw.get("input_tokens_details")
    return Usage(
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        reasoning_tokens=(
            int(output_details.get("reasoning_tokens") or 0)
            if isinstance(output_details, Mapping)
            else 0
        ),
        cached_input_tokens=(
            int(input_details.get("cached_tokens") or 0)
            if isinstance(input_details, Mapping)
            else 0
        ),
    )


def normalize_openai_event(raw_value: Any) -> tuple[ProviderEvent, ...]:
    raw = _as_dict(raw_value)
    event_type = str(raw.get("type", ""))
    if event_type == "response.output_text.delta":
        return (ProviderEvent("text_delta", delta=str(raw.get("delta", ""))),)
    if event_type in (
        "response.reasoning_summary_text.delta",
        "response.reasoning_text.delta",
    ):
        return (ProviderEvent("reasoning_delta", delta=str(raw.get("delta", ""))),)
    if event_type == "response.output_item.done":
        item = raw.get("item")
        if isinstance(item, Mapping) and item.get("type") == "reasoning":
            return (ProviderEvent("reasoning_item", reasoning_item=dict(item)),)
        return ()
    if event_type in ("response.completed", "response.incomplete"):
        response = raw.get("response")
        response = response if isinstance(response, Mapping) else {}
        usage = _usage(response)
        reason = "stop"
        if event_type == "response.incomplete" or response.get("status") == "incomplete":
            reason = "length"
        response_id = response.get("id")
        return (
            ProviderEvent("usage", usage=usage),
            ProviderEvent(
                "done",
                stop_reason=reason,
                usage=usage,
                response_id=str(response_id) if response_id else None,
            ),
        )
    if event_type in ("response.failed", "response.cancelled", "error"):
        response = raw.get("response")
        response = response if isinstance(response, Mapping) else {}
        raw_error = raw.get("error") or response.get("error")
        if isinstance(raw_error, Mapping):
            message = str(raw_error.get("message") or raw_error.get("code") or "provider error")
        else:
            message = str(raw_error or "provider error")
        reason = "aborted" if event_type == "response.cancelled" else "error"
        return (ProviderEvent("error", stop_reason=reason, error_message=message),)
    return ()


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout_seconds: float = 300.0,
        client: Any | None = None,
        trace: TraceCallback | None = None,
    ) -> None:
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key and client is None:
            raise ValueError("OPENAI_API_KEY is not set")
        self._client: Any = client or AsyncOpenAI(api_key=key, timeout=timeout_seconds)
        self._owns_client = client is None
        self._trace = trace

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def stream_response(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        body = build_public_request(request)
        if self._trace:
            self._trace("openai.request", body)
        yield ProviderEvent("start")
        terminal = False
        try:
            stream = await self._client.responses.create(**body)
            async for raw in stream:
                raw_dict = _as_dict(raw)
                if self._trace:
                    self._trace("openai.raw_event", raw_dict)
                for event in normalize_openai_event(raw_dict):
                    if event.type in ("done", "error"):
                        terminal = True
                    yield event
            if not terminal:
                yield ProviderEvent(
                    "error",
                    stop_reason="error",
                    error_message="OpenAI stream ended without a terminal response event",
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # SDK errors are normalized at this boundary.
            yield ProviderEvent("error", stop_reason="error", error_message=str(error))
