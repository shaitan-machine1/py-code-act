from __future__ import annotations

import asyncio
from pathlib import Path

from py_code_act.application import run
from py_code_act.config import RunConfig, TraceConfig

ROOT = Path(__file__).parent

CONFIG = RunConfig(
    model="gpt-5.6-sol",
    auth_mode="codex",
    codex_login_method="browser",
    cwd=ROOT,
    session_path=ROOT / ".py-code-act" / "session.jsonl",
    reasoning_level="medium",
    display_reasoning=False,
    max_turns=16,
    max_executions=12,
    max_protocol_repairs=2,
    run_timeout_seconds=None,
    execution_timeout_seconds=300.0,
    interrupt_grace_seconds=5.0,
    provider_timeout_seconds=300.0,
    tool_rpc_timeout_seconds=30.0,
    skill_paths=(),
    trace=TraceConfig(enabled=True, file=None, provider_raw=True),
)

if __name__ == "__main__":
    asyncio.run(run(CONFIG))
