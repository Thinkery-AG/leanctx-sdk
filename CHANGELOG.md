# Changelog

## 1.1.0 (release candidate)

- Stable PR #8 Agent Tools contract with explicit read/write/execute policy,
  persistent Engine sessions, metrics, reconnect, and typed failures.
- Language-native SDK previews for Python, TypeScript, Go, Rust, Java/Kotlin,
  and .NET with shared Product/Engine wire fingerprints.
- Publication remains locked to verified LeanCTX Engine 3.10.1 artifacts.

## 1.0.0

### Stable

- Five Product primitives: `ContextSession`, `ContextSource`, `ContextView`,
  `ContextPlan`, and `ContextReceipt`.
- Select → Shape → Reuse → Recover lifecycle.
- Exact Engine protocol compatibility, typed errors, receipts, recovery, and
  provider-free OpenAI Agents 0.8.4 reference integration.

### Preview

- Explicit `leanctx_sdk.preview` namespace.
- Local Workspace, Checkpoint, Delta, Handoff, fork, and policy-inheritance
  evaluation APIs.
- Narrow `.ctxpkg` seal, seed, and SnapshotV1 migration operations backed by
  the supported public Engine `v3.10.0` release.

### Distribution

- Distribution `thinkery-leanctx-sdk`, import `leanctx_sdk`, version `1.0.0`.
- Perpetual source-available SDK license with no automatic open-source date;
  commercial Production Use requires a Thinkery AG agreement.
- P8 Cloud Receipt Board, P9 Governed Optimization, and private research evidence
  are excluded.
