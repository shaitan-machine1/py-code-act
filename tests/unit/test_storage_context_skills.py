import json
from pathlib import Path

import pytest

from py_code_act.agent.context import discover_context_files
from py_code_act.domain.messages import UserMessage
from py_code_act.storage.session_jsonl import SessionFormatError, SessionLedger
from py_code_act.tools.skills import SkillsService, discover_skills, format_skills_for_prompt


def test_session_ledger_round_trip_and_resume(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "session.jsonl"
    ledger = SessionLedger(path)
    original_id = ledger.session_id
    message = UserMessage("hello", id="msg_test")
    ledger.append_message(message)
    ledger.append("todo_change", {"operation": "create", "name": "work", "status": "pending"})

    resumed = SessionLedger(path)

    assert resumed.resumed
    assert resumed.session_id == original_id
    assert resumed.messages() == [message]
    assert resumed.todo_state() == {"work": "pending"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_session_ignores_only_partial_final_line(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    ledger = SessionLedger(path)
    path.write_bytes(path.read_bytes() + b'{"partial":')
    recovered = SessionLedger(path)
    assert recovered.session_id == ledger.session_id
    recovered.append("session_info", {"recovered": True})
    assert b'"partial"' not in path.read_bytes()

    path.write_bytes(path.read_bytes() + b"}\nnot-json\n")
    with pytest.raises(SessionFormatError):
        SessionLedger(path)


def test_session_tracks_generation_even_without_execution(tmp_path: Path) -> None:
    ledger = SessionLedger(tmp_path / "session.jsonl")
    ledger.append("session_info", {"kernel_generation": 1})
    resumed = SessionLedger(ledger.path)
    assert resumed.max_kernel_generation() == 1


def test_future_session_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session",
                "schema_version": 999,
                "id": "entry",
                "timestamp": "now",
                "session_id": "session",
                "payload": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SessionFormatError, match="newer"):
        SessionLedger(path)


def test_context_files_layer_root_to_cwd_with_candidate_precedence(tmp_path: Path) -> None:
    root = tmp_path / "root"
    cwd = root / "project" / "src"
    cwd.mkdir(parents=True)
    (root / "AGENTS.md").write_text("root", encoding="utf-8")
    (root / "project" / "AGENTS.md").write_text("ordinary", encoding="utf-8")
    (root / "project" / "AGENTS.override.md").write_text("override", encoding="utf-8")
    (cwd / "CLAUDE.md").write_text("nearest", encoding="utf-8")

    files = discover_context_files(cwd)

    relevant = [item for item in files if root in item.path.parents or item.path == root]
    assert [item.content for item in relevant] == ["root", "override", "nearest"]


def _write_skill(path: Path, name: str, description: str, *, disabled: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"disable-model-invocation: {str(disabled).lower()}\n"
        "---\n"
        f"# {name}\n",
        encoding="utf-8",
    )


def test_explicit_skill_wins_and_disabled_skill_is_hidden(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()
    explicit = tmp_path / "explicit"
    _write_skill(explicit / "first" / "SKILL.md", "same", "explicit <skill>")
    _write_skill(cwd / ".pi" / "skills" / "second" / "SKILL.md", "same", "project")
    _write_skill(cwd / ".pi" / "skills" / "hidden" / "SKILL.md", "hidden", "hidden", disabled=True)

    discovery = discover_skills(cwd, (explicit,))
    service = SkillsService(discovery.skills)
    prompt = format_skills_for_prompt(discovery.skills)

    assert service.load("same").base_dir.name == "first"
    assert "explicit &lt;skill&gt;" in prompt
    assert "hidden" not in prompt
    assert any(item.level == "collision" for item in discovery.diagnostics)
