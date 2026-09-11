# py-code-act Implementation Plan

## 1. Goal

Build a minimal Python coding-agent harness where models do not receive JSON tool schemas and do not make native tool calls. The model requests actions by emitting Python inside a reserved block:

```text
<exec>
from pathlib import Path
list(Path(".").iterdir())
</exec>
```

The harness executes that code in a persistent Jupyter-like Python kernel, captures ordered stdout/stderr, logging output, the last expression, displays, and exceptions, then sends a structured textual observation back to the model.

The first supported provider is OpenAI only, through either the public API or the ChatGPT Codex backend. The first user interface is a conventional line-oriented interactive runner, not a full-screen TUI. During development it is launched only through a configured root-level `run.py`; there is no command-line argument surface.

## 2. Initial scope

### Included

- Regular CPython 3.14 (not the free-threaded build) and uv packaging under `src/py_code_act/`
- OpenAI Responses API using `OPENAI_API_KEY` authentication
- OpenAI Codex/ChatGPT authentication using a headless OAuth device-code flow
- Provider-neutral internal interfaces, with OpenAI public-API and Codex transports only
- Streaming assistant text and reasoning events where OpenAI exposes them
- A strict streaming `<exec>` parser
- A persistent child `ipykernel` managed through `jupyter_client`
- Ordered stdout/stderr, logger output, last-expression values, displays, and exceptions
- A sequential model/execution agent loop
- A readable line-oriented terminal renderer
- A verbose, disposable development `.log` trace
- A separate durable JSONL session ledger
- An injected, self-describing `tools` Python namespace backed by harness RPC
- A simple harness-owned todo service
- Pi-style Agent Skills discovery with progressive disclosure through `tools.skills`
- Interrupt, timeout, restart, output truncation, and artifact handling
- Manual and eventual automatic context compaction
- Unit, integration, and scripted end-to-end tests

### Explicitly excluded from the initial implementation

- Anthropic
- DeepSeek
- Native provider tool calls
- JSON tool schemas
- Built-in read/write/edit/bash tools
- Multiple or parallel action execution
- Full-screen TUI
- Extensions, plugins, packages, themes, and a generic resource loader beyond the focused skills loader
- MCP, subagents, and plan mode
- Permission dialogs or sandboxing
- OAuth and subscription authentication
- Image input or generation
- Dynamic model catalogs
- Session trees, branching, labels, and branch summaries
- Telemetry and update checks

The design must leave clean boundaries for additional providers and interfaces, but no placeholder Anthropic or DeepSeek implementations should be added.

## 3. Design principles

1. **Separate provider, agent, execution, persistence, and presentation layers.**
2. **Keep messages, events, trace records, and session entries distinct.**
3. **Use immutable delta events and one authoritative final message.**
4. **Never execute an incomplete `<exec>` block.**
5. **Treat the kernel process as a lifecycle boundary, not a security sandbox.**
6. **Do not rely on provider-side conversation state.** Persist enough local information to rebuild requests.
7. **Keep OpenAI-specific data inside the OpenAI adapter or opaque provider metadata.**
8. **Make failures observable and recoverable without hiding partial output.**
9. **Prefer a complete vertical slice over broad provider or UI support.**
10. **Do not automatically replay prior Python cells when resuming a session.** They may have external side effects.

## 4. Proposed source layout

```text
run.py

src/py_code_act/
  __init__.py
  application.py
  config.py

  auth/
    __init__.py
    storage.py
    openai_codex.py

  domain/
    __init__.py
    ids.py
    messages.py
    models.py
    events.py
    execution.py
    usage.py

  providers/
    __init__.py
    base.py
    openai.py
    openai_codex.py
    openai_conversion.py

  protocol/
    __init__.py
    exec_parser.py
    exec_result.py
    system_prompt.py

  kernel/
    __init__.py
    base.py
    jupyter.py
    bootstrap.py
    outputs.py

  agent/
    __init__.py
    loop.py
    state.py
    context.py
    session.py

  observability/
    __init__.py
    trace.py
    redaction.py

  rendering/
    __init__.py
    terminal.py

  storage/
    __init__.py
    session_jsonl.py
    artifacts.py
    paths.py

  tools/
    __init__.py
    protocol.py
    server.py
    proxy.py
    namespace.py
    todo.py
    skills.py
```

Tests should mirror source boundaries:

```text
tests/
  unit/
  integration/
  e2e/
  fixtures/
```


## 5. Data model

Use typed dataclasses and explicit serialization functions. Avoid passing provider SDK objects outside provider code.

### 5.1 Canonical transcript messages

```text
UserMessage
AssistantMessage
ExecutionMessage
CompactionMessage
KernelNoticeMessage
```

`KernelNoticeMessage` is an internal transcript message used when the model must know that interpreter state was lost. Pure UI notices should remain session entries/events and should not enter model context.

### 5.2 Assistant content blocks

```text
TextBlock
ReasoningBlock
ExecBlock
```

Suggested `ExecBlock` fields:

```text
id
code
```

