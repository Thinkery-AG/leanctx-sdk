# LeanCTX SDK

**Embed LeanCTX context control into your application.**

LeanCTX is the **Context Gateway for AI Systems**. **Control what your AI can see.**
The SDK lets an application select task-relevant context, apply the controls
supported by its integration, and inspect the resulting output and evidence.
Your application keeps its model, workflow, and user interface.

## SELECT → CONTROL → PROVE

- **Select:** gather relevant context with supported search, read, tree, compose,
  and lifecycle APIs.
- **Control:** apply the permissions and command policy available in the chosen
  integration and Engine release.
- **Prove:** inspect returned results and measurements; the stable lifecycle also
  exposes receipts. A context-only integration cannot observe what the host later
  sends to its model.

## Choose your path

- **Coding agent:** use Stable `AgentContext` or `AsyncAgentContext` for local
  context tools with explicit permissions; your host keeps the model and agent
  loop.
- **Enterprise copilot:** use only the five Stable lifecycle primitives:
  `ContextSession`, `ContextSource`, `ContextView`, `ContextPlan`, and
  `ContextReceipt`.
- **Product builder:** embed the SDK and Engine in your application. The source
  license permits non-production use; production use, OEM embedding, and
  commercial redistribution require a written agreement signed by Thinkery AG.

Build with the SDK when your application needs its own LeanCTX integration. Use
the Engine's CLI or MCP integration directly when an existing supported agent
already provides the context tools you need.

The SDK does not contain a second implementation of the Engine. It starts one
verified local Engine process and exposes its negotiated capabilities as stable
language-native methods.

```text
your model / agent loop
          ↓
AgentContext or AsyncAgentContext
          ↓  versioned local Agent Tools Interface
LeanCTX Engine (release-specific; see [Compatibility](COMPATIBILITY.md))
          ↓
project-jailed files, cache, search, patches, approved commands
```

## Install

The public SDK 1.1.0 release uses Engine 3.10.1 and its companion wheels. The
current `main` source targets Engine 3.10.5. Install the public Python SDK and
Engine together with:

> These commands install the published SDK 1.1.0, which requires Engine
> 3.10.1. Current `main` sources target Engine 3.10.5; package version metadata
> alone does not identify a published artifact. See
> [COMPATIBILITY.md](COMPATIBILITY.md#main-unreleased).

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

## Language SDKs

All six language SDKs implement the five Stable lifecycle primitives and the
separate Stable Agent Tools surface. The table describes current `main` source
compatibility. The published SDK 1.1.0 release requires Engine 3.10.1; current
`main` targets Engine 3.10.5. Registry publication is per artifact and must be
verified at that registry.

| Runtime | Package source | Package identity |
| --- | --- | --- |
| Python 3.9–3.14 | repository root | `thinkery-leanctx-sdk` |
| Node.js 22+ / TypeScript | `packages/typescript` | `@thinkery/leanctx-sdk` |
| Go 1.24+ | `packages/go` | `github.com/Thinkery-AG/leanctx-sdk/packages/go` |
| Rust 1.77+ | `packages/rust` | `thinkery-leanctx-sdk` |
| Java 21 / Kotlin 2.1 | `packages/jvm` | `com.leanctx:leanctx-sdk` |
| .NET 8+ | `packages/dotnet` | `Thinkery.LeanCtx` |

Each package follows the shared protocol and release gate. The Engine remains a
separate local binary. Python's Preview workspace APIs live only under
`leanctx_sdk.preview`; they are outside the Stable v1 guarantee.

Registry releases use language-scoped tags from the same commit:
`packages/typescript/vX.Y.Z`, `packages/go/vX.Y.Z`,
`packages/rust/vX.Y.Z`, `packages/jvm/vX.Y.Z`, and
`packages/dotnet/vX.Y.Z`. See
[`docs/LANGUAGE-PACKAGES.md`](docs/LANGUAGE-PACKAGES.md).

Go follows the standard monorepo module convention and is tagged
`packages/go/vX.Y.Z`. Registry-backed packages use their native registries:
PyPI, npm, crates.io, Maven Central, and NuGet.

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

## SDK 1.1 Stable Agent Tools

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
`ContextReceipt`. Stable Agent Tools is a separate API surface. The published
SDK 1.1.0 requires Agent Tools Interface v1 from Engine 3.10.1; current `main`
targets Engine 3.10.5. The older context-view/recover Engine Interface v1
remains separate.

See:

- [Custom agents](docs/CUSTOM-AGENTS.md)
- [Quickstart](docs/QUICKSTART.md)
- [Compatibility](COMPATIBILITY.md)
- [Security](SECURITY.md)
- [Errors](docs/ERRORS.md)
- [Migration](MIGRATION.md)
- [Stable public surface](PUBLIC-SURFACE-MANIFEST.md)

## License

The SDK is source-available under a license that permits non-production use.
Production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG. LeanCTX Engine is licensed separately
under Apache-2.0; that license does not grant SDK production or OEM rights.
