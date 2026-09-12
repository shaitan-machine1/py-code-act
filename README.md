# py-code-act

`py-code-act` is a small coding-agent harness that gives an OpenAI model a persistent Python kernel instead of JSON tool calls. The model requests one action by writing Python between line-anchored `<exec>` markers. The harness runs the cell in a child `ipykernel`, returns ordered output to the model, and continues until the model gives a text-only answer.

## Status

This is a development harness, not a sandbox or a distributable CLI. OpenAI's public Responses API and the ChatGPT Codex backend are the only provider transports. The Codex transport initially uses server-sent events (SSE), not WebSockets.

## Security

**Model-generated Python runs with the operating-system permissions of the user who starts the program.** It can read and modify files, run processes, access the network, and inspect same-user resources. The child process exists for persistence, interruption, and replacement; it does not provide isolation.

Provider credentials are not intentionally forwarded into the kernel environment, session ledger, or trace. This reduces accidental disclosure but is not a security boundary.

## Run

Requires regular CPython 3.14 and [uv](https://docs.astral.sh/uv/).

1. Review and edit [`run.py`](run.py). It is the complete development configuration; there are no raw command-line arguments.
2. Start the harness:

   ```bash
   uv run run.py
   ```

3. Enter one prompt at a time. Use an exact `/quit` line or EOF to exit. There is no general slash-command system.

The checked-in configuration selects Codex browser login and `gpt-5.6-sol`. Change `auth_mode`, model, paths, and limits directly in `run.py` as needed.

### Public OpenAI API

Set `auth_mode="api_key"` in `run.py`, then export a key:

```bash
export OPENAI_API_KEY=...
uv run run.py
```

The key may instead be supplied in memory through `RunConfig.api_key`, but it must not be checked into source control.

### ChatGPT Codex subscription

Set `auth_mode="codex"` and choose one `codex_login_method`:

- `browser`: the harness prints an OpenAI authorization URL. Complete authorization, then copy the full callback URL from the browser address bar and paste it into the terminal. No local callback server is started.
- `device_code`: the harness prints a verification URL and user code, then polls until authorization, cancellation, or expiry.

The implementation follows Pi's Codex OAuth constants, PKCE/token protocol, account-ID extraction, refresh behavior, and `originator: pi` request identity. Refreshable credentials are saved with user-only permissions under the platform configuration directory (normally `~/.config/py-code-act/openai-codex.json`). Delete that file to force a new login.

## Execution protocol

A model action has exactly one block:

```text
I will inspect the repository.
<exec>
from pathlib import Path
sorted(path.name for path in Path(".").iterdir())
</exec>
I will use the result next.
```

Markers must be alone on their lines. Prose can occur before and after the block. The complete provider stream is drained before Python starts. A malformed block, an incomplete block, or any block from a response ending with `length`, `error`, or `aborted` is never executed. Protocol failures become durable model-visible repair observations, not fake kernel executions.

Variables persist between cells in one kernel generation. Session resume and kernel replacement start empty Python memory; prior cells are never replayed automatically because they may have external side effects.

## Harness tools

Every kernel receives a self-describing `tools` object without an import:

```python
dir(tools)
help(tools.todo.create)
tools.todo.create("inspect tests", status="in_progress")
tools.skills.list()
tools.skills.load("a-skill")
tools.kernel.restart()
```

`tools.kernel.restart()` is deferred until its current cell finishes. The remainder of that cell runs in the old generation, after which the harness replaces the kernel and tells the model that memory was lost.

### Skills

Skills use Pi-compatible progressive discovery. Metadata is loaded at startup and visible skills are listed in the system prompt; complete `SKILL.md` content is loaded only through `tools.skills.load(name)`.

Locations include:

- `~/.pi/agent/skills/`
- `~/.agents/skills/`
- `<cwd>/.pi/skills/`
- `.agents/skills/` from the working directory through its Git root (or filesystem root)
- explicit `RunConfig.skill_paths`, which have highest precedence

Project trust gating is not implemented yet. Skills are instructions and may direct the model to execute code, so review them before use.

## Project instructions

The harness loads a global `AGENTS.md` from its platform configuration directory, then one context file per directory from filesystem root to the configured working directory. Candidate precedence is `AGENTS.override.md`, `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, then `CLAUDE.MD`. Nearer files are layered later.

## Sessions and traces

`RunConfig.session_path` directly names the append-only, durable semantic JSONL ledger. Its parent and file are created on first use. Starting again with that path resumes conversation history using a fresh kernel and appends a model-visible state-loss notice. Do not put durable sessions under `/tmp`.

Development traces default to `/tmp/py-code-act/traces/`. They are independent of sessions and can be deleted safely. Authorization fields and known token fields are redacted, but traces can still contain sensitive prompts, generated code, repository content, and execution output. Disable traces or raw provider events through `TraceConfig`.

Execution output is currently preserved in full in the session, terminal, and model observation. Output truncation/artifacts and context compaction are intentionally deferred. Very large outputs or long sessions can therefore exceed terminal or model context limits.

## Development

```bash
uv run ruff format --check src tests run.py
uv run ruff check src tests run.py
uv run basedpyright src tests run.py
uv run pytest
```

The normal suite uses deterministic fake transports and local `ipykernel` processes; it makes no paid provider calls.