Assistant metadata should include:

```text
id
provider
model
content
usage
stop_reason
error_message
created_at
provider_metadata
```

`provider_metadata` must be an explicitly serializable opaque mapping used only when OpenAI requires replay data. It must not contain SDK objects or credentials.

### 5.3 Execution output blocks

```text
StreamOutput(channel="stdout" | "stderr", text=...)
ValueOutput(mime_type="text/plain", data=...)
DisplayOutput(mime_type="text/plain", data=...)
ErrorOutput(name=..., message=..., traceback=...)
TruncationOutput(artifact_path=..., omitted_bytes=..., sha256=...)
```

An `ExecutionMessage` should contain:

```text
id
exec_id
kernel_generation
status: ok | error | interrupted | timed_out | kernel_lost | protocol_error
outputs: ordered list of output blocks
started_at
finished_at
```

Keep outputs ordered. Do not flatten stdout and stderr into separate aggregate fields because that loses interleaving.

### 5.4 Stop reasons

Provider completion reasons should be normalized to:

```text
stop
length
error
aborted
```

There is no provider `tool_use` reason. The agent decides to continue by finding a complete `ExecBlock` in the assistant message.

## 6. Event model

### 6.1 Stable public runtime events

These events drive the terminal renderer and any future SDK or JSON mode:

```text
agent_start
agent_end
turn_start
turn_end
message_start
message_text_delta
message_reasoning_delta
exec_code_delta
message_end
exec_start
exec_output
exec_end
kernel_start
kernel_restart_requested
kernel_restarted
kernel_failed
usage_update
todo_changed
```

Every event envelope should include applicable correlation fields:

```text
sequence
occurred_at
session_id
run_id
turn_id
request_id
message_id
exec_id
kernel_generation
```

Not every event needs every field, but naming must be consistent.

Events should carry deltas, not shared mutable cumulative messages. The session/agent owns builders for the current assistant message and execution result. `message_end` and `exec_end` carry authoritative completed objects.

### 6.2 Internal trace records

The development trace may record unstable internals:

```text
openai.request
openai.raw_event
provider.normalized_event
protocol.open_tag
protocol.close_tag
protocol.error
kernel.shell_message
kernel.iopub_message
kernel.control_message
agent.state_transition
session.append_started
session.append_finished
tools.rpc_request
tools.rpc_response
```

These records are not public runtime events and do not enter session history.

### 6.3 Required event ordering

A normal text-only interaction:

```text
agent_start
turn_start
message_start(user)
message_end(user)
message_start(assistant)
message_text_delta*
message_end(assistant)
turn_end
agent_end
```

An execution interaction:

```text
agent_start
turn_start
message_start(user)
message_end(user)
message_start(assistant)
message_text_delta*
exec_code_delta*
message_end(assistant)
exec_start
exec_output*
exec_end
message_start(execution)
message_end(execution)
turn_end
turn_start
message_start(assistant)
...
agent_end
```

An assistant response plus its requested execution is one turn. The model response after the execution result begins the next turn.

## 7. Observability

### 7.1 Development trace log

Write disposable, human-readable trace files under a preconfigured temporary root:

```text
/tmp/py-code-act/traces/2026-04-01T121042-session_id.log
```

Create `/tmp/py-code-act/` and its children with user-only permissions where supported. Traces are deliberately temporary development diagnostics; durable sessions and authentication credentials must not be stored under `/tmp`.

Example format:

```text
2026-04-01T12:10:42.123Z seq=14 layer=provider event=response.output_text.delta
session=session_1 run=run_1 turn=turn_1 request=req_1
payload:
  delta='Inspecting the repository...'
```

Requirements:

- Append in event sequence order.
- Flush often enough that crash debugging retains recent records.
- Record wall-clock and monotonic timing where useful.
- Make trace-write failure non-fatal to the agent, but report it once on stderr.
- Redact API keys, authorization headers, cookies, credential objects, and known secret fields.
- Create files with user-only permissions where supported.
- Document that traces still contain sensitive prompts, generated code, file content, and execution output.
- Allow raw provider tracing to be disabled independently because it is verbose.

Expose equivalent `TraceConfig` fields (`enabled`, `file`, and `provider_raw`) in the root `run.py` configuration. There are no trace command-line arguments.

### 7.2 Session JSONL is separate

The session JSONL is durable semantic state. It must not contain raw OpenAI events, parser transitions, token deltas, or debug stack traces unrelated to a semantic failure.

The trace file is never loaded to resume a session. Deleting it must not affect session behavior.

### 7.3 Terminal rendering

The terminal renderer consumes only stable runtime events. It should render:

- User prompts
- Streaming assistant prose
- Reasoning only when explicitly enabled for display
- Python code as a distinct block
- Ordered stdout/stderr
- Last-expression values
- Exceptions
- Kernel restarts
- Usage summary at turn or run completion

The agent loop, provider adapter, parser, and kernel must not print directly to the interactive terminal.

