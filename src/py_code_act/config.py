from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from platformdirs import user_config_path

AuthMode = Literal["api_key", "codex"]
CodexLoginMethod = Literal["browser", "device_code"]
ReasoningLevel = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


@dataclass(frozen=True, slots=True)
class TraceConfig:
    """Development trace configuration."""

    enabled: bool = True
    file: Path | None = None
    provider_raw: bool = True


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Complete configuration for one interactive py-code-act process."""

    model: str
    auth_mode: AuthMode
    cwd: Path
    session_path: Path
    codex_login_method: CodexLoginMethod = "browser"
    api_key: str | None = None
    reasoning_level: ReasoningLevel = "medium"
    display_reasoning: bool = False
    max_turns: int = 16
    max_executions: int = 12
    max_protocol_repairs: int = 2
    run_timeout_seconds: float | None = None
    execution_timeout_seconds: float = 300.0
    interrupt_grace_seconds: float = 5.0
    provider_timeout_seconds: float = 300.0
    tool_rpc_timeout_seconds: float = 30.0
    skill_paths: tuple[Path, ...] = ()
    trace: TraceConfig = field(default_factory=TraceConfig)
    oauth_path: Path = field(
        default_factory=lambda: user_config_path("py-code-act") / "openai-codex.json"
    )

    def normalized(self) -> RunConfig:
        """Return a configuration with absolute filesystem paths."""

        cwd = self.cwd.expanduser().resolve()
        session_path = self.session_path.expanduser()
        if not session_path.is_absolute():
            session_path = cwd / session_path
        oauth_path = self.oauth_path.expanduser()
        return RunConfig(
            model=self.model,
            auth_mode=self.auth_mode,
            cwd=cwd,
            session_path=session_path.resolve(),
            codex_login_method=self.codex_login_method,
            api_key=self.api_key,
            reasoning_level=self.reasoning_level,
            display_reasoning=self.display_reasoning,
            max_turns=self.max_turns,
            max_executions=self.max_executions,
            max_protocol_repairs=self.max_protocol_repairs,
            run_timeout_seconds=self.run_timeout_seconds,
            execution_timeout_seconds=self.execution_timeout_seconds,
            interrupt_grace_seconds=self.interrupt_grace_seconds,
            provider_timeout_seconds=self.provider_timeout_seconds,
            tool_rpc_timeout_seconds=self.tool_rpc_timeout_seconds,
            skill_paths=tuple(
                (
                    path.expanduser().resolve()
                    if path.expanduser().is_absolute()
                    else (cwd / path.expanduser()).resolve()
                )
                for path in self.skill_paths
            ),
            trace=self.trace,
            oauth_path=oauth_path.resolve(),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` when a configured invariant is invalid."""

        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.max_turns < 1 or self.max_executions < 0 or self.max_protocol_repairs < 0:
            raise ValueError("run limits must be non-negative and max_turns must be positive")
        for name, value in (
            ("execution_timeout_seconds", self.execution_timeout_seconds),
            ("interrupt_grace_seconds", self.interrupt_grace_seconds),
            ("provider_timeout_seconds", self.provider_timeout_seconds),
            ("tool_rpc_timeout_seconds", self.tool_rpc_timeout_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
