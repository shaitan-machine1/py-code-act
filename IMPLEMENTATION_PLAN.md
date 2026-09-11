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
- OpenAI Codex/ChatGPT subscription authentication using both manual browser OAuth and a headless device-code flow
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
- Interrupt, timeout, restart, and kernel-loss recovery
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
- OAuth and subscription authentication for providers other than OpenAI Codex/ChatGPT
- Output truncation and execution-output artifacts
- Manual or automatic context compaction
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
ProtocolErrorMessage
KernelNoticeMessage
```

`ProtocolErrorMessage` is a model-visible transcript message used when malformed or truncated assistant output was not executed. Suggested fields are `id`, `error`, `message`, `assistant_message_id`, and `created_at`. It is converted to a synthetic user observation but is not represented as a fake kernel execution.

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

`provider_metadata` must be an explicitly serializable opaque mapping used only when OpenAI requires replay data. It must not contain SDK objects or credentials. Match Pi's stateless OpenAI replay behavior: a `ReasoningBlock` keeps its displayable summary/text together with the complete serializable OpenAI reasoning output item, including encrypted continuation content when returned. Replay data remains opaque outside the OpenAI adapter.

### 5.3 Execution output blocks

```text
StreamOutput(channel="stdout" | "stderr", text=...)
ValueOutput(mime_type="text/plain", data=...)
DisplayOutput(mime_type="text/plain", data=...)
ErrorOutput(name=..., message=..., traceback=...)
```

An `ExecutionMessage` should contain:

```text
id
exec_id
kernel_generation
status: ok | error | interrupted | timed_out | kernel_lost
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
message_text_delta* (prefix)
exec_code_delta*
message_text_delta* (suffix)
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

A protocol-repair interaction has no execution lifecycle because no Python runs:

```text
message_end(assistant)
message_start(protocol_error)
message_end(protocol_error)
turn_end
turn_start
message_start(assistant)
...
```

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
- `codex`: use a ChatGPT OAuth access token with `https://chatgpt.com/backend-api/codex/responses`. Match the request shape and required headers used by Pi's OpenAI Codex transport, including `Authorization`, `chatgpt-account-id`, `originator`, and the Responses beta header. Keep `originator` set to `pi` initially.

The initial Codex transport uses SSE only. Keep SSE/WebSocket mechanics inside the provider transport boundary so WebSocket support can be added later without changing the agent, protocol, persistence, or rendering layers.

Neither transport sends native tool definitions or a `tools` field.

### 8.2 Codex authentication

Copy Pi's OpenAI Codex OAuth protocol, constants, endpoints, PKCE behavior, token exchange, refresh behavior, account-ID extraction, and validation as closely as practical. Support two configured login methods when no valid credential exists:

- `browser`: generate Pi's authorization URL and PKCE/state values, print the URL, and prompt the user to paste the full callback URL from the browser address bar after authorization. Do not start a local callback server. Parse the authorization code and state from the pasted URL, reject a state mismatch, and exchange the code using Pi's redirect URI.
- `device_code`: copy Pi's headless flow. Request a device authorization, print the verification URL and user code, poll using the server-provided interval until authorization, expiry, failure, or cancellation, then exchange the returned authorization code and PKCE verifier.

Both methods then:

1. Store access and refresh tokens and their expiry.
2. Extract and store the ChatGPT account ID from the access-token JWT.
3. Refresh expired access tokens automatically and update the stored account ID.

`RunConfig.auth_mode` selects `api_key` or `codex`; a separate Codex login-method setting selects `browser` or `device_code` for first login. When `auth_mode="codex"` is configured and no valid credential exists, `uv run run.py` starts the configured login flow before opening the agent session. Store the resulting OAuth credential in the application configuration directory selected by `platformdirs`, in a file created with user-only permissions. The credential is never written to the session ledger, trace, generated `run.py`, or kernel environment.

