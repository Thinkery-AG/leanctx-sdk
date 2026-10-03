# Changelog

## Unreleased

- Preview `leanctx-gateway-preview` 0.1 in all six SDKs: egress admission
  through the Engine's `egress-admit` with typed `ContextPrincipal`,
  `ContextDestination`, `ContextDecision`, `SecuritySignal`,
  `DetectorCoverage`, `ContextDecisionReceipt` and `EgressAdmission`; strict
  parsing that mirrors the Engine's validation.
- Shared conformance fixtures (`fixtures/gateway-preview-v1`: real Engine
  responses plus 29 single-violation documents) and a live-Engine journey in
  every SDK; reference apps A (minimal app) and B (own agent loop) without
  MCP, UI or cloud. See `docs/gateway-preview.md`.
- The stable root surfaces are unchanged (Python `leanctx_sdk.preview`,
  TypeScript `@thinkery/leanctx-sdk/preview` subpath).

## 1.2.0

- Pair all six SDK packages with LeanCTX Engine 4.0.0.
- Keep the existing interface, schema and transport versions and public-surface contract.
- Preserve the published 1.1.0 packages; 1.2.0 uses a new release identity.
- Include the TypeScript Engine process-group cleanup correction.

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