Harness diagnostics go to the trace file. Python `print()` and Python logging produced by executed code become execution output, not harness diagnostics.

## 8. OpenAI provider

### 8.1 API transports

Support two OpenAI-only transports selected by `RunConfig.auth_mode`:

- `api_key`: use the official asynchronous OpenAI Python SDK and the public Responses API. Resolve the key from an explicit in-memory config value or `OPENAI_API_KEY` without persisting it.
- `codex`: use a ChatGPT OAuth access token with `https://chatgpt.com/backend-api/codex/responses`. Match the request shape and required headers used by Pi's OpenAI Codex transport, including `Authorization`, `chatgpt-account-id`, `originator`, and the Responses beta header.

Neither transport sends native tool definitions or a `tools` field.

### 8.2 Codex authentication

Use Pi's simpler headless device-code flow rather than requiring a local callback server:

1. Request a device authorization from OpenAI and print its verification URL and user code.
2. Poll until the user authorizes, the request expires, or the run is cancelled.
3. Exchange the returned authorization code and PKCE verifier for access and refresh tokens.
4. Extract the ChatGPT account ID from the access-token JWT.
5. Refresh expired access tokens automatically.

When `auth_mode="codex"` is configured and no valid credential exists, `uv run run.py` starts this login flow before opening the agent session. Store the resulting OAuth credential in the application configuration directory selected by `platformdirs`, in a file created with user-only permissions. The credential is never written to the session ledger, trace, generated `run.py`, or kernel environment.

Authentication failures, malformed token responses, cancellation, and device-code expiry must produce clear terminal errors and sanitized traces. The Codex endpoints and client protocol are implementation details isolated in the auth/provider packages because they may change independently of the public OpenAI API.

### 8.3 Provider interface

Define a narrow provider protocol similar to:

```python
class ModelProvider(Protocol):
    async def stream_response(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ProviderEvent]: ...
```

Keep model identity separate from API implementation:

```text
Model: provider, model ID, context window, maximum output, reasoning support
Provider: authentication and request dispatch
Transport: public OpenAI Responses API or OpenAI Codex backend
Adapter: canonical/OpenAI request and event conversion
```

Only these OpenAI implementations and tests are created initially.

### 8.4 Conversion behavior

The OpenAI adapters must:

- Convert canonical transcript messages to the applicable Responses input.
- Serialize assistant `ExecBlock` values back to `<exec>` text.
- Serialize `ExecutionMessage` as a synthetic user observation.
- Preserve required opaque OpenAI continuation/reasoning data when applicable.
- Normalize streamed text, reasoning summaries, usage, completion, and errors.
- Never expose SDK or raw transport event objects outside the provider package.
- Trace sanitized request summaries and optionally sanitized raw response events.
- Support cancellation through `asyncio` task cancellation and transport closure.

Do not depend exclusively on `previous_response_id`; local session messages must remain sufficient to reconstruct a request.

### 8.5 Model selection

Configure the model directly in `run.py`; the initial preconfigured model is `gpt-5.6-sol`. Avoid building a model catalog. Model capabilities can use a small explicit configuration record and conservative defaults.

## 9. `<exec>` protocol

### 9.1 Grammar

Treat the markers as framing syntax, not general XML:

```text
assistant-response := final-text | prefix-text exec-block
exec-block         := OPEN_LINE python-code CLOSE_LINE
OPEN_LINE          := line containing exactly "<exec>"
CLOSE_LINE         := line containing exactly "</exec>"
```

Rules:

1. Opening and closing markers must each be on their own line.
2. Parse markers only from assistant text, never reasoning or execution results.
3. Allow at most one execution block per assistant response.
4. Allow prose before the opening marker.
5. Require only whitespace after the closing marker.
6. A completed closing marker ends the model action.
7. Never execute code if the response reaches a length/error/abort termination before a complete closing marker.
8. Escape output when formatting observations so printed marker text cannot become executable.

A line-anchored closing marker permits ordinary strings such as `print("</exec>")`. If code genuinely needs a source line containing only `</exec>`, it must construct it dynamically; this is an accepted protocol limitation.

### 9.2 Streaming parser

Implement an incremental state machine:

```text
TEXT
EXEC
COMPLETE
ERROR
```

It must correctly handle markers split across arbitrary provider chunks and `\r\n`/`\n` boundaries. It emits prose and code deltas but retains enough state to validate final completion.

Do not parse `<think>` in the production protocol. OpenAI reasoning is represented by native normalized reasoning events, not text tags.

### 9.3 Parser failures

Malformed output becomes a structured protocol error observation so the model can retry, subject to a retry/turn limit. Examples:

- Closing marker without opening marker
- Second opening marker
- Second execution block
- Non-whitespace trailing text
- Stream ending inside an execution block

## 10. Execution-result formatting

Provider-facing observations should use a stable escaped envelope:

```text
<exec_result id="exec_1" status="ok" kernel_generation="1">
<stream channel="stdout">...</stream>
<stream channel="stderr">...</stream>
<value mime_type="text/plain">...</value>
</exec_result>
```

