import json
from pathlib import Path

from py_code_act.config import TraceConfig
from py_code_act.observability.redaction import redact
from py_code_act.observability.trace import TraceRecorder


def test_recursive_secret_redaction() -> None:
    value = {
        "Authorization": "Bearer secret",
        "nested": [{"access_token": "token", "safe": "visible"}],
    }
    assert redact(value) == {
        "Authorization": "[REDACTED]",
        "nested": [{"access_token": "[REDACTED]", "safe": "visible"}],
    }


def test_trace_is_private_redacted_and_raw_toggle_is_independent(tmp_path: Path) -> None:
    path = tmp_path / "trace.log"
    trace = TraceRecorder(TraceConfig(enabled=True, file=path, provider_raw=False), "session")
    trace.record("openai.raw_event", {"ignored": True})
    trace.record("openai.request", {"api_key": "secret", "input": "prompt"})

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["payload"] == {"api_key": "[REDACTED]", "input": "prompt"}
    assert path.stat().st_mode & 0o777 == 0o600
