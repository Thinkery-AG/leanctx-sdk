# Thinkery.LeanCtx 1.1.1

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

Thinkery.LeanCtx is a .NET 8 SDK that connects your host-owned model and
workflow to a local LeanCTX Engine. Your application keeps its model, agent
loop, and UI. It provides the five Stable lifecycle primitives plus the
separate Stable Agent Tools API.

SDK 1.1.1 preserves the Engine 3.10.1 requirement of SDK 1.1.0. Check the release-specific compatibility record before
choosing an Engine binary. The package does not embed an Engine and does not
grant Engine rights. Use
`LEANCTX_ENGINE_BIN` or an explicit executable path for a separately installed
Engine.

The five Product values use deterministic UTF-8 JSON and SHA-256 bindings that
are compatible with the TypeScript and Python SDK fixtures. Agent Tools uses a
persistent JSONL child process with fail-closed protocol handling, an immutable
0600 policy file, structured argv, and explicit environment allowlists.

The SDK source license permits non-production use; production use, OEM
embedding, and commercial redistribution require a separate written agreement
signed by Thinkery AG. See `LICENSE`, `COMMERCIAL-LICENSE.md`, and
`THIRD_PARTY_NOTICES` for terms.
