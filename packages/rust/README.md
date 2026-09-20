# Thinkery LeanCTX SDK for Rust

Source-available Rust SDK 1.1.0 for the governed LeanCTX Product lifecycle
and Agent Tools Interface v1. It provides the five stable Product primitives
(`ContextSession`, `ContextSource`, `ContextView`, `ContextPlan`, and
`ContextReceipt`) plus a persistent, permissioned `AgentContext`.

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

Agent Tools requires the published Engine 3.10.1 and negotiates the exact v1
capability set. Registry releases are produced from the monorepo's cross-SDK
promotion gate. See the repository contracts and
`PUBLIC-SURFACE-MANIFEST.md` for the frozen wire and public API contracts.

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

License and commercial-use terms are in `LICENSE` and
`COMMERCIAL-LICENSE.md`; dependency notices are in `THIRD_PARTY_NOTICES`.