Requirements:

- XML-escape text fields.
- Preserve output block order.
- Include execution and kernel IDs.
- Distinguish no value from a value whose representation is `None` if the kernel reports one.
- Include structured exception name, message, and sanitized traceback.
- Indicate truncation and artifact location explicitly.
- Keep terminal rendering independent from provider-facing formatting.

Execution-result formatting should be a pure function with golden tests.

## 11. Jupyter kernel

### 11.1 Lifecycle

Use `jupyter_client.AsyncKernelManager` to start and supervise a child `ipykernel` in the session working directory.

The kernel manager must support:

- Start and readiness wait
- One active cell at a time
- Execution message correlation by Jupyter parent message ID
- Interrupt
- Restart
- Shutdown
- Kernel death detection
- Monotonic `kernel_generation` increment on replacement

A child process allows reliable replacement of a damaged interpreter. It does not restrict filesystem, process, or network access.

### 11.2 Output collection

Consume relevant IOPub and shell messages:

```text
stream
execute_result
display_data
error
status
execute_reply
```

Only accept messages correlated to the current execution. Preserve IOPub order.

Initial display support should use `text/plain` only. Record unsupported MIME types in the trace and omit or summarize them in model context. Image support is out of initial scope.

Do not implement last-expression handling by rewriting code to call `print()`. Use the kernel's normal `execute_result` behavior.

### 11.3 Execution semantics

- Persistent globals across cells within one kernel generation
- Normal notebook behavior for the final expression
- Standard stdout/stderr collection
- Default Python logger output captured through stderr
- One cell execution lock
- Configurable timeout
- Cancellation via kernel interrupt
- Forced restart if the kernel does not return to idle after an interrupt grace period

## 12. Agent loop

The loop owns orchestration, not rendering or persistence details.

Pseudo-flow:

```text
append user message
emit user message lifecycle

repeat:
  build transformed provider context
  stream OpenAI assistant response
  parse text into TextBlock/ExecBlock
  finalize and append assistant message

  if provider failed, was aborted, or hit length:
    stop according to error policy

  if no ExecBlock:
    finish run

  execute the cell
  finalize and append ExecutionMessage
  begin next turn
```

Operational limits:

- Maximum automatic turns per user prompt
- Maximum execution cells per run
- Maximum protocol-repair attempts
- Overall optional run deadline

User input while a run is active should initially be rejected with a clear message. Steering and follow-up queues are deferred.

The agent loop emits public events through an event sink. A session wrapper reduces those events into state, writes completed semantic messages to the session store, and forwards events to subscribers.

## 13. Agent and session state

Expose a state object containing:

```text
system_prompt
model
reasoning_level
messages
is_running
streaming_message
active_execution
kernel_generation
last_error
usage_totals
```

State mutations should occur in one reducer/session boundary. Subscribers observe events after the corresponding in-memory state update.

Define clear settlement semantics:

- `agent_end` means the loop will emit no more run events.
- `wait_until_idle()` resolves after final persistence and subscriber work required by the runtime have completed.

## 14. Session JSONL storage

### 14.1 Purpose

The session ledger stores completed semantic history and durable state transitions. It is not a debug event log despite also using a line-oriented format.

### 14.2 Initial entries

```text
session
message
model_change
kernel_restart
todo_change
compaction
session_info
```

Each entry should include:

```text
type
schema_version
id
timestamp
session_id
payload
```

A linear sequence is sufficient initially. Include stable IDs so tree links can be added in a future schema version without redesigning message objects.

### 14.3 Persistence rules

- Append user, assistant, and execution messages only when authoritative and complete.
- Do not persist stream deltas individually.
- Flush after semantic entries needed for recovery.
- Use explicit versioned serialization/deserialization.
- Reject or clearly report unsupported future schema versions.
- Preserve unknown optional fields where practical.
- Never persist API keys or OAuth credentials in the session ledger.
- Keep large execution artifacts outside JSONL and reference them by path/hash.

### 14.4 Resume behavior

On resume:

1. Load semantic transcript and configuration.
2. Start a fresh kernel with a new generation.
3. Do not replay prior cells automatically.
4. Add a model-visible notice that Python memory is empty while filesystem effects may remain.
5. Continue with canonical context reconstructed from session messages.

## 15. `tools` namespace and RPC

### 15.1 Purpose and prompt contributions

Inject a discoverable Python object into each kernel generation:

```python
dir(tools)
help(tools.todo.create)
tools.todo.create("Inspect authentication", status="in_progress")
tools.todo.update("Inspect authentication", "done")
tools.skills.list()
tools.skills.load("pdf-processing")
tools.kernel.restart()
```

Methods must have concrete signatures, type hints, docstrings, and useful return representations. No JSON schemas are sent to the model.

Register namespaces through one small tool registry rather than hand-writing every namespace into the system prompt. Each namespace supplies a stable, short prompt contribution; detailed operation documentation remains available through `help()` and docstrings. Generate the tool section deterministically from enabled namespaces. The todo contribution should be intentionally minimal, approximately:

