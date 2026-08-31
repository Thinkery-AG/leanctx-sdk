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

Agent Tools requires Engine 3.10.1 and negotiates the exact v1 capability
set. The package remains `publish = false` until that Engine release is
available. See the repository contracts and `PUBLIC-SURFACE-MANIFEST.md` for
the frozen wire and public API contracts.

License and commercial-use terms are in `LICENSE` and
`COMMERCIAL-LICENSE.md`; dependency notices are in `THIRD_PARTY_NOTICES`.
