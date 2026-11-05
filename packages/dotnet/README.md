# Thinkery.LeanCtx 1.3.0

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

Thinkery.LeanCtx is a .NET 8 SDK that connects your host-owned model and
workflow to a local LeanCTX instance. Your application keeps its model, agent
loop, and UI. It provides the five Stable lifecycle primitives plus the
separate Stable Agent Tools API.

SDK 1.3.0 requires LeanCTX | OSS 3.12.2 for Agent Tools. Check the
release-specific compatibility record before choosing a LeanCTX instance
binary. The package does not embed a LeanCTX instance and does not grant
instance rights. Use `LEANCTX_INSTANCE_BIN` or an explicit executable path
(`instanceBinary`) for a separately installed instance.

## Deprecated names (until 2.0)

SDK 1.3 renamed the public "Engine" names to "Instance". The old names still
compile and behave the same, marked `[Obsolete]`, and are removed in 2.0. .NET
cannot alias types, so each old type derives from its new one and the SDK keeps
throwing or returning the old subtype until 2.0: `catch (EngineUnavailable)` and
`catch (InstanceUnavailable)` both catch what the SDK throws. Error codes
(`engine_unavailable`, ...) and the engine interface wire contract
(`engine_interface_version`, `engine_id`, ...) are unchanged.

| Deprecated | Use |
|---|---|
| `EngineError`, `EngineUnavailable`, `EngineTimeout`, `EngineCrashed` | `InstanceError`, `InstanceUnavailable`, `InstanceTimeout`, `InstanceCrashed` |
| `EngineProtocolError`, `EngineRejected`, `EngineExecutionError` | `InstanceProtocolError`, `InstanceRejected`, `InstanceExecutionError` |
| `UnsupportedEngineError` | `UnsupportedInstanceError` |
| `EngineClient`, `SubprocessEngineClient`, `EnterpriseEngineClient` | `InstanceClient`, `SubprocessInstanceClient`, `EnterpriseInstanceClient` |
| `EnginePlanningRequest`, `EnginePresenterGrant`, `EngineSourceMaterializationV2` | `InstancePlanningRequest`, `InstancePresenterGrant`, `InstanceSourceMaterializationV2` |
| `EngineStatus`, `SessionContainment.EngineStarter` | `InstanceStatus`, `SessionContainment.InstanceStarter` |
| `EngineBinary` property, `ContextSession.Engine` | `InstanceBinary`, `ContextSession.Instance` |
| `PresenterCapability.MinEngineVersion` | `PresenterCapability.MinInstanceVersion` |
| `LEANCTX_ENGINE_BIN` (read with a one-time warning when the new one is unset) | `LEANCTX_INSTANCE_BIN` |

`SubprocessInstanceClient` takes `instanceBinary:`; its old `engineBinary:`
named argument is still accepted. `AgentContext`/`AsyncAgentContext` keep the
`engineBinary` parameter name in 1.3 for source compatibility of named arguments.

The five Product values use deterministic UTF-8 JSON and SHA-256 bindings that
are compatible with the TypeScript and Python SDK fixtures. Agent Tools uses a
persistent JSONL child process with fail-closed protocol handling, an immutable
0600 policy file, structured argv, and explicit environment allowlists.

## Authorized source context

`EnterpriseInstanceClient` calls an existing LeanCTX service to plan and
materialize authorized sources. It does not require a local LeanCTX instance executable.
The service grants source access; passing a source ID never grants permission.

```csharp
using Thinkery.LeanCtx;

using var client = new EnterpriseInstanceClient(
    "https://leanctx.example.com",
    Environment.GetEnvironmentVariable("LEANCTX_API_TOKEN")!,
    "11111111-1111-4111-8111-111111111111");
var request = new InstancePlanningRequest("login-fix", "Investigate the login failure", 2048);
var sources = new[] { "22222222-2222-4222-8222-222222222222" };
var response = client.ContextPlan(request, sources);
var plan = (IReadOnlyDictionary<string, object?>)response["plan"]!;
var materialized = client.ContextMaterialize(
    request, sources, (ulong)response["governance_revision"]!,
    (string)plan["binding_digest"]!);
var context = (IReadOnlyDictionary<string, object?>)materialized["materialization"]!;
string text = (string)context["content"]!;
```

The example IDs are placeholders for configured tenant/source IDs. Both
operations also expose asynchronous methods with cancellation tokens. Responses
are checked against the requested tenant, sources, governance revision and
plan/content digests before being returned. Returned context is prepared data;
these calls do not send it to a model or establish an execution outcome.

HTTPS retains platform certificate verification. Redirects, cookies and proxy
inheritance are disabled. Response sizes and the full request duration,
including body reads, are bounded. Plain HTTP is available only with explicit
`allowLoopbackHttp: true` for a literal loopback address in local tests.

The SDK source license permits non-production use; production use, OEM
embedding, and commercial redistribution require a separate written agreement
signed by Thinkery AG. See `LICENSE`, `COMMERCIAL-LICENSE.md`, and
`THIRD_PARTY_NOTICES` for terms.

## Process containment (session starter)

The SDK starts the instance binary with `LEAN_CTX_SESSION_CONTAINMENT=1`.
A LeanCTX instance binary that has a session starter answers with one line on standard
output before the instance itself starts (`{"leanctx_session_containment":1}`).
The SDK removes that line and reports one of two states (`SessionContainment`):

- `InstanceStarter`: the process the SDK started is the instance's session
  starter. .NET cannot create a process group; the starter is what provides
  one. It runs the instance in a process group of its own, ends that group when
  the instance exits, when the SDK ends the starter or when this process is
  gone, and exits only afterwards.
- `Unavailable`: no acknowledgement. Only the tree the runtime can still reach
  for a live instance is ended; what a LeanCTX instance that exited first left behind is
  not.

Read it from `AgentContext.SessionContainment` (also on `AsyncAgentContext`)
and, for the one-shot client, from
`SubprocessInstanceClient.LastSessionContainment` (the most recent completed
run).

On Windows there is no starter process: a LeanCTX instance that has the mode joins a
kill-on-close job of its own before it starts anything and acknowledges with
the same line (reported as the same state); everything it started ends when
the instance process ends. A host job that forbids nesting leaves it
unacknowledged.

Not covered: descendants that leave their
process group (`setsid`, `setpgid`), and a graceful stop — the SDK ends the
starter, and the instance's group is ended right after, which can be a few
milliseconds after cancel or dispose returned. Whether a given LeanCTX | OSS build
has the starter is told only by the reported state.
