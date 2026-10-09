# Thinkery LeanCTX SDK for Rust

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

This Rust SDK connects your host-owned model and workflow to a local LeanCTX
Engine. Your application keeps its model, agent loop, and UI. It provides the
five Stable lifecycle primitives plus the separate Stable Agent Tools API.

The SDK launches a local LeanCTX Engine subprocess with a project-root jail,
bounded UTF-8 JSON/JSONL transport, strict protocol validation, and typed
fail-closed errors. It never invokes a shell for an Engine or Agent Tools
request. `AgentContext` defaults to read-only; writes and command execution
require explicit immutable policy admission.

```rust,no_run
use leanctx_sdk::{AgentContext, ReadMode};

let context = AgentContext::open(".")?;
let source = context.read("src/lib.rs", ReadMode::Signatures, false)?;
println!("{} (saved {})", source.text(), source.saved_tokens());
context.close()?;
# Ok::<(), Box<dyn std::error::Error + Send + Sync>>(())
```

The published SDK 1.1.1 release requires Engine 3.10.1; current `main` sources
target Engine 3.11.2. Check the release-specific compatibility record before
choosing an Engine binary. The SDK source license permits non-production use;
production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG.

`AsyncAgentContext` moves blocking process operations off the polling thread using
standard-library workers; it does not require a particular async runtime. Four
operations may be admitted across the process. A slot remains occupied until its
result is consumed or cancellation cleanup finishes, including completed but
unconsumed opens. Saturation returns a typed `EngineExecutionError`, also for
`cancel()` and `close()`; dropping a pending operation requests cleanup without
needing another slot. Cancellation terminates the shared context, not just one
request. One cleanup worker reuses the synchronous termination authority.

Dropping an operation future queues process cleanup; it does not wait for process
termination. This does not make `context()`, `metrics()`, or dropping an already
delivered context asynchronous: call `close().await` before dropping that context.
The existing process-tree containment limits and Engine version contract still
apply. Internal lock ordering is documented in `LOCK_ORDERING.md`.

## Authorized source context

`EnterpriseEngineClient` calls an existing authenticated LeanCTX service to plan
and materialize authorized source context. A source ID selects an input; it does
not grant access. The client validates tenant and requested-source bindings,
engine versions, governance revision, plan/content digests, and response bounds.

```rust,no_run
use leanctx_sdk::{EnginePlanningRequest, EnterpriseEngineClient};

let client = EnterpriseEngineClient::new(
    "https://leanctx.example.com",
    std::env::var("LEANCTX_API_TOKEN")?,
    "11111111-1111-4111-8111-111111111111",
)?;
let request = EnginePlanningRequest::new("login-fix", "Investigate the login failure", 2048)?;
let sources = ["22222222-2222-4222-8222-222222222222"];
let plan = client.context_plan(&request, &sources)?;
let binding_digest = plan.plan()["binding_digest"]
    .as_str()
    .ok_or("missing source binding digest")?;
let materialized = client.context_materialize(
    &request,
    &sources,
    plan.governance_revision(),
    binding_digest,
    None,
)?;
let prepared_context = materialized.materialization()["content"]
    .as_str()
    .ok_or("missing materialized content")?;
println!("{prepared_context}");
# Ok::<(), Box<dyn std::error::Error + Send + Sync>>(())
```

HTTPS retains rustls certificate-chain and hostname verification. Redirects and
environment proxy inheritance are disabled. The default full request deadline
is 30 seconds; `with_options` accepts 0.1–120 seconds. After DNS resolution it
covers connect, send, response headers, and body. Planning requests are limited
to 64 KiB, plan responses to 1 MiB, and materialization responses to 2 MiB with
at most 1 MiB of content. HTTP is available only with explicit
`allow_loopback_http: true` for a literal loopback address in local tests.
ureq resolves DNS synchronously, so the deadline cannot interrupt a blocked DNS
lookup or guarantee a hard upper bound while name resolution is in progress.

Materialized context is prepared data; these calls do not send it to a model or
establish an execution outcome. They do not invoke the local Engine subprocess.

License and commercial-use terms are in `LICENSE` and
`COMMERCIAL-LICENSE.md`; dependency notices are in `THIRD_PARTY_NOTICES`.
