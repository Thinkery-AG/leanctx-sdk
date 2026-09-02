# Thinkery.LeanCtx 1.1.0

Thinkery.LeanCtx is a source-available .NET 8 SDK for the LeanCTX Product
primitives, Engine Interface v1, and Agent Tools 1.1.

Engine-backed production use is gated on LeanCTX Engine 3.10.1. The package
does not embed an Engine and does not grant Engine rights. Use
`LEANCTX_ENGINE_BIN` or an explicit executable path for a separately installed
Engine.

The five Product values use deterministic UTF-8 JSON and SHA-256 bindings that
are compatible with the TypeScript and Python SDK fixtures. Agent Tools uses a
persistent JSONL child process with fail-closed protocol handling, an immutable
0600 policy file, structured argv, and explicit environment allowlists.

See `LICENSE`, `COMMERCIAL-LICENSE.md`, and `THIRD_PARTY_NOTICES` for terms.
