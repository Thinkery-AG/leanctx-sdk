# LeanCTX SDK

Build Python coding agents that read, search, edit, and run approved commands
without sending raw repository output to the model every time.

Your framework owns the model and agent loop. LeanCTX owns the local context
tools, compression, cache, permissions, token measurements, and recovery path.

## Choose the right product

| You want to… | Use |
| --- | --- |
| improve an existing coding agent through CLI/MCP | LeanCTX Engine |
| build your own Python agent with LeanCTX tools | LeanCTX SDK + Engine |
| keep your own model/framework but add governed context | `AgentContext` |

The SDK does not contain a second implementation of the Engine. It starts one
verified local Engine process and exposes its negotiated capabilities as stable
Python methods.

```text
your model / agent loop
          ↓
AgentContext or AsyncAgentContext
          ↓  versioned local Agent Tools Interface
LeanCTX Engine 3.10.1
          ↓
project-jailed files, cache, search, patches, approved commands
```

## Install

The 1.1 source tree is a release candidate until the exact Engine 3.10.1
companion wheels are published. The release gate must not publish the SDK first.
After both artifacts are available, the one-command install is:

Standard Engine:

```bash
python -m pip install "thinkery-leanctx-sdk[agent]==1.1.0"
```

With the certified OpenAI Agents integration:

```bash
python -m pip install "thinkery-leanctx-sdk[agent,openai-agents]==1.1.0"
```

CUDA and Windows-GNU builds use the documented `agent-cuda` and
`agent-windows-gnu` extras. The core SDK remains pure Python.

## Five-minute custom agent

```python
from leanctx_sdk import AgentContext


def my_model(task: str, context: str) -> str:
    # Replace with any model or framework call.
    return f"{task}\n\nRelevant project context:\n{context}"


with AgentContext(".", task="Explain the public API") as ctx:
    files = ctx.tree(depth=2)
    matches = ctx.search("class AgentContext", path="src")
    source = ctx.read("src/leanctx_sdk/agent.py", mode="signatures")
    answer = my_model(ctx.task, "\n".join((files.text, matches.text, source.text)))
    print(answer)
    print(f"saved tokens: {ctx.metrics.saved_tokens}")
```

Default permissions are read-only. A coding agent must opt in explicitly:

```python
from leanctx_sdk import AgentContext, AgentPermissions, ExecutionPolicy

with AgentContext(
    ".",
    permissions=AgentPermissions(write=True, execute=True),
    execution_policy=ExecutionPolicy(allowed_executables=("git", "pytest")),
) as ctx:
    ctx.replace_unique("app.py", "old_name", "new_name")
    tests = ctx.run(("pytest", "-q"), timeout=30)
```

The permission policy is immutable for the session and is enforced again by
the Engine. `call()` cannot bypass it, and process tools must use `run(argv)`.

## SDK 1.1 Agent Tools capabilities

- `read`, `search`, `glob`, `tree`, `compose`, and `symbol`
- safe `create_file`, `patch`, and `replace_unique`
- allowlisted argv execution with compressed output
- persistent per-agent cache and aggregate token measurements
- synchronous and asynchronous APIs
- capability negotiation and typed fail-closed errors
- optional OpenAI Agents 0.8.4 function tools

The SDK supplies the tool substrate, not an autonomous planner, model, hosted
service, or universal quality guarantee. Savings are measured per call against
the Engine's raw-output baseline; they are not a promise for every workload.

## Compatibility

The five SDK 1.0 lifecycle primitives remain available unchanged:
`ContextSession`, `ContextSource`, `ContextView`, `ContextPlan`, and
`ContextReceipt`. `AgentContext` requires Agent Tools Interface v1 from Engine
3.10.1; the older context-view/recover Engine Interface v1 remains unchanged.

See:

- [Custom agents](docs/CUSTOM-AGENTS.md)
- [Quickstart](docs/QUICKSTART.md)
- [Compatibility](COMPATIBILITY.md)
- [Security](SECURITY.md)
- [Errors](docs/ERRORS.md)
- [Migration](MIGRATION.md)
- [Stable public surface](PUBLIC-SURFACE-MANIFEST.md)

## License

LeanCTX SDK is source-available. Commercial Production Use, OEM embedding, and
commercial redistribution require a written agreement with Thinkery AG.
LeanCTX Engine and its companion binary distributions remain Apache-2.0.
