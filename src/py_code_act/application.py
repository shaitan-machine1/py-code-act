from __future__ import annotations

import asyncio

import httpx
from platformdirs import user_config_path

from .agent.context import discover_context_files
from .agent.loop import AgentLoop, RestartController
from .agent.session import AgentSession
from .agent.state import AgentState
from .auth.openai_codex import CodexOAuth
from .auth.storage import CredentialStore
from .config import RunConfig
from .domain.events import RuntimeEvent
from .domain.messages import KernelNoticeMessage
from .kernel.jupyter import JupyterKernel
from .observability.trace import TraceRecorder
from .protocol.system_prompt import build_system_prompt
from .providers.base import ModelProvider
from .providers.openai import OpenAIProvider
from .providers.openai_codex import OpenAICodexProvider
from .rendering.terminal import TerminalRenderer
from .storage.session_jsonl import SessionLedger
from .tools.namespace import ToolNamespace, ToolRegistry
from .tools.protocol import KernelService
from .tools.server import ToolRpcServer
from .tools.skills import SkillsService, discover_skills
from .tools.todo import Task, TodoService


async def _create_provider(
    config: RunConfig,
    renderer: TerminalRenderer,
    trace: TraceRecorder,
) -> ModelProvider:
    callback = trace.record
    if config.auth_mode == "api_key":
        return OpenAIProvider(
            config.api_key,
            timeout_seconds=config.provider_timeout_seconds,
            trace=callback,
        )

    async with httpx.AsyncClient(timeout=config.provider_timeout_seconds) as client:
        oauth = CodexOAuth(client, CredentialStore(config.oauth_path))
        credential = await oauth.credential(
            config.codex_login_method,
            renderer.prompt,
            renderer.notify,
        )
    return OpenAICodexProvider(
        credential,
        timeout_seconds=config.provider_timeout_seconds,
        trace=callback,
    )


async def run(config: RunConfig) -> None:
    """Run the configured line-oriented coding-agent application."""

    config = config.normalized()
    config.validate()
    if not config.cwd.is_dir():
        raise ValueError(f"working directory does not exist: {config.cwd}")
    if str(config.session_path).startswith("/tmp/"):
        raise ValueError("durable session_path must not be under /tmp")

    ledger = SessionLedger(config.session_path)
    trace = TraceRecorder(config.trace, ledger.session_id)
    renderer = TerminalRenderer(display_reasoning=config.display_reasoning)
    renderer.security_warning()
    provider = await _create_provider(config, renderer, trace)

    skills = discover_skills(config.cwd, config.skill_paths)
    for diagnostic in skills.diagnostics:
        trace.record(
            "skills.diagnostic",
            {"level": diagnostic.level, "message": diagnostic.message, "path": diagnostic.path},
        )
    global_context = user_config_path("py-code-act") / "AGENTS.md"
    context_files = tuple(discover_context_files(config.cwd, global_context))
    restart_controller = RestartController()

    session_holder: list[AgentSession] = []

    def todo_changed(operation: str, task: Task | None) -> None:
        payload: dict[str, object] = {"operation": operation}
        if task is not None:
            payload.update({"name": task.name, "status": task.status})
        ledger.append("todo_change", payload)
        if session_holder:
            session_holder[0].emit_background("todo_changed", payload=payload)

    todo = TodoService(ledger.todo_state(), todo_changed)
    skill_service = SkillsService(skills.skills)
    registry = ToolRegistry(
        (
            ToolNamespace(
                "todo",
                todo,
                "`tools.todo` maintains a simple task list.",
                ("create", "get", "update", "list", "clear"),
            ),
            ToolNamespace(
                "skills",
                skill_service,
                "`tools.skills` lists and loads Agent Skills on demand.",
                ("list", "load"),
            ),
            ToolNamespace(
                "kernel",
                KernelService(restart_controller.request),
                "`tools.kernel.restart()` schedules replacement after the current cell.",
                ("restart",),
            ),
        )
    )
    system_prompt = build_system_prompt(registry, skills.skills, context_files)
    state = AgentState(
        system_prompt=system_prompt,
        model=config.model,
        reasoning_level=config.reasoning_level,
        messages=ledger.messages(),
    )
    session = AgentSession(ledger, state)
    session_holder.append(session)
    session.subscribe(renderer.handle)

    async def trace_runtime(event: RuntimeEvent) -> None:
        trace.record(
            f"runtime.{event.type}",
            event.payload,
            session_id=event.session_id,
            run_id=event.run_id,
            turn_id=event.turn_id,
            request_id=event.request_id,
            message_id=event.message_id,
            exec_id=event.exec_id,
            kernel_generation=event.kernel_generation,
            sequence=event.sequence,
        )

    session.subscribe(trace_runtime)
    server = ToolRpcServer(registry, trace=trace.record)
    await server.start()
    initial_generation = ledger.max_kernel_generation() + 1
    kernel = JupyterKernel(
        str(config.cwd),
        rpc_host="127.0.0.1",
        rpc_port=server.port,
        rpc_token=server.token,
        rpc_timeout_seconds=config.tool_rpc_timeout_seconds,
        rpc_token_rotator=server.rotate_token,
        initial_generation=initial_generation,
        interrupt_grace_seconds=config.interrupt_grace_seconds,
        trace=trace.record,
    )

    try:
        await kernel.start()
        state.kernel_generation = kernel.generation
        await session.emit("kernel_start", kernel_generation=kernel.generation)
        if ledger.resumed:
            ledger.append(
                "kernel_restart",
                {"kernel_generation": kernel.generation, "reason": "resume"},
            )
            notice = KernelNoticeMessage(
                message=(
                    "This session was resumed with a fresh Python kernel. Ordinary in-memory "
                    "variables are gone; filesystem and external side effects may remain."
                ),
                kernel_generation=kernel.generation,
                reason="resume",
            )
            session.append_message(notice)
            await session.emit(
                "message_start",
                message_id=notice.id,
                kernel_generation=kernel.generation,
                payload={"role": "kernel_notice"},
            )
            await session.emit(
                "message_end",
                message_id=notice.id,
                kernel_generation=kernel.generation,
                payload={"role": "kernel_notice", "message": notice},
            )
        else:
            ledger.append(
                "session_info",
                {"kernel_generation": kernel.generation, "cwd": str(config.cwd)},
            )

        agent = AgentLoop(config, provider, kernel, session, restart_controller)
        while True:
            try:
                text = await renderer.prompt("You: ")
            except EOFError, KeyboardInterrupt, asyncio.CancelledError:
                break
            if text == "/quit":
                break
            if not text.strip():
                continue
            await agent.run(text)
    finally:
        await kernel.shutdown()
        await server.close()
        if isinstance(provider, (OpenAIProvider, OpenAICodexProvider)):
            await provider.close()
