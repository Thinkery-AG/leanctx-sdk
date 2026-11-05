# LeanCTX JVM SDK

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

This Java 21 and Kotlin SDK connects your host-owned model and workflow to a
local LeanCTX instance. Your application keeps its model, agent loop, and UI. It
provides the five Stable lifecycle primitives plus the separate Stable Agent
Tools API.

```java
try (AgentContext tools = AgentContext.open(projectRoot)) {
    ToolResult result = tools.read("README.md", ReadMode.AUTO, false);
    System.out.println(result.text());
}
```

Production code has no runtime dependencies. LeanCTX instance processes use structured
arguments, bounded streams, project-root containment, strict JSON validation,
secure temporary policy files, and fail-closed process termination.

Cancelling an `AsyncAgentContext` tool future terminates its persistent LeanCTX instance
session and reaps that process tree; reconnect before making another call.
This also applies to `search`, `glob`, `tree`, `compose`, `symbol` and `patch`.
These convenience methods retain the synchronous argument checks and write
permission rules, returning validation failures through their futures.

Persistent and one-shot adapters share one process-termination implementation.
It waits for the LeanCTX instance and descendants captured at termination, with a shared
two-second cleanup deadline; incomplete cleanup is an `InstanceExecutionError`.
That cleanup failure takes precedence over the original timeout or protocol
error, so callers cannot mistake an unverified shutdown for a normal timeout.
An interrupted caller retains its interrupt flag after that cleanup. This is
not OS sandboxing: children detached before capture require deployment-level
process containment.

## Deprecated names (until 2.0)

SDK 1.3.0 renames the public "Engine" API to "Instance"; the engine interface
contract (wire fields such as `engine_interface_version`, error codes such as
`engine_unavailable`) is unchanged. Every old name still compiles and is marked
`@Deprecated(since = "1.3.0", forRemoval = true)`.

| Deprecated | Use |
|---|---|
| `EngineError`, `EngineUnavailable`, `EngineProtocolError`, `EngineExecutionError`, `EngineTimeout`, `EngineCrashed`, `EngineRejected` | `InstanceError`, `InstanceUnavailable`, `InstanceProtocolError`, `InstanceExecutionError`, `InstanceTimeout`, `InstanceCrashed`, `InstanceRejected` |
| `UnsupportedEngineError` | `UnsupportedInstanceError` |
| `EngineClient`, `SubprocessEngineClient`, `EnterpriseEngineClient` | `InstanceClient`, `SubprocessInstanceClient`, `EnterpriseInstanceClient` |
| `EngineStatus`, `EnginePlanningRequest` | `InstanceStatus`, `InstancePlanningRequest` |
| `engineBinary()` / `getEngineBinary()`, `ContextSession.engine()` | `instanceBinary()` / `getInstanceBinary()`, `ContextSession.instance()` |
| `LEANCTX_ENGINE_BIN`, `LEANCTX_ENGINE_BINARY` (test harness) | `LEANCTX_INSTANCE_BIN`, `LEANCTX_INSTANCE_BINARY` (old name is a fallback with a one-time warning) |

Java cannot alias types, so each old exception is a subclass of its new one and
the SDK keeps throwing the old subtype until 2.0: `catch (EngineUnavailable e)`
and `catch (InstanceUnavailable e)` both match. The new exception classes extend
`EngineError`, so `catch (EngineError e)` still matches every instance failure.
Only an `Instance*` exception you construct yourself is not caught by the
matching old `Engine*` catch.

## Authorized source context

`EnterpriseInstanceClient` exposes `contextPlan` and `contextMaterialize` for an
existing authenticated LeanCTX service. `InstancePlanningRequest` carries the
task and token budget; source IDs select inputs that the service must authorize.
Materialization takes the governance revision and binding digest from the
plan response. Revisions use `BigInteger` to preserve the unsigned 64-bit range.

The client checks tenant/source scope, versions, plan bindings and content
digests before returning immutable response maps. These calls prepare context;
they do not send it to a model or establish an execution outcome. Local LeanCTX instance
processes are not involved in these HTTP operations.

HTTPS uses the JDK trust manager and hostname checks. Redirects and proxy
inheritance are disabled; response bytes and the full response duration are
bounded. Explicit `allowLoopbackHttp` is reserved for literal loopback addresses
in local tests. SDK and LeanCTX instance licensing remain separate.

The published SDK 1.1.1 release requires LeanCTX 3.10.1; current `main` sources
target LeanCTX 3.12.2. Check the release-specific compatibility record before
choosing a LeanCTX instance binary. The SDK source license permits non-production use;
production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG.

## Process containment (session starter)

The SDK starts the LeanCTX instance binary with `LEAN_CTX_SESSION_CONTAINMENT=1`.
A LeanCTX instance binary that has a session starter answers with one line on standard
output before the LeanCTX instance itself starts (`{"leanctx_session_containment":1}`).
The SDK removes that line and reports one of two states (`SessionContainment`):

- `ENGINE_STARTER`: the process the SDK started is the LeanCTX instance's session
  starter. The JVM cannot create a process group; the starter is what provides
  one. It runs the LeanCTX instance in a process group of its own, ends that group when
  the LeanCTX instance exits, when the SDK ends the starter or when the JVM is gone, and
  exits only afterwards.
- `UNAVAILABLE`: no acknowledgement. Only the descendants the JDK can still
  enumerate for a live LeanCTX instance are ended; what a LeanCTX instance that exited first left
  behind is not.

Read it from `AgentContext.sessionContainment()` (also on `AsyncAgentContext`)
and, for the one-shot client, from
`SubprocessInstanceClient.lastSessionContainment()` (the most recent completed
run).

On Windows there is no starter process: a LeanCTX instance that has the mode joins a
kill-on-close job of its own before it starts anything and acknowledges with
the same line (reported as the same state); everything it started ends when
the LeanCTX instance process ends. A host job that forbids nesting leaves it
unacknowledged.

Not covered: descendants that leave their
process group (`setsid`, `setpgid`), and a graceful stop — the SDK ends the
starter, and the LeanCTX instance's group is ended right after, which can be a few
milliseconds after `cancel()`/`close()` returned. Whether a given LeanCTX instance build
has the starter is told only by the reported state.

## Context Store preview (local source proposal)

`ContextStorePreview.parsePolicyEvidence` and `parseTaskLineage` validate
content-free policy evidence and task lineage. `readPolicyEvidence` and
`readTaskLineage` use an explicit LeanCTX instance client, project root and scope.
The same four operations are forwarded by `LeanCtx` in this private local
source resolution under the released JVM unit; this facade is a new local
choice, not an existing public `e142dc45` entry surface.
Unmeasured evidence stays unmeasured; incomplete lineage names its gaps.
The preview contract is `leanctx-context-store-preview` 0.1.0.

This source is paired with LeanCTX 3.12.2 (published); the
compatibility matrix also admits 3.11.0, 3.11.3, 3.11.4, 3.11.5, 3.12.0 and 3.12.1.
