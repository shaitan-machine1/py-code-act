import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from py_code_act.auth.openai_codex import (
    CLIENT_ID,
    DEVICE_REDIRECT_URI,
    DEVICE_TOKEN_URL,
    DEVICE_USER_CODE_URL,
    REDIRECT_URI,
    TOKEN_URL,
    CodexOAuth,
    OAuthError,
    create_authorization_flow,
    parse_callback_url,
)
from py_code_act.auth.storage import CredentialStore, OAuthCredential
from py_code_act.domain.messages import (
    AssistantMessage,
    ExecBlock,
    ReasoningBlock,
    TextBlock,
    UserMessage,
)
from py_code_act.providers.base import ModelRequest
from py_code_act.providers.openai import OpenAIProvider, normalize_openai_event
from py_code_act.providers.openai_codex import OpenAICodexProvider
from py_code_act.providers.openai_conversion import (
    build_codex_request,
    convert_messages,
    serialize_assistant_text,
)


def _jwt(account_id: str) -> str:
    payload = json.dumps(
        {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"header.{encoded}.signature"


def _request(messages: tuple[object, ...] = ()) -> ModelRequest:
    return ModelRequest(
        model="test-model",
        system_prompt="system",
        messages=messages,  # type: ignore[arg-type]
        reasoning_level="medium",
        session_id="session_test",
        request_id="request_test",
    )


def test_authorization_flow_matches_pi_and_validates_state() -> None:
    flow = create_authorization_flow()
    parameters = parse_qs(urlparse(flow.url).query)
    assert parameters["client_id"] == [CLIENT_ID]
    assert parameters["redirect_uri"] == [REDIRECT_URI]
    assert parameters["originator"] == ["pi"]
    assert (
        parse_callback_url(
            f"http://localhost:1455/auth/callback?code=ok&state={flow.state}", flow.state
        )
        == "ok"
    )
    with pytest.raises(OAuthError, match="state"):
        parse_callback_url("http://localhost:1455/auth/callback?code=ok&state=wrong", flow.state)
    with pytest.raises(OAuthError, match="full callback"):
        parse_callback_url("plain-code", flow.state)


@pytest.mark.asyncio
async def test_browser_login_exchanges_and_stores_credentials(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"access_token": _jwt("account"), "refresh_token": "refresh", "expires_in": 3600},
        )

    notices: list[str] = []

    async def prompt(_: str) -> str:
        state = parse_qs(urlparse(notices[0].splitlines()[-1]).query)["state"][0]
        return f"http://localhost:1455/auth/callback?code=code&state={state}"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = CredentialStore(tmp_path / "oauth.json")
        credential = await CodexOAuth(client, store).browser_login(prompt, notices.append)

    assert credential.account_id == "account"
    assert seen[0].url == httpx.URL(TOKEN_URL)
    assert b"redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback" in seen[0].content
    assert store.load() == credential
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_device_login_polls_pending_then_exchanges(tmp_path: Path) -> None:
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if str(request.url) == DEVICE_USER_CODE_URL:
            return httpx.Response(
                200,
                json={"device_auth_id": "device", "user_code": "ABCD", "interval": 0},
            )
        if str(request.url) == DEVICE_TOKEN_URL:
            polls += 1
            if polls == 1:
                return httpx.Response(403, json={"error": "deviceauth_authorization_pending"})
            return httpx.Response(
                200, json={"authorization_code": "authorization", "code_verifier": "verifier"}
            )
        assert str(request.url) == TOKEN_URL
        assert (
            DEVICE_REDIRECT_URI.encode() in request.content
            or b"deviceauth%2Fcallback" in request.content
        )
        return httpx.Response(
            200,
            json={"access_token": _jwt("account"), "refresh_token": "refresh", "expires_in": 3600},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        oauth = CodexOAuth(client, CredentialStore(tmp_path / "oauth.json"))
        credential = await oauth.device_login(lambda _: None)

    assert polls == 2
    assert credential.account_id == "account"


def test_conversion_replays_opaque_reasoning_and_has_no_tools() -> None:
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}
    assistant = AssistantMessage(
        provider="openai",
        model="test-model",
        content=(ReasoningBlock("summary", reasoning_item), TextBlock("answer")),
        stop_reason="stop",
    )
    converted = convert_messages((UserMessage("question"), assistant))
    body = build_codex_request(_request((UserMessage("question"), assistant)))

    assert reasoning_item in converted
    assert "tools" not in body
    assert body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"]


def test_assistant_exec_replay_keeps_valid_marker_lines() -> None:
    message = AssistantMessage(
        provider="openai",
        model="test-model",
        content=(TextBlock("before\n"), ExecBlock("1 + 1\n"), TextBlock("after")),
        stop_reason="stop",
    )
    assert serialize_assistant_text(message) == "before\n<exec>\n1 + 1\n</exec>\nafter"


def test_normalizes_terminal_usage_and_length() -> None:
    events = normalize_openai_event(
        {
            "type": "response.incomplete",
            "response": {
                "id": "response",
                "status": "incomplete",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        }
    )
    assert events[-1].stop_reason == "length"
    assert events[-1].usage.total_tokens == 5


class _FakeStream:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self.values = values

    def __aiter__(self):  # type: ignore[no-untyped-def]
        async def iterate():  # type: ignore[no-untyped-def]
            for value in self.values:
                yield value

        return iterate()


@pytest.mark.asyncio
async def test_public_provider_drains_to_terminal_event() -> None:
    values = [
        {"type": "response.output_text.delta", "delta": "hello"},
        {"type": "response.completed", "response": {"id": "r", "usage": {}}},
    ]
    client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **_: _async_value(_FakeStream(values)))
    )
    provider = OpenAIProvider(client=client)
    events = [event async for event in provider.stream_response(_request())]
    assert [event.type for event in events] == ["start", "text_delta", "usage", "done"]


async def _async_value(value):  # type: ignore[no-untyped-def]
    return value


@pytest.mark.asyncio
async def test_codex_sse_headers_and_terminal_stream() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        data = (
            'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
            'data: {"type":"response.completed","response":{"id":"r","usage":{}}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})

    credential = OAuthCredential(
        _jwt("account"), "refresh", int(time.time() * 1000) + 1000, "account"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(credential, client=client)
        events = [event async for event in provider.stream_response(_request())]

    assert events[-1].type == "done"
    assert captured[0].headers["originator"] == "pi"
    assert captured[0].headers["chatgpt-account-id"] == "account"
