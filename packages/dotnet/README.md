# Thinkery.LeanCtx 1.2.0

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

Thinkery.LeanCtx is a .NET 8 SDK that connects your host-owned model and
workflow to a local LeanCTX Engine. Your application keeps its model, agent
loop, and UI. It provides the five Stable lifecycle primitives plus the
separate Stable Agent Tools API.

The published SDK 1.1.1 release requires Engine 3.10.1; current `main` sources
target Engine 3.11.1. Check the release-specific compatibility record before
choosing an Engine binary. The package does not embed an Engine and does not
grant Engine rights. Use
`LEANCTX_ENGINE_BIN` or an explicit executable path for a separately installed
Engine.

The five Product values use deterministic UTF-8 JSON and SHA-256 bindings that
are compatible with the TypeScript and Python SDK fixtures. Agent Tools uses a
persistent JSONL child process with fail-closed protocol handling, an immutable
0600 policy file, structured argv, and explicit environment allowlists.

## Authorized source context

`EnterpriseEngineClient` calls an existing LeanCTX service to plan and
materialize authorized sources. It does not require a local Engine executable.
The service grants source access; passing a source ID never grants permission.

```csharp
using Thinkery.LeanCtx;

using var client = new EnterpriseEngineClient(
    "https://leanctx.example.com",
    Environment.GetEnvironmentVariable("LEANCTX_API_TOKEN")!,
    "11111111-1111-4111-8111-111111111111");
var request = new EnginePlanningRequest("login-fix", "Investigate the login failure", 2048);
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
