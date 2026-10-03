# Changelog

## 1.1.1

- Align package descriptions and documentation with LeanCTX's Context Gateway
  positioning and the SDK's role in embedding context control.
- Preserve the 1.1.0 Stable and Preview APIs, exact Agent Tools Engine 3.10.1
  requirement, protocol versions, and supported language runtime minimums.
- Backport the audited PyJWT 2.15.1 and urllib3 2.8.0 wheelhouse security
  corrections; update the optional OpenAI Agents urllib3 dependency accordingly.
- Pin the existing Rust `thiserror` 2.0.20 dependency so clean package resolution
  retains the declared Rust 1.76 minimum; 2.0.21 requires Rust 1.77.
- Release artifacts and approval records use the exact maintenance version;
  Engine digests, license terms, signing, provenance and publication gates remain.

## 1.1.0

- Stable PR #8 Agent Tools contract with explicit read/write/execute policy,
  persistent Engine sessions, metrics, reconnect, and typed failures.
- Language-native SDK previews for Python, TypeScript, Go, Rust, Java/Kotlin,
  and .NET with shared Product/Engine wire fingerprints.
- Fixed fresh-process `bind_source` → `attach_session` for forked Workspaces
  without persisting machine-local source paths; attachment rejects content
  changed after binding and bindings stale after a durable source update.
- Extended the tested Python contract through CPython 3.14 and enforced Ruff
  formatting plus complementary ty/mypy checks in the release gate.
- Release provenance is bound to verified LeanCTX Engine 3.10.1 artifacts.

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
