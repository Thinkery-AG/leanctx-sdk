# LeanCTX JVM SDK

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

This Java 21 and Kotlin SDK connects your host-owned model and workflow to a
local LeanCTX Engine. Your application keeps its model, agent loop, and UI. It
provides the five Stable lifecycle primitives plus the separate Stable Agent
Tools API.

```java
try (AgentContext tools = AgentContext.open(projectRoot)) {
    ToolResult result = tools.read("README.md", ReadMode.AUTO, false);
    System.out.println(result.text());
}
```

Production code has no runtime dependencies. Engine processes use structured
arguments, bounded streams, project-root containment, strict JSON validation,
secure temporary policy files, and fail-closed process termination.

Cancelling an `AsyncAgentContext` tool future terminates its persistent Engine
session and reaps that process tree; reconnect before making another call.
This also applies to `search`, `glob`, `tree`, `compose`, `symbol` and `patch`.
These convenience methods retain the synchronous argument checks and write
permission rules, returning validation failures through their futures.

Persistent and one-shot adapters share one process-termination implementation.
It waits for the Engine and descendants captured at termination, with a shared
two-second cleanup deadline; incomplete cleanup is an `EngineExecutionError`.
That cleanup failure takes precedence over the original timeout or protocol
error, so callers cannot mistake an unverified shutdown for a normal timeout.
An interrupted caller retains its interrupt flag after that cleanup. This is
not OS sandboxing: children detached before capture require deployment-level
process containment.

## Authorized source context

`EnterpriseEngineClient` exposes `contextPlan` and `contextMaterialize` for an
existing authenticated LeanCTX service. `EnginePlanningRequest` carries the
task and token budget; source IDs select inputs that the service must authorize.
Materialization takes the governance revision and binding digest from the
plan response. Revisions use `BigInteger` to preserve the unsigned 64-bit range.

The client checks tenant/source scope, versions, plan bindings and content
digests before returning immutable response maps. These calls prepare context;
they do not send it to a model or establish an execution outcome. Local Engine
processes are not involved in these HTTP operations.

HTTPS uses the JDK trust manager and hostname checks. Redirects and proxy
inheritance are disabled; response bytes and the full response duration are
bounded. Explicit `allowLoopbackHttp` is reserved for literal loopback addresses
in local tests. SDK and Engine licensing remain separate.

The published SDK 1.1.1 release requires Engine 3.10.1; current `main` sources
target Engine 3.11.2. Check the release-specific compatibility record before
choosing an Engine binary. The SDK source license permits non-production use;
production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG.
