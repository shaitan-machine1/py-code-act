from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from py_code_act.domain.ids import new_id
from py_code_act.domain.messages import Message, message_from_dict, message_to_dict, utc_now

from .paths import ensure_private_file

SCHEMA_VERSION = 1


class SessionFormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionEntry:
    type: str
    id: str
    timestamp: str
    session_id: str
    payload: Mapping[str, Any]
    schema_version: int = SCHEMA_VERSION
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **dict(self.extra),
            "type": self.type,
            "schema_version": self.schema_version,
            "id": self.id,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SessionEntry:
        version = int(value.get("schema_version", 0))
        if version > SCHEMA_VERSION:
            raise SessionFormatError(
                f"session schema {version} is newer than supported schema {SCHEMA_VERSION}"
            )
        if version < 1:
            raise SessionFormatError(f"unsupported session schema: {version}")
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise SessionFormatError("session entry payload must be an object")
        known = {"type", "schema_version", "id", "timestamp", "session_id", "payload"}
        return cls(
            type=str(value.get("type", "")),
            schema_version=version,
            id=str(value.get("id", "")),
            timestamp=str(value.get("timestamp", "")),
            session_id=str(value.get("session_id", "")),
            payload=dict(payload),
            extra={key: item for key, item in value.items() if key not in known},
        )


class SessionLedger:
    """Append-only durable semantic JSONL session storage."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries = self._read_existing(path)
        if self.entries:
            self.session_id = self.entries[0].session_id
            if not self.session_id:
                raise SessionFormatError("existing session has no session_id")
            self.resumed = True
        else:
            self.session_id = new_id("session")
            self.resumed = False
            ensure_private_file(path)
            self.append(
                "session",
                {
                    "created_at": utc_now(),
                    "format": "py-code-act-semantic-jsonl",
                },
            )

    @staticmethod
    def _read_existing(path: Path) -> list[SessionEntry]:
        if not path.exists():
            return []
        data = path.read_bytes()
        if not data:
            return []
        lines = data.splitlines(keepends=True)
        result: list[SessionEntry] = []
        byte_offset = 0
        for index, raw_line in enumerate(lines):
            line_start = byte_offset
            byte_offset += len(raw_line)
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                is_partial_final = index == len(lines) - 1 and not raw_line.endswith((b"\n", b"\r"))
                if is_partial_final:
                    with path.open("r+b") as stream:
                        stream.truncate(line_start)
                    break
                raise SessionFormatError(f"invalid JSONL at line {index + 1}: {error}") from error
            if not isinstance(value, Mapping):
                raise SessionFormatError(f"session line {index + 1} is not an object")
            result.append(SessionEntry.from_dict(value))
        if result:
            session_ids = {entry.session_id for entry in result}
            if len(session_ids) != 1:
                raise SessionFormatError("session ledger contains multiple session IDs")
        return result

    def append(self, entry_type: str, payload: Mapping[str, Any]) -> SessionEntry:
        entry = SessionEntry(
            type=entry_type,
            id=new_id("entry"),
            timestamp=utc_now(),
            session_id=self.session_id,
            payload=dict(payload),
        )
        ensure_private_file(self.path)
        line = json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        self.entries.append(entry)
        return entry

    def append_message(self, message: Message) -> SessionEntry:
        return self.append("message", {"message": message_to_dict(message)})

    def messages(self) -> list[Message]:
        result: list[Message] = []
        for entry in self.entries:
            if entry.type != "message":
                continue
            raw = entry.payload.get("message")
            if isinstance(raw, Mapping):
                result.append(message_from_dict(raw))
        return result

    def todo_state(self) -> dict[str, str]:
        state: dict[str, str] = {}
        for entry in self.entries:
            if entry.type != "todo_change":
                continue
            operation = entry.payload.get("operation")
            name = str(entry.payload.get("name", ""))
            if operation == "clear":
                state.clear()
            elif operation in ("create", "update") and name:
                state[name] = str(entry.payload.get("status", "pending"))
        return state

    def max_kernel_generation(self) -> int:
        generations: list[int] = []
        for entry in self.entries:
            if entry.type in ("kernel_restart", "session_info"):
                generations.append(int(entry.payload.get("kernel_generation", 0)))
        for message in self.messages():
            generation = getattr(message, "kernel_generation", None)
            if isinstance(generation, int):
                generations.append(generation)
        return max(generations, default=0)

    def extend_messages(self, messages: Iterable[Message]) -> None:
        for message in messages:
            self.append_message(message)