```text
`tools.todo` maintains a simple task list.
```

Skills add their generated availability section described below in addition to the generic namespace contribution.

### 15.2 Transport

Use a dedicated authenticated local RPC channel separate from stdout/stderr and Jupyter message channels. A localhost TCP server with a random per-kernel token is portable; Unix sockets can be considered later.

Internal JSON framing is acceptable because the no-JSON restriction applies to model-facing tool invocation, not harness internals.

Requirements:

- Request and response IDs
- Method allowlist
- Runtime argument validation
- Structured remote errors
- Independent serving task to prevent deadlocks while awaiting cell completion
- Request timeout
- Trace records with secret redaction
- Clean invalidation when a kernel generation ends

The token prevents accidental external use but is not a security boundary against unrestricted kernel code.

### 15.3 Todo service

The todo service owns a simple ordered mapping keyed by unique task name. The task name is also its task ID; there is no separate generated identifier. A task contains only:

```text
name
status: pending | in_progress | done
```

`pending` is the default and represents a queued task. There is no restriction on the number of `in_progress` tasks.

```text
tools.todo.create(name, status="pending") -> Task
tools.todo.get(name) -> Task
tools.todo.update(name, status) -> Task
tools.todo.list() -> list[Task]
tools.todo.clear() -> None
```

Creating a duplicate name, reading/updating an unknown name, or supplying an invalid status raises a clear remote error. `create`, `get`, and `update` return the resulting task; `list` returns tasks in stable insertion order; `clear` returns `None`. Todo state is owned independently by the service. Changes remain durable `todo_change` session entries and terminal events, but are not automatically separate model messages; the Python return value or exception gives the model immediate feedback.

### 15.4 Skills service

Implement Agent Skills with the same progressive-disclosure behavior as Pi:

1. Discover and validate skills at startup from Pi-compatible global/project locations and any explicit paths configured in `run.py`.
2. Read `name`, `description`, and `disable-model-invocation` from YAML frontmatter. Discover directories containing `SKILL.md`; follow Pi's handling of direct Markdown skill files, diagnostics, duplicate names, ignored files, and relative paths.
3. Add every model-visible skill's escaped name, short description, and file location to the system prompt in the Agent Skills XML format. There is no arbitrary skill-count limit.
4. Do not preload full skill bodies. Load the complete `SKILL.md` dynamically only when selected.
5. Resolve scripts, references, and assets relative to the skill's base directory.
6. Keep skills with `disable-model-invocation: true` out of the system prompt while retaining them for explicit lookup.

Expose the progressive interface through Python:

```text
tools.skills.list() -> list[SkillSummary]
tools.skills.load(name) -> SkillContent
```

`list()` exposes discovered short metadata. `load(name)` returns the full instructions and base-directory information required to resolve relative references. Unknown skill names raise a clear error. Unlike Pi, which can use its file tools to read `SKILL.md`, this harness provides the dedicated namespace because Python execution is its model-facing action mechanism.

### 15.5 Kernel service

```text
tools.kernel.restart()
```

`tools.kernel.restart()` schedules work after the current cell reaches idle. It must not kill the interpreter in the middle of its own RPC call. There is no environment namespace or environment-reload operation.

## 16. Interrupt, restart, and failure handling

### 16.1 User interrupt

`Ctrl+C` behavior:

- While waiting for OpenAI: cancel the request and finalize an aborted assistant message.
- While executing Python: send a kernel interrupt.
- If the kernel does not become idle within the grace period: kill and restart it.
- While idle: exit or require a second interrupt according to the line-interface policy.

### 16.2 Kernel failure

If the kernel exits unexpectedly:

1. Preserve outputs already received.
2. Finalize the execution as `kernel_lost`.
3. Emit `kernel_failed`.
4. Start a new kernel generation when policy allows.
5. Inform the model that in-memory state was lost.

### 16.3 Provider failure

Normalize OpenAI setup, HTTP, stream, timeout, rate-limit, and cancellation failures. Preserve partial assistant content where available. Automatic retry should initially be conservative and must not duplicate an execution.

Never automatically retry a completed or ambiguously completed Python cell. External effects may already have happened.

## 17. Output limits and artifacts

Apply limits independently to:

- Individual stream blocks
- Total output per execution
- Value/display representation
- Trace payload representation
- Provider-facing execution result

When output exceeds the model-context limit:

1. Continue draining kernel output so execution can complete.
2. Write full output to an artifact file.
3. Return bounded head/tail excerpts.
4. Include byte counts, artifact path, and SHA-256.
5. Let the model inspect the artifact with Python if needed.

Store development artifacts under `/tmp/py-code-act/artifacts/<session_id>/`. Paths must not collide across executions. Create the temporary root and artifact directories with user-only permissions where supported. Artifacts are disposable and separate from the durable session JSONL, which stores only their path/hash references.

## 18. Context construction and compaction

### 18.1 Context builder

Before each OpenAI request:

