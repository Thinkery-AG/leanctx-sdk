# Thinkery LeanCTX SDK for Rust

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

This Rust SDK connects your host-owned model and workflow to a local LeanCTX
instance. Your application keeps its model, agent loop, and UI. It provides the
five Stable lifecycle primitives plus the separate Stable Agent Tools API.

The SDK launches a local LeanCTX subprocess with a project-root jail,
bounded UTF-8 JSON/JSONL transport, strict protocol validation, and typed
fail-closed errors. It never invokes a shell for an instance or Agent Tools
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

The published SDK 1.1.1 release requires LeanCTX 3.10.1; current `main` sources
target LeanCTX 3.12.1. Check the release-specific compatibility record before
choosing an instance binary. The SDK source license permits non-production use;
production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG.

`AsyncAgentContext` moves blocking process operations off the polling thread using
standard-library workers; it does not require a particular async runtime. Four
operations may be admitted across the process. A slot remains occupied until its
result is consumed or cancellation cleanup finishes, including completed but
unconsumed opens. Saturation returns a typed `InstanceExecutionError`, also for
`cancel()` and `close()`; dropping a pending operation requests cleanup without
needing another slot. Cancellation terminates the shared context, not just one
request. One cleanup worker reuses the synchronous termination authority.

Dropping an operation future queues process cleanup; it does not wait for process
termination. This does not make `context()`, `metrics()`, or dropping an already
delivered context asynchronous: call `close().await` before dropping that context.
The existing process-tree containment limits and instance version contract still
apply. Internal lock ordering is documented in `LOCK_ORDERING.md`.

## Authorized source context

`EnterpriseInstanceClient` calls an existing authenticated LeanCTX service to plan
and materialize authorized source context. A source ID selects an input; it does
not grant access. The client validates tenant and requested-source bindings,
instance versions, governance revision, plan/content digests, and response bounds.

```rust,no_run
use leanctx_sdk::{InstancePlanningRequest, EnterpriseInstanceClient};

let client = EnterpriseInstanceClient::new(
    "https://leanctx.example.com",
    std::env::var("LEANCTX_API_TOKEN")?,
    "11111111-1111-4111-8111-111111111111",
)?;
let request = InstancePlanningRequest::new("login-fix", "Investigate the login failure", 2048)?;
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
establish an execution outcome. They do not invoke the local instance subprocess.

License and commercial-use terms are in `LICENSE` and
`COMMERCIAL-LICENSE.md`; dependency notices are in `THIRD_PARTY_NOTICES`.

## Process containment (session starter)

On Unix the SDK starts the instance binary with `LEAN_CTX_SESSION_CONTAINMENT=1`.
An instance binary that has a session starter answers with one line on standard
output before the instance itself starts (`{"leanctx_session_containment":1}`).
The SDK removes that line and reports one of two states (`SessionContainment`):

- `EngineStarter`: the process the SDK started is the instance's session
  starter. It runs the instance in a process group of its own, ends that group
  when the instance exits, when the SDK ends the starter or when the SDK's
  process is gone, and exits only afterwards.
- `Unavailable`: no acknowledgement. The SDK's own process-group handling is
  all there is.

Read it from `AgentContext::session_containment()` (also on
`AsyncAgentContext`) and, for the one-shot client, from
`SubprocessInstanceClient::last_session_containment()` (the most recent completed
run).

On Windows there is no starter process: an instance that has the mode joins a
kill-on-close job of its own before it starts anything and acknowledges with
the same line (reported as the same state); everything it started ends when
the instance process ends. A host job that forbids nesting leaves it
unacknowledged.

Not covered: descendants that leave their
process group (`setsid`, `setpgid`), and a graceful stop — the SDK ends the
starter, and the instance's group is ended right after, which can be a few
milliseconds after `cancel`/`close` returned. Whether a given instance build has
the starter is told only by the reported state.

## Context Store preview (source proposal)

SubprocessInstanceClient::read_policy_evidence and read_task_lineage provide
read-only tenant/project-scoped access through preview::ContextStoreScope.
The preview exports include parse_policy_evidence, parse_task_lineage,
evidence/lineage types and the preview contract 0.1.0.

This source is paired with LeanCTX 3.12.1 (published); the
compatibility matrix also admits 3.11.0, 3.11.3, 3.11.4, 3.11.5 and 3.12.0. Exact public Context Store source retains the separately reported
F2 signed-integer overflow; correction is a later local step.

## Deprecated names (until 2.0)

SDK 1.3.0 names the running LeanCTX copy an *instance*. The Engine names
still compile (with a deprecation warning) and are the same types, so
`downcast_ref::<EngineUnavailable>()` keeps matching what the SDK returns.
The engine interface contract (`ENGINE_INTERFACE_VERSION`,
`engine_interface_version`, `engine_id`, `engine_version` and every other wire
field) is unchanged.

| Deprecated | Use |
|---|---|
| `EngineError`, `EngineUnavailable`, `EngineTimeout`, `EngineCrashed` | `InstanceError`, `InstanceUnavailable`, `InstanceTimeout`, `InstanceCrashed` |
| `EngineProtocolError`, `EngineRejected`, `EngineExecutionError` | `InstanceProtocolError`, `InstanceRejected`, `InstanceExecutionError` |
| `UnsupportedEngineError` | `UnsupportedInstanceError` |
| `EngineStatus` | `InstanceStatus` |
| `EngineClient` (trait re-export, no warning) | `InstanceClient` |
| `SubprocessEngineClient`, `EnterpriseEngineClient` | `SubprocessInstanceClient`, `EnterpriseInstanceClient` |
| `EnginePlanningRequest` | `InstancePlanningRequest` |
| `EnginePresenterGrant`, `EngineSourcePlanResponse` | `InstancePresenterGrant`, `InstanceSourcePlanResponse` |
| `EngineSourceMaterializationResponse`(`V2`) | `InstanceSourceMaterializationResponse`(`V2`) |
| `SUPPORTED_AGENT_TOOLS_ENGINE_VERSION` | `SUPPORTED_AGENT_TOOLS_INSTANCE_VERSION` |
| `ENGINE_PRESENTER_CAPABILITY_MIN_ENGINE_VERSION` | `INSTANCE_PRESENTER_CAPABILITY_MIN_INSTANCE_VERSION` |
| `MAX_ENGINE_PRESENTER_TTL_SECONDS` | `MAX_INSTANCE_PRESENTER_TTL_SECONDS` |
| `AgentContextBuilder::engine_binary`, `AgentContext::engine_binary` | `instance_binary` |
| `SubprocessInstanceClient::engine_binary` | `instance_binary` |
| `ContextSession::with_engine`, `new_with_engine` | `with_instance`, `new_with_instance` |

`SessionContainment::EngineStarter` keeps its name: adding a renamed variant
would break exhaustive `match` statements in existing code.

The live-instance tests read `LEANCTX_INSTANCE_BIN`, `LEANCTX_INSTANCE_BINARY`,
`LEANCTX_INSTANCE_PROOF_FILE` and `LEANCTX_REQUIRE_INSTANCE` first; the old
`LEANCTX_ENGINE_*` / `LEANCTX_REQUIRE_ENGINE` names still work with a one-time
deprecation warning.
