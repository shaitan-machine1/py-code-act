from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .storage import CredentialStore, OAuthCredential

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE_URL = "https://auth.openai.com"
AUTHORIZE_URL = f"{AUTH_BASE_URL}/oauth/authorize"
TOKEN_URL = f"{AUTH_BASE_URL}/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
DEVICE_USER_CODE_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/token"
DEVICE_VERIFICATION_URI = f"{AUTH_BASE_URL}/codex/device"
DEVICE_REDIRECT_URI = f"{AUTH_BASE_URL}/deviceauth/callback"
DEVICE_CODE_TIMEOUT_SECONDS = 15 * 60
SCOPE = "openid profile email offline_access"
JWT_CLAIM_PATH = "https://api.openai.com/auth"


class OAuthError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AuthorizationFlow:
    verifier: str
    state: str
    url: str


@dataclass(frozen=True, slots=True)
class DeviceAuthorization:
    device_auth_id: str
    user_code: str
    interval_seconds: float


type Prompt = Callable[[str], Awaitable[str]]
type Notify = Callable[[str], None]


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def create_authorization_flow() -> AuthorizationFlow:
    verifier = _base64url(secrets.token_bytes(32))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_hex(16)
    parameters = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "pi",
    }
    return AuthorizationFlow(verifier, state, f"{AUTHORIZE_URL}?{urlencode(parameters)}")


def parse_callback_url(value: str, expected_state: str) -> str:
    try:
        parsed = urlparse(value.strip())
    except ValueError as error:
        raise OAuthError("invalid callback URL") from error
    if not parsed.scheme or not parsed.netloc:
        raise OAuthError("paste the full callback URL from the browser address bar")
    parameters = parse_qs(parsed.query)
    error = parameters.get("error", [None])[0]
    if error:
        description = parameters.get("error_description", [error])[0]
        raise OAuthError(f"authorization failed: {description}")
    state = parameters.get("state", [None])[0]
    if state != expected_state:
        raise OAuthError("OAuth state mismatch")
    code = parameters.get("code", [None])[0]
    if not code:
        raise OAuthError("callback URL has no authorization code")
    return code


def decode_account_id(access_token: str) -> str:
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            raise ValueError
        payload_part = parts[1]
        payload_part += "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part))
        auth = payload[JWT_CLAIM_PATH]
        account_id = auth["chatgpt_account_id"]
        if not isinstance(account_id, str) or not account_id:
            raise ValueError
        return account_id
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise OAuthError("failed to extract ChatGPT account ID from access token") from error