1. Select semantic transcript messages.
2. Apply any compaction checkpoint.
3. Convert internal messages to provider-compatible user/assistant input.
4. Serialize `ExecBlock` and `ExecutionMessage` using stable protocol formatters.
5. Add the stable system prompt.
6. Estimate context size and apply output-specific pruning if required.

### 18.2 Compaction

Implement after persistence and execution are stable.

Manual compaction first:

```text
/compact [optional instructions]
```

A compaction entry should contain:

```text
summary
messages or entry IDs replaced
retained recent tail
usage for summary generation
created_at
```

The summary must distinguish:

- Durable filesystem/process effects observed in outputs
- Historical code that was run
- In-memory Python variables that may still exist in the current kernel
- In-memory state that definitely does not exist after restart/resume

Automatic compaction can later trigger near the configured context limit. It must emit start/end events and remain separate from trace logging.

## 19. System prompt

The stable system prompt must explain:

- Normal prose is used for user-facing answers.
- Python actions use exactly one line-anchored `<exec>` block.
- The model waits for execution output before issuing more code.
- The Python namespace persists within a kernel generation.
- Kernel restart/resume loses ordinary Python variables.
- Filesystem and external side effects may persist.
- `tools` is available without import and can be inspected with `dir()` and `help()`.
- The deterministic tool section is assembled from each enabled namespace's short prompt contribution.
- Every model-visible skill is listed by short metadata, and its complete instructions must be loaded on demand through `tools.skills.load(name)`.
- Output may be truncated to artifacts.
- Malformed blocks are not executed.

Keep the stable prompt deterministic for provider prompt caching. Add project instructions as a separate section. Basic `AGENTS.md` discovery may be implemented without a general extension/resource system.

## 20. Development entrypoint and line interface

There is no command-line argument parser or installed console command initially. The only development launch command is:

```text
uv run run.py
```

Keep `run.py` in the project root, outside package sources. It is a deliberately editable development entrypoint that constructs the complete typed configuration and calls the package API:

```python
import asyncio
from pathlib import Path

from py_code_act.application import run
from py_code_act.config import RunConfig

CONFIG = RunConfig(
    model="gpt-5.6-sol",
    auth_mode="codex",
    cwd=Path(__file__).parent,
    # Session, reasoning, limits, tracing, artifacts, skills, and rendering
    # are also explicitly preconfigured here.
)

if __name__ == "__main__":
    asyncio.run(run(CONFIG))
```

The checked-in file must contain usable values for every non-secret setting so it starts without assembling raw arguments. Changing behavior during development means editing `run.py`. Secrets must still come from `OPENAI_API_KEY` or durable OAuth credential storage, never source code. Remove the placeholder package console entry from `pyproject.toml`; a distributable argument-based CLI can be designed later.

Interactive behavior:

- Complete any required Codex device login before opening the prompt.
- Read one prompt while idle.
- Stream a readable response through the terminal renderer.
- Return to the input prompt when the run settles.
- Support EOF to exit.
- Support `Ctrl+C` cancellation.
- Provide minimal slash commands only when required, initially `/quit`, `/new`, `/session`, `/model`, and `/compact` as they become implemented.

`rich` may be used for colors and block formatting without adopting a full-screen TUI. Output must remain understandable without color.

## 21. Configuration and paths

Initial configuration sources, highest priority first:

1. The typed `RunConfig` constructed in root `run.py`
2. Environment variables used for secrets, currently `OPENAI_API_KEY`
3. Dataclass defaults

OAuth credentials are runtime state, not general configuration. Store them in a user-only file under the `platformdirs` configuration directory.

Use `platformdirs` for:

```text
configuration and OAuth credentials
session data
cache
```

Use fixed development defaults for:

```text
traces:    /tmp/py-code-act/traces/
artifacts: /tmp/py-code-act/artifacts/
```

Durable session JSONL remains in the platform data directory and must not be placed in `/tmp`. Use the configured working directory for executed Python and project instruction/skill discovery.

Provider credentials remain in the harness process. Start the kernel with a sanitized environment that removes known provider credential variables rather than intentionally forwarding them. This reduces accidental disclosure but is not a security guarantee because unrestricted same-user code may still inspect local process or credential sources.

## 22. Dependencies

Planned runtime dependencies:

```text
openai
httpx
jupyter-client
ipykernel
platformdirs
PyYAML
rich
```

Prefer the standard library for dataclasses, JSON, logging, XML escaping, hashing, paths, and asyncio orchestration. Use `httpx` for the Codex OAuth/backend HTTP paths and `PyYAML` for compatible skill frontmatter parsing.

Planned development dependencies:

```text
pytest
pytest-asyncio
ruff
basedpyright or pyright
```

Pin direct dependencies according to project policy once versions are selected. Use uv for dependency and command management.

## 23. Testing strategy

### 23.1 Unit tests

Canonical data:

- Serialization round trips
- Unknown/versioned fields
- ID and correlation propagation
- Usage accumulation

Parser:

