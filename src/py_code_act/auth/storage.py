from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from py_code_act.storage.paths import ensure_private_file


@dataclass(frozen=True, slots=True)
class OAuthCredential:
    access: str
    refresh: str
    expires: int
    account_id: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OAuthCredential:
        required = ("access", "refresh", "expires", "account_id")
        if any(key not in value for key in required):
            raise ValueError("stored OAuth credential is missing required fields")
        return cls(
            access=str(value["access"]),
            refresh=str(value["refresh"]),
            expires=int(value["expires"]),
            account_id=str(value["account_id"]),
        )


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> OAuthCredential | None:
        if not self.path.is_file():
            return None
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("stored OAuth credential must be an object")
        return OAuthCredential.from_dict(value)

    def save(self, credential: OAuthCredential) -> None:
        ensure_private_file(self.path)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        ensure_private_file(temporary)
        temporary.write_text(
            json.dumps(asdict(credential), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)
        self.path.chmod(0o600)