Authentication failures, malformed token responses, state mismatch, cancellation, and browser/device-code expiry must produce clear terminal errors and sanitized traces. The Codex endpoints and client protocol are implementation details isolated in the auth/provider packages because they may change independently of the public OpenAI API.

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
- Serialize `ExecutionMessage` and `ProtocolErrorMessage` as synthetic user observations.
- Preserve and replay complete opaque OpenAI reasoning output items, including encrypted continuation content, in the same manner as Pi.
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
assistant-response := final-text | prefix-text exec-block suffix-text
exec-block         := OPEN_LINE python-code CLOSE_LINE
OPEN_LINE          := line containing exactly "<exec>"
CLOSE_LINE         := line containing exactly "</exec>"
```

Rules:

1. Opening and closing markers must each be on their own line.
2. Parse markers only from assistant text, never reasoning or execution results.
3. Allow at most one execution block per assistant response.
4. Allow prose before and after the execution block.
5. A completed closing marker ends the Python block, not the assistant response; continue consuming the provider stream through its terminal event.
6. Never execute code from a response whose normalized stop reason is `length`, `error`, or `aborted`, even if a complete block appears to have arrived. This matches Pi's conservative handling of tool calls from truncated responses.
7. Execute only after the complete provider response is authoritative and the parser has confirmed exactly one complete execution block.
8. Escape output when formatting observations so printed marker text cannot become executable.

A line-anchored closing marker permits ordinary strings such as `print("</exec>")`. If code genuinely needs a source line containing only `</exec>`, it must construct it dynamically; this is an accepted protocol limitation.

### 9.2 Streaming parser

Implement an incremental state machine:

```text
TEXT
EXEC
AFTER_EXEC
ERROR
```

It must correctly handle markers split across arbitrary provider chunks and `\r\n`/`\n` boundaries. It emits prose before and after the block as text deltas and emits code deltas while inside the block, but retains enough state to validate final completion. Seeing `</exec>` never cancels the provider request or starts execution early.

Do not parse `<think>` in the production protocol. OpenAI reasoning is represented by native normalized reasoning events, not text tags.

### 9.3 Parser failures

Malformed or unsafe-to-execute output becomes a `ProtocolErrorMessage` so the model can retry, subject to a retry/turn limit. It uses normal message lifecycle events and does not emit `exec_start` or `exec_end`, because no kernel execution occurred. Examples:

- Closing marker without opening marker
- Second opening marker
- Second execution block
- Stream ending inside an execution block
- A complete-looking execution block in a response that terminates with `length`

Ordinary prose after a single complete block is valid and remains part of the assistant message.

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
  stream and fully drain the OpenAI assistant response
  parse text into TextBlock/ExecBlock
  finalize and append the authoritative assistant message

  if provider failed or was aborted:
    stop according to error policy

  if the response hit length:
    never execute its code
    append a ProtocolErrorMessage and retry when an execution block needs repair
    otherwise stop according to error policy

  if parsing failed:
    append a ProtocolErrorMessage and retry within the repair limit

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
- Persist complete execution output directly in the semantic message; output artifacts are deferred.

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

Implement Agent Skills with the same progressive-disclosure behavior as Pi, without adding Pi's project-trust gate yet:

1. Discover and validate skills at startup from Pi-compatible global locations (`~/.pi/agent/skills/` and `~/.agents/skills/`), the working directory's `.pi/skills/`, `.agents/skills/` in the working directory and its ancestors up to the Git repository root (or filesystem root outside a repository), and any explicit paths configured in `run.py`. Follow Pi's source precedence and collision behavior, with explicit configured paths taking precedence.
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

`tools.kernel.restart()` schedules work after the current cell reaches idle. It must not kill the interpreter in the middle of its own RPC call. The RPC returns and the remainder of the current cell runs in the old generation. That execution finishes normally under the old generation; the harness then replaces the kernel, increments `kernel_generation`, persists the restart, and appends a model-visible `KernelNoticeMessage` before the next provider request. `kernel_restart_requested` is emitted when the RPC request is accepted, while `kernel_restarted` is emitted only after replacement succeeds. There is no environment namespace or environment-reload operation.

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

## 17. Output handling

Do not truncate execution output or create execution-output artifacts in the initial implementation. Preserve and render all ordered output blocks and send the complete escaped result to the model. Output limits, spill-to-artifact behavior, and bounded terminal rendering are deferred explicitly.

The development trace remains disposable and may contain full prompts, generated code, file content, and execution output.

## 18. Context construction

Before each OpenAI request:

1. Select semantic transcript messages.
2. Convert internal messages to provider-compatible user/assistant input.
3. Serialize `ExecBlock`, `ExecutionMessage`, `ProtocolErrorMessage`, and `KernelNoticeMessage` using stable protocol formatters.
4. Add the stable system prompt.
5. Estimate context size where practical and report a clear context-overflow failure instead of silently pruning or compacting history.

Manual and automatic compaction are deferred and have no commands, session entries, or runtime events in the initial implementation.

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
- Execution output is returned without initial truncation.
- Malformed or truncated-response blocks are not executed.

Keep the stable prompt deterministic for provider prompt caching. Add project instructions as a separate section. Implement Pi-style context-file discovery without a general extension/resource system: load the global context file from the platform configuration directory, then walk from the filesystem root toward the configured working directory so nearer instructions layer later. In each directory, use Pi's candidate precedence: `AGENTS.override.md`, `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, then `CLAUDE.MD`, loading at most one. Project trust gating is deferred.

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
    codex_login_method="browser",
    cwd=Path(__file__).parent,
    session_path=Path(".py-code-act/session.jsonl"),
    # Reasoning, limits, tracing, skills, and rendering are also explicitly
    # preconfigured here.
)