- Every possible chunk split across opening and closing markers
- LF and CRLF
- Marker text inside Python strings
- Unexpected close
- Duplicate open/close
- Multiple blocks
- Trailing text
- End inside block
- Provider abort/length inside block
- Reasoning input never parsed as executable code

Formatting:

- XML escaping
- Ordered mixed stdout/stderr/value
- Exceptions
- Empty output
- Truncation references
- Malicious output containing protocol tags

Observability:

- Redaction
- Correlation fields
- Trace ordering
- Trace failure is non-fatal
- Raw provider tracing toggle

Agent loop:

- Text-only completion
- One and multiple sequential cells across turns
- Protocol correction
- Provider failure
- Execution failure
- Turn limits
- Cancellation
- No duplicate execution after failures

Session storage:

- Append/load round trip
- Partial final line recovery policy
- Unsupported schema version
- No deltas/raw provider events persisted
- Resume inserts fresh-kernel notice
- Todo state reconstructs from `todo_change` entries without entering model context

Todo and tool registry:

- Unique task names and the three valid statuses
- Default `pending` status and stable insertion order
- Return values for create/get/update/list/clear
- Duplicate, missing-name, and invalid-status errors
- Deterministic minimal namespace prompt contributions

Skills:

- Pi-compatible discovery locations and ignore behavior
- YAML frontmatter parsing and validation diagnostics
- Duplicate-name precedence
- `disable-model-invocation`
- Escaped prompt metadata for every visible skill with no count limit
- Dynamic full-content loading and base-directory resolution

### 23.2 Kernel integration tests

Run a real local ipykernel and verify:

- Persistent variables
- stdout
- stderr
- Python logging
- Last expression
- `None` behavior
- Exception traceback
- Interleaved outputs
- Display data
- Interrupt
- Timeout and forced restart
- Kernel generation changes
- Working directory
- Sanitized kernel environment
- Tool namespace bootstrap
- Todo calls and skill loading over RPC

### 23.3 Provider tests

Use a fake or mocked OpenAI transport/SDK stream. Do not require paid API calls in the normal test suite.

Verify:

- Public API and Codex request conversion
- No `tools` field/schema is sent by either transport
- Codex device authorization, pending polling, token exchange, cancellation, and expiry
- OAuth token refresh, account-ID extraction, credential permissions, and redaction
- Stream normalization
- Usage
- Errors and partial output
- Cancellation
- Reasoning events
- Sanitized raw tracing
- Assistant/execution transcript replay

### 23.4 Scripted end-to-end tests

Use a deterministic fake provider that emits predefined deltas. Exercise:

```text
root `run.py`/session input
→ agent loop
→ parser
→ real or fake kernel
→ execution result
→ second provider response
→ final session entries and events
```

Keep live OpenAI smoke tests optional and explicitly enabled through an environment flag.

## 24. Implementation phases

### Phase 0: Repository foundation

- Remove the placeholder package console script and add the root `run.py` development entrypoint.
- Define the typed `RunConfig` and package-level application function called by `run.py`.
- Preconfigure every non-secret development setting, including `gpt-5.6-sol`, Codex auth, `/tmp` traces/artifacts, durable sessions, skills, limits, and rendering.
- Add source/test subpackages.
- Configure pytest, Ruff, and static type checking.
- Add runtime and development dependencies through uv.

Acceptance criteria:

- `uv run run.py` reaches authentication or starts the interactive line interface without arguments.
- No argument parser or installed `py-code-act` console entry exists.
- Lint, type check, and the empty test suite run through documented uv commands.

### Phase 1: Canonical data and provider boundary

- Implement IDs, usage, model config, content blocks, messages, execution outputs, and serialization.
- Define provider request/event protocol.
- Add unit tests for all serializable domain objects.

Acceptance criteria:

- No OpenAI SDK type crosses the provider boundary.
- Domain objects round-trip through explicit JSON-compatible serialization.

### Phase 2: Events, tracing, and terminal renderer

- Implement public event envelopes and sequence generation.
- Implement internal trace recorder and redaction.
- Implement basic terminal event rendering.
- Wire trace options from `RunConfig` and create user-only `/tmp/py-code-act` paths.

Acceptance criteria:

- A scripted event stream renders readably and produces an inspectable `.log`.
- Trace and terminal output are independent sinks.
- Secrets are redacted in tests.

### Phase 3: OpenAI text-only vertical slice

- Implement public OpenAI Responses API and Codex backend request conversion and streaming normalization.
- Implement Codex device-code login, durable credential storage, account-ID extraction, and refresh.
- Add a minimal one-turn runner.
- Connect the root runner's line input, OpenAI streaming, terminal rendering, and trace recording.
- Add cancellation and normalized provider/authentication errors.

Acceptance criteria:

- With `auth_mode="api_key"`, a user can run a text-only conversation using `OPENAI_API_KEY`.
- With `auth_mode="codex"`, missing credentials trigger headless login and a text-only conversation can use the stored/refreshed credential.
- Raw sanitized and normalized events are visible in the trace.
- Neither transport sends a tool schema.