class CodexOAuth:
    """OpenAI Codex OAuth protocol using an injectable async HTTP client."""

    def __init__(self, client: httpx.AsyncClient, store: CredentialStore) -> None:
        self.client = client
        self.store = store

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.request(method, url, **kwargs)
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as error:
            raise OAuthError(f"OpenAI Codex OAuth request failed: {error}") from error

    async def _token_request(
        self,
        operation: Literal["exchange", "refresh"],
        data: dict[str, str],
    ) -> OAuthCredential:
        response = await self._request(
            "POST",
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
        )
        if not response.is_success:
            raise OAuthError(
                f"OpenAI Codex token {operation} failed ({response.status_code}): "
                f"{response.text or response.reason_phrase}"
            )
        try:
            value = response.json()
            access = value["access_token"]
            refresh = value["refresh_token"]
            expires_in = value["expires_in"]
            if not isinstance(access, str) or not isinstance(refresh, str):
                raise ValueError
            if not isinstance(expires_in, (int, float)):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OAuthError(f"OpenAI Codex token {operation} response missing fields") from error
        return OAuthCredential(
            access=access,
            refresh=refresh,
            expires=int(time.time() * 1000 + expires_in * 1000),
            account_id=decode_account_id(access),
        )

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> OAuthCredential:
        return await self._token_request(
            "exchange",
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
        )

    async def refresh(self, refresh_token: str) -> OAuthCredential:
        return await self._token_request(
            "refresh",
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )

    async def browser_login(self, prompt: Prompt, notify: Notify) -> OAuthCredential:
        flow = create_authorization_flow()
        notify("Open this URL in a browser and authorize py-code-act:\n" + flow.url)
        callback = await prompt("Paste the full callback URL: ")
        code = parse_callback_url(callback, flow.state)
        credential = await self.exchange(code, flow.verifier, REDIRECT_URI)
        self.store.save(credential)
        return credential

    async def start_device_authorization(self) -> DeviceAuthorization:
        response = await self._request(
            "POST",
            DEVICE_USER_CODE_URL,
            headers={"Content-Type": "application/json"},
            json={"client_id": CLIENT_ID},
        )
        if response.status_code == 404:
            raise OAuthError("OpenAI Codex device-code login is not enabled; use browser login")
        if not response.is_success:
            raise OAuthError(
                f"OpenAI Codex device-code request failed ({response.status_code}): {response.text}"
            )
        try:
            value = response.json()
            interval = float(value["interval"])
            device_auth_id = value["device_auth_id"]
            user_code = value["user_code"]
            if (
                interval < 0
                or not isinstance(device_auth_id, str)
                or not isinstance(user_code, str)
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OAuthError("invalid OpenAI Codex device-code response") from error
        return DeviceAuthorization(device_auth_id, user_code, interval)

    async def poll_device_authorization(
        self,
        device: DeviceAuthorization,
        *,
        timeout_seconds: float = DEVICE_CODE_TIMEOUT_SECONDS,
    ) -> tuple[str, str]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        interval = device.interval_seconds
        while True:
            if asyncio.get_running_loop().time() >= deadline:
                raise OAuthError("OpenAI Codex device authorization expired")
            await asyncio.sleep(interval)
            response = await self._request(
                "POST",
                DEVICE_TOKEN_URL,
                headers={"Content-Type": "application/json"},
                json={
                    "device_auth_id": device.device_auth_id,
                    "user_code": device.user_code,
                },
            )
            if response.is_success:
                try:
                    value = response.json()
                    authorization_code = value["authorization_code"]
                    code_verifier = value["code_verifier"]
                    if not isinstance(authorization_code, str) or not authorization_code:
                        raise ValueError
                    if not isinstance(code_verifier, str) or not code_verifier:
                        raise ValueError
                    return authorization_code, code_verifier
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise OAuthError("invalid OpenAI Codex device token response") from error
            error_code: str | None = None
            try:
                raw_error = response.json().get("error")
                error_code = (
                    raw_error.get("code") if isinstance(raw_error, dict) else str(raw_error)
                )
            except json.JSONDecodeError, AttributeError:
                pass
            if (
                response.status_code in (403, 404)
                or error_code == "deviceauth_authorization_pending"
            ):
                continue
            if error_code == "slow_down":
                interval += 5
                continue
            message = (
                f"OpenAI Codex device authorization failed ({response.status_code}): "
                f"{response.text}"
            )
            raise OAuthError(message)

    async def device_login(self, notify: Notify) -> OAuthCredential:
        device = await self.start_device_authorization()
        notify(
            f"Open {DEVICE_VERIFICATION_URI} and enter code {device.user_code} "
            f"(expires in {DEVICE_CODE_TIMEOUT_SECONDS // 60} minutes)."
        )
        code, verifier = await self.poll_device_authorization(device)
        credential = await self.exchange(code, verifier, DEVICE_REDIRECT_URI)
        self.store.save(credential)
        return credential

    async def credential(
        self,
        login_method: Literal["browser", "device_code"],
        prompt: Prompt,
        notify: Notify,
    ) -> OAuthCredential:
        credential = self.store.load()
        now = int(time.time() * 1000)
        if credential is None:
            if login_method == "browser":
                return await self.browser_login(prompt, notify)
            return await self.device_login(notify)
        if credential.expires <= now + 60_000:
            credential = await self.refresh(credential.refresh)
            self.store.save(credential)
        return credential
