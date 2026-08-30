# Build custom agents with LeanCTX

`AgentContext` is the tool layer for a host-owned agent loop. It keeps one local
Engine process alive, so reads share cache state and unchanged re-reads can use
smaller delta output.

## API

| Method | Purpose | Permission |
| --- | --- | --- |
| `read(path, mode)` | compressed or exact file view | read-only |
| `search(pattern, …)` | bounded code search | read-only |
| `glob(pattern, …)` | file discovery | read-only |
| `tree(path, …)` | repository map | read-only |
| `compose(task, …)` | task-ranked context | read-only |
| `symbol(name)` | symbol lookup | read-only |
| `create_file`, `patch`, `replace_unique` | safe Engine-backed edits | `write=True` |
| `run(argv, …)` | allowlisted command with compressed output | `execute=True` |
| `call(tool, arguments)` | negotiated advanced read/write tool | tool policy |
| `cancel()` | terminate an in-flight process without retry | none |
| `reconnect()` | return a fresh process with the same immutable policy | none |

Every call returns `ToolResult`. Its `text` is suitable for a model; token
fields and `AgentContext.metrics` are factual Engine measurements. Shell state
is available through `ToolResult.shell` without parsing terminal text. Image
and other non-text MCP payloads are preserved in `ToolResult.content_blocks`.

## Framework-neutral loop

```python
from leanctx_sdk import AgentContext


def run_agent(model, task: str):
    with AgentContext(".", task=task) as ctx:
        orientation = ctx.compose()
        result = model(task=task, context=orientation.text, tools={
            "read": ctx.read,
            "search": ctx.search,
            "tree": ctx.tree,
        })
        return result, ctx.metrics
```

LeanCTX never selects the model, retries the model, or decides when the task is
complete. Those decisions stay in the host.

## OpenAI Agents

The adapter is certified for exactly `openai-agents==0.8.4`.

```python
from agents import Agent, Runner
from leanctx_sdk import AgentContext
from leanctx_sdk.integrations.openai_agents import openai_tools

with AgentContext(".", task="Explain the configuration") as ctx:
    agent = Agent(
        name="Repository assistant",
        instructions=ctx.task,
        tools=openai_tools(ctx),
    )
    result = Runner.run_sync(agent, input="Inspect the project and answer concisely.")
    print(result.final_output)
```

Provider credentials and returned framework objects remain host-owned.

## Safe coding mode

```python
from leanctx_sdk import AgentContext, AgentPermissions, ExecutionPolicy

permissions = AgentPermissions(write=True, execute=True)
commands = ExecutionPolicy(
    max_timeout=60,
    allowed_executables=("git", "pytest"),
    allowed_env=("CI",),
)

with AgentContext(".", permissions=permissions, execution_policy=commands) as ctx:
    anchored = ctx.read("module.py", mode="anchored")
    # An agent can pass anchors from that view to ctx.patch(...).
    ctx.replace_unique("module.py", "return False", "return True")
    result = ctx.run(("pytest", "-q"))
    if result.shell and result.shell.get("exitCode", 0) != 0:
        raise RuntimeError(result.text)
```

Write and execute rights cannot be added to an existing context. Create a new
context after an explicit host authorization decision. Execute permission
requires a non-empty executable allowlist. Environment variables are rejected
unless named in `allowed_env`; the Engine validates argv, executable, env, and
timeout again before constructing its internal shell command.

## Failure behavior

- incompatible handshake or malformed result → `EngineProtocolError`
- missing capability → `UnsupportedCapabilityError`
- denied write/execute operation → `AgentPermissionError`
- process exit → `EngineCrashed`; no mutation is retried
- deadline exceeded → `EngineTimeout` and the process is terminated

Create a new context after a crash or timeout. Cache state is intentionally
process-local; `reconnect()` is explicit and does not claim to recover that
cache. Async task cancellation terminates the process before propagating
`CancelledError`. Durable Preview workspaces and checkpoints remain separate.

## Reproduce the Agent Tools evidence

Use an Engine 3.10.1 binary; both commands are provider-free and make no
network calls:

```bash
PYTHONPATH=src:. python scripts/verify_agent_context_e2e.py \
  --engine /path/to/lean-ctx --expected-engine-version 3.10.1
PYTHONPATH=src:. python -m benchmarks.agent_tools.retrieval_benchmark \
  --engine /path/to/lean-ctx
```

The end-to-end gate exercises persistent read, search, tree, compose, create,
replace, and structured-argv execution through the real binary. The benchmark
requires exact-answer parity between direct full-file context and LeanCTX search
context, denies Python-side network access, repeats the run three times, and
fails unless deterministic median context-input savings are at least 30%.

The raw baseline uses direct file content and the Engine's original-token count;
the LeanCTX lane uses returned search context. This controlled three-task
fixture does not claim provider billing savings, production-workload coverage,
or that every agent workload will achieve the same ratio.