if __name__ == "__main__":
    asyncio.run(run(CONFIG))
```

The checked-in file must contain usable values for every non-secret setting so it starts without assembling raw arguments. Changing behavior during development means editing `run.py`. Secrets must still come from `OPENAI_API_KEY` or durable OAuth credential storage, never source code. Remove the placeholder package console entry from `pyproject.toml`; a distributable argument-based CLI can be designed later.

Interactive behavior:

- Complete any required configured Codex browser or device-code login before opening the prompt.
- Read one prompt while idle.
- Stream a readable response through the terminal renderer.
- Return to the input prompt when the run settles.
- Support EOF to exit.
- Support `Ctrl+C` cancellation.
- Provide minimal slash commands only when required, initially `/quit`, `/new`, `/session`, and `/model` as they become implemented.

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

Use a fixed development default for traces:

```text
traces: /tmp/py-code-act/traces/
```

`RunConfig.session_path` selects the durable JSONL ledger directly. Create it and its parent directory on first use; if it already exists, resume it with a fresh kernel. Do not add startup session selection logic to the development runner. A checked-in development default may be project-relative, but durable sessions must not be placed in `/tmp`. Use the configured working directory for executed Python and project instruction/skill discovery.

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
- Prose after one complete block
- End inside block
- Provider abort/length with incomplete and complete-looking blocks
- No execution for any `length` response
- Reasoning input never parsed as executable code

Formatting:

- XML escaping
- Ordered mixed stdout/stderr/value
- Exceptions
- Empty output
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
- Protocol correction through persisted `ProtocolErrorMessage` values
- Provider failure
- Complete-looking execution withheld after a `length` stop
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

- Pi-compatible discovery locations and ignore behavior without trust gating
- YAML frontmatter parsing and validation diagnostics
- Duplicate-name precedence, including explicit configured paths
- `disable-model-invocation`
- Escaped prompt metadata for every visible skill with no count limit
- Dynamic full-content loading and base-directory resolution

Context files:

- Pi-compatible global and root-to-working-directory ordering
- Candidate precedence and `AGENTS.override.md` behavior
- At most one context file loaded from each directory

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
- Codex manual browser authorization URL/callback parsing, PKCE, state validation, token exchange, and cancellation
- Codex device authorization, pending polling, token exchange, cancellation, and expiry
- OAuth token refresh, account-ID extraction, credential permissions, and redaction
- SSE-only initial Codex streaming with transport details isolated behind the provider boundary
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
- Preconfigure every non-secret development setting, including `gpt-5.6-sol`, Codex auth and login method, `/tmp` traces, a direct durable session path, skills, limits, and rendering.
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

- Implement public OpenAI Responses API and SSE-only Codex backend request conversion and streaming normalization.
- Implement Codex manual browser and headless device-code login, durable credential storage, account-ID extraction, and refresh by following Pi's OAuth protocol.
- Add a minimal one-turn runner.
- Connect the root runner's line input, OpenAI streaming, terminal rendering, and trace recording.
- Add cancellation and normalized provider/authentication errors.

Acceptance criteria:

- With `auth_mode="api_key"`, a user can run a text-only conversation using `OPENAI_API_KEY`.
- With `auth_mode="codex"`, missing credentials trigger the configured browser or headless login and a text-only conversation can use the stored/refreshed credential.
- Raw sanitized and normalized events are visible in the trace.
- Neither transport sends a tool schema.

### Phase 4: Streaming execution protocol

- Implement the strict incremental parser.
- Convert streamed text into prose and execution-code deltas.
- Finalize structured assistant content.
- Add exhaustive parser tests and golden execution-result formatter tests.

Acceptance criteria:

- Arbitrary provider chunking cannot cause premature execution.
- The provider stream is drained through its terminal event after `</exec>` arrives.
- Prose after one complete execution block is accepted.
- Malformed/incomplete blocks and all blocks from `length` responses are never executed.

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
- Create or resume the directly configured `RunConfig.session_path`; start a fresh kernel when resuming an existing ledger.
- Keep trace logs completely independent.

Acceptance criteria:

- Sessions resume conversation history.
- Resume starts a fresh kernel and tells the model state was lost.
- Session JSONL contains no stream deltas or raw provider events.

### Phase 8: Harness tools RPC and skills

- Implement the local RPC server, namespace registry, prompt-contribution interface, and kernel-side proxy/bootstrap.
- Implement the simple todo service and kernel restart control; do not add an environment namespace.
- Implement Pi-style skill discovery, prompt summaries, and dynamic `tools.skills.load()` without project trust gating.
- Implement Pi-style context-file discovery and deterministic project-instruction prompt composition.
- Persist todo and restart state transitions.
- Add unit and integration tests.

Acceptance criteria:

- `tools.todo.create(...)`, `get`, `update`, `list`, and `clear` have the specified minimal state and return behavior.
- `help(tools.todo.create)` is useful while todo's system-prompt contribution stays minimal.
- Every visible discovered skill is summarized in the prompt, and its full instructions load only on demand.
- Restart requests happen after cell completion without deadlock, preserve the old generation's completed execution, and append a fresh-generation model notice before another provider request.

### Phase 9: Operational robustness

- Implement execution timeout, interrupt grace period, forced restart, and kernel-death recovery.
- Add failure-injection tests.

Acceptance criteria:

- Infinite Python code can be interrupted.
- An unresponsive kernel can be replaced without losing session history.
- Kernel replacement produces explicit generation changes and model-visible state-loss notices.

### Phase 10: Line-interface refinement

- Improve readable stream composition, prompts, usage display, session commands, and shutdown behavior.
- Keep the interface line-oriented and startup configuration in root `run.py`; do not add raw arguments.
- Document operation, authentication, security model, session files, temporary traces, skills, deferred output limits/compaction, and troubleshooting.

Acceptance criteria:

- `uv run run.py` is usable for normal repository work without a full-screen TUI.
- Terminal output remains readable when redirected or color is disabled.

## 25. Cross-cutting acceptance criteria

The initial implementation is complete when:

1. `uv run run.py` starts a preconfigured line-oriented OpenAI session without raw arguments.
2. Both `OPENAI_API_KEY` and refreshable OpenAI Codex credentials obtained through manual browser or headless device-code login are supported.
3. No JSON tool schemas or native tool calls are sent to either OpenAI transport.
4. Complete line-anchored `<exec>` blocks run in a persistent child Python kernel.
5. The model receives complete ordered and escaped execution observations.
6. Assistant prose, Python code, output, results, and errors render readably in the terminal.
7. A separate trace `.log` under `/tmp/py-code-act/traces` exposes provider, parser, agent, kernel, and persistence activity with correlation IDs.
8. Session JSONL remains durable outside `/tmp`, stores only semantic history, and can resume with an explicitly fresh kernel.
9. The minimal `tools.todo`, progressive `tools.skills`, and kernel restart control work through dedicated RPC.
10. Cancellation, deferred restart requests, and forced kernel replacement work predictably.
11. The normal automated test suite uses no paid provider calls.
12. OpenAI public API and OpenAI Codex are the only provider transports in the repository.

## 26. Security statement

The model-generated Python runs with the operating-system permissions of the user running `uv run run.py`. The child kernel is not a sandbox. It can read and modify files, start processes, access the network, and potentially inspect local credentials or interfere with the harness.

Process separation exists so the kernel can be interrupted and replaced; it does not provide isolation. This must be stated prominently in the README and interactive startup behavior before the agent is presented as generally usable.
