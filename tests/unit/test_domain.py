from pathlib import Path

from py_code_act.config import RunConfig
from py_code_act.domain.execution import ErrorOutput, StreamOutput
from py_code_act.domain.messages import (
    AssistantMessage,
    ExecBlock,
    ExecutionMessage,
    ReasoningBlock,
    TextBlock,
    message_from_dict,
    message_to_dict,
)
from py_code_act.domain.usage import Usage


def test_assistant_message_round_trip() -> None:
    message = AssistantMessage(
        provider="openai",
        model="test-model",
        content=(
            TextBlock("before"),
            ReasoningBlock("summary", {"type": "reasoning", "encrypted_content": "opaque"}),
            ExecBlock("1 + 1", id="exec_test"),
            TextBlock("after"),
        ),
        usage=Usage(input_tokens=3, output_tokens=5, reasoning_tokens=2),
        stop_reason="stop",
        provider_metadata={"response_id": "resp_test"},
        id="msg_test",
        created_at="2026-01-01T00:00:00Z",
    )

    assert message_from_dict(message_to_dict(message)) == message


def test_execution_message_round_trip_preserves_output_order() -> None:
    message = ExecutionMessage(
        id="msg_execution",
        exec_id="exec_test",
        kernel_generation=2,
        status="error",
        outputs=(
            StreamOutput("stdout", "first"),
            StreamOutput("stderr", "second"),
            ErrorOutput("ValueError", "bad", ("line one", "line two")),
        ),
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
    )

    assert message_from_dict(message_to_dict(message)) == message


def test_config_resolves_relative_session_path(tmp_path: Path) -> None:
    config = RunConfig(
        model="test-model",
        auth_mode="api_key",
        cwd=tmp_path,
        session_path=Path("state/session.jsonl"),
    ).normalized()

    assert config.session_path == tmp_path / "state/session.jsonl"