### Phase 4: Streaming execution protocol

- Implement the strict incremental parser.
- Convert streamed text into prose and execution-code deltas.
- Finalize structured assistant content.
- Add exhaustive parser tests and golden execution-result formatter tests.

Acceptance criteria:

- Arbitrary provider chunking cannot cause premature execution.
- Malformed/incomplete blocks are never executed.

### Phase 5: Persistent Jupyter kernel

- Implement kernel startup, execution, output collection, and shutdown.
- Implement structured outputs and execution result building.
- Add real-kernel integration tests.

Acceptance criteria:

- Variables persist across cells.
- stdout/stderr/logger output and the last expression are distinct and ordered.
- Exceptions produce structured results without crashing the harness.

### Phase 6: Full agent loop

- Integrate OpenAI, parser, kernel, context conversion, and repeated turns.
- Implement run/turn limits and failure handling.
- Emit complete public event sequences.

Acceptance criteria:

- A model can inspect and modify a repository using sequential Python cells.
- Execution observations are fed back to OpenAI as synthetic user messages.
- Text-only responses terminate the run cleanly.

### Phase 7: Durable session JSONL

- Implement versioned semantic entries and append/load behavior.
- Connect final message and kernel-state persistence.
- Implement new/resume/ephemeral-session behavior configured by `RunConfig` and interactive commands.
- Keep trace logs completely independent.

Acceptance criteria:

- Sessions resume conversation history.
- Resume starts a fresh kernel and tells the model state was lost.
- Session JSONL contains no stream deltas or raw provider events.

### Phase 8: Harness tools RPC and skills

- Implement the local RPC server, namespace registry, prompt-contribution interface, and kernel-side proxy/bootstrap.
- Implement the simple todo service and kernel restart control; do not add an environment namespace.
- Implement Pi-style skill discovery, prompt summaries, and dynamic `tools.skills.load()`.
- Persist todo and restart state transitions.
- Add unit and integration tests.

Acceptance criteria:

- `tools.todo.create(...)`, `get`, `update`, `list`, and `clear` have the specified minimal state and return behavior.
- `help(tools.todo.create)` is useful while todo's system-prompt contribution stays minimal.
- Every visible discovered skill is summarized in the prompt, and its full instructions load only on demand.
- Restart requests happen after cell completion without deadlock.

### Phase 9: Operational robustness

- Implement execution timeout, interrupt grace period, forced restart, output limits, artifacts, and kernel-death recovery.
- Add failure-injection tests.

Acceptance criteria:

- Infinite Python code can be interrupted.
- An unresponsive kernel can be replaced without losing session history.
- Large output does not flood model context or the terminal irrecoverably.

### Phase 10: Context management

- Implement context estimation and bounded output conversion.
- Add manual compaction and compaction entries.
- Add automatic compaction only after manual behavior is reliable.

Acceptance criteria:

- Long sessions remain within configured model context limits.
- Compaction preserves recent actionable state and does not falsely promise restored kernel memory.

### Phase 11: Line-interface refinement

- Improve readable stream composition, prompts, usage display, session commands, and shutdown behavior.
- Keep the interface line-oriented and startup configuration in root `run.py`; do not add raw arguments.
- Document operation, authentication, security model, session files, temporary traces/artifacts, skills, and troubleshooting.

Acceptance criteria:

- `uv run run.py` is usable for normal repository work without a full-screen TUI.
- Terminal output remains readable when redirected or color is disabled.

## 25. Cross-cutting acceptance criteria

The initial implementation is complete when:

1. `uv run run.py` starts a preconfigured line-oriented OpenAI session without raw arguments.
2. Both `OPENAI_API_KEY` and refreshable OpenAI Codex device-code credentials are supported.
3. No JSON tool schemas or native tool calls are sent to either OpenAI transport.
4. Complete line-anchored `<exec>` blocks run in a persistent child Python kernel.
5. The model receives ordered, escaped, bounded execution observations.
6. Assistant prose, Python code, output, results, and errors render readably in the terminal.
7. A separate trace `.log` under `/tmp/py-code-act/traces` exposes provider, parser, agent, kernel, and persistence activity with correlation IDs.
8. Session JSONL remains durable outside `/tmp`, stores only semantic history, and can resume with an explicitly fresh kernel.
9. The minimal `tools.todo`, progressive `tools.skills`, and kernel restart control work through dedicated RPC.
10. Cancellation, kernel replacement, output truncation, and temporary artifacts work predictably.
11. The normal automated test suite uses no paid provider calls.
12. OpenAI public API and OpenAI Codex are the only provider transports in the repository.

## 26. Security statement

The model-generated Python runs with the operating-system permissions of the user running `uv run run.py`. The child kernel is not a sandbox. It can read and modify files, start processes, access the network, and potentially inspect local credentials or interfere with the harness.

Process separation exists so the kernel can be interrupted and replaced; it does not provide isolation. This must be stated prominently in the README and interactive startup behavior before the agent is presented as generally usable.
