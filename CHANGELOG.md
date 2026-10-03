# Changelog

## Unreleased

### Changed

- Every language package now targets **LeanCTX Engine 3.10.5**, the newest
  published Engine. Since 1.1.0 only the Python constant had moved
  (3.10.2–3.10.5, #18–#21); the TypeScript, Go, Rust, JVM and .NET constants,
  the `[agent]` companion-Engine extras, the Agent Tools contract and the
  release-candidate evidence still named 3.10.1. A Python `[agent]` install
  from `main` therefore pulled an Engine its own SDK rejected. All of them now
  agree on 3.10.5.
- The release-candidate pipeline and release-evidence scripts bind Engine
  v3.10.5: commit `102330a77c36061483f60d914aeb14d3551b6e24`, signed
  `SHA256SUMS` and the Linux x86_64 / macOS arm64 archive and binary digests
  (see `COMPATIBILITY.md`).

- The Rust package's minimum supported Rust version is now **1.77** (was
  1.76). `thiserror` 2.0.21 (released 2026-09-23) requires Rust 1.77, so
  `cargo package` verification on 1.76 could no longer resolve the crate's
  dependencies. The MSRV CI leg runs on 1.77.0.

### Security

- The certified OpenAI Agents 0.8.4 wheelhouse is re-certified with
  **PyJWT 2.15.1** (was 2.13.0) and **urllib3 2.8.0** (was 2.7.0). The old pins
  carried 13 PyJWT advisories (one critical, five high: for example asymmetric
  PEM detection bypass, public keys or JWK containers accepted as HMAC secrets,
  a BOM bypass) and 3 urllib3 advisories (two high: HTTPS proxy TLS
  configuration ignored, unbounded buffering in `stream()` / `read_chunked()`),
  published 2026-09-29/30.
  - Both replacements are the official PyPI `py3-none-any` wheels. The urllib3
    source provenance is its `2.8.0` tag commit
    `b1d30ab61fe0db8f11092805e8c5ac43e091064a`, following the existing
    convention.
  - The closure stays at 41 artifacts: neither wheel adds a dependency on
    Python 3.11, and the bounds that pull them in still hold (`mcp` needs
    `pyjwt>=2.10.1`, `requests` needs `urllib3<3`).
  - The new artifacts digest is
    `255295d47432f88f38dbf1adbefd8b07df044085f0ef47e41f752e76f97e6c15`.
  - The user-facing `[openai-agents]` extra pinned the same vulnerable
    `urllib3==2.7.0`; it now pins `urllib3==2.8.0`. PyJWT is not pinned by
    the extra and resolves to a current release. The published 1.1.0 extra
    still pins 2.7.0; the 1.1.1 maintenance release below includes this correction.
  - The per-wheel checks of `dependency_wheel_audit` pass for both wheels
    (MIT, no secrets, no build-path findings, so the audit policy is
    unchanged), and an OSV query over all 41 pinned artifacts finds no known
    vulnerability.

### Compatibility notes

- **3.10.1 → 3.10.5:** the Agent Tools and Engine Interface code is unchanged
  (interface `1.0.0`, schema `1`, transport `1`; the same ten tools with the
  same argument schemas). `scripts/verify_agent_context_e2e.py` passes against
  3.10.5.
- **Next Engine release (after 3.10.5, not yet published):** the protocol is
  unchanged as well, and the end-to-end verification passes against a build
  of the Engine's current `main`. Tool results will differ in three
  observable ways: tool output text passes the Engine's secret redaction
  (secret-shaped values are masked); `ctx_shell` reports a command terminated
  by a signal with exit code `128 + signal` instead of a flat `1`; and
  `ctx_read` keeps a `-N` tail window under `raw=true`. Supporting that release
  needs only the usual constant bump.

## 1.1.1

- Published from the Engine 3.10.1 maintenance line, separately from the newer
  unreleased `main` source. Public APIs, wire versions, language minimums and
  license terms are unchanged.
- Updated the README and all six package descriptions for LeanCTX context
  control and the Context Gateway for AI Systems positioning.
- Backported the audited optional PyJWT 2.15.1 and urllib3 2.8.0 corrections.
- Pinned already-locked thiserror 2.0.20 to preserve Rust 1.76 package resolution.
- Exact source, wheel digest and Engine pairing are recorded in
  [COMPATIBILITY.md](COMPATIBILITY.md#stable-sdk-111).

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
