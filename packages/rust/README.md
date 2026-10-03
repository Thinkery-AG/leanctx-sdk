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

SDK 1.1.1 preserves the Engine 3.10.1 requirement of SDK 1.1.0. Check the release-specific compatibility record before
choosing an Engine binary. The SDK source license permits non-production use;
production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG.

License and commercial-use terms are in `LICENSE` and
`COMMERCIAL-LICENSE.md`; dependency notices are in `THIRD_PARTY_NOTICES`.
