# Changelog

## 1.3.0

Instance names; Engine names deprecated until 2.0; engine interface contract
unchanged. See `RELEASE-NOTES-1.3.0.md`.

- Python: `Instance*` names are primary; `Engine*` names, `engine_binary=`,
  `engine=`, the `leanctx_sdk.engine`/`enterprise_engine` modules and the
  `LEANCTX_ENGINE_*` variables keep working with a `DeprecationWarning`.
- All six packages pair with LeanCTX | OSS **3.12.2** (published 2026-11-05).
  The pair evidence was recorded against the release archives.
- The release-candidate pipeline, the release-evidence script and the
  release-gate tests bind the signed, published LeanCTX | OSS v3.12.2: its tag
  commit `ffe6e95c34babc2d42a6a1cd60833c352e928b52`, its `SHA256SUMS`, and the
  archive and binary digests for Linux x86_64 and macOS arm64. See
  `COMPATIBILITY.md`.

### Fixed

- .NET: closing an agent context no longer fails with "descendants could not
  be inspected" when an Engine child exits while its children are being read.
  For such a task, Linux procfs answers ESRCH, an `IOException` rather than
  `FileNotFoundException`. A process that is gone, a zombie or dead is now
  treated as having no children.
- Rust: `utf8_iter` and `utf16_iter` are pinned to their last Rust 1.77
  releases (1.0.4 and 1.0.5). `cargo package` resolves without the lockfile,
  and 1.1.0 of both requires Rust 1.83, so the 1.77 MSRV leg broke. The gateway
  preview also satisfies the 1.77 clippy lints `format_collect` and
  `needless_borrows_for_generic_args`, with identical output.

### Security

- The certified OpenAI Agents 0.8.4 wheelhouse is re-certified with
  **certifi 2026.10.18** (was 2026.7.22, PYSEC-2026-4320) and **mcp 1.30.0**
  (was 1.29.1, CVE-2026-59951, CVE-2026-116684, CVE-2026-116685 and
  CVE-2026-116686).
  - Both are the official PyPI `py3-none-any` wheels, and mcp 1.30.0 declares
    the same dependencies as 1.29.1. The closure therefore stays at 41
    artifacts, and `openai-agents` 0.8.4 still admits it (`mcp<2,>=1.19.0`).
  - The new artifacts digest is
    `42bb55a09b16a50016a09000ca335f19d7e1d18c8b9a5ba8ae2274fa16998626`.
  - `pip-audit` finds no known vulnerability in the closure. `validate_wheelhouse`
    and `dependency_wheel_audit` pass for all 41 wheels (2384 entries), so the
    audit policy is unchanged.
  - The release-audit tooling pins certifi 2026.10.18 as well.
- The release tooling and the Rust lockfile no longer carry advised versions:
  `filelock` 4.2.1 (was 3.32.4) and `msgpack` 1.2.3 (was 1.2.1) in the audit
  tooling, `setuptools` 84.1.0 (was 83.0.0) in the build tooling, and
  `rustls` 0.23.46 (was 0.23.45, still within the Rust 1.77 MSRV) in
  `packages/rust/Cargo.lock`. Every requirement keeps all of its PyPI file
  hashes, both files install with `--require-hashes` on Python 3.11, and
  `pip-audit` finds no known vulnerability in either.

## Unreleased (1.2.0)

SDK 1.2.0 pairs all six language packages with LeanCTX Engine **3.11.0** and
adds the v4 preview surfaces below. Interface, schema and transport versions
and the stable public-surface contract are unchanged. The published 1.1.x
packages keep their release identity.

### Added

- Preview `leanctx-context-store-preview` 0.1 in all six SDKs: read-only task
  lineage (`engine context-lineage`) and read-strategy policy evidence
  (`engine context-policy-evidence`) within one tenant/project scope; typed
  `TaskLineage`, `LineageStep`, `LineageDelivery`, `DeliverySummary`,
  `ContextPolicyEvidence`, `StrategyOutcomeRecord`, `StrategyEvaluation`,
  `QualityEvidence`, `SecurityEvidence`, `Workload`. Unmeasured evidence never
  reads as measured. Shared fixtures `fixtures/context-store-preview-v1`,
  contract `contracts/context-store-preview-v1.json`, guide
  `docs/context-store-preview.md`, decision record
  `docs/decisions/2026-10-03-context-store-preview.md`.
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
- Conformance and live-Engine journeys pass in all six SDKs (JVM verified on
  JDK 21, .NET on .NET 8).
- Flagship journey `examples/gateway_login_journey.py` ("Fix the production
  login issue") reports a HUD line computed from the Engine's plan and the
  receipt. With the Engine's relevance floor for explicit-source plans,
  unrelated documents stay out even when budget remains.
- Authorized source planning and materialization clients
  (`EnterpriseEngineClient` and the local source-planning adapters) across the
  six SDKs; see each package README.
- TypeScript Engine process-group cleanup correction.

### Changed

- Every language package, the `[agent]` companion-Engine extras and the Agent
  Tools contract target **LeanCTX Engine 3.11.0** (not yet published). `main`
  previously targeted 3.10.5; before that, only the Python constant had moved
  past 3.10.1 (#18–#21), so a Python `[agent]` install from `main` pulled an
  Engine its own SDK rejected. All six languages agree again.
- The release-candidate pipeline and release-evidence scripts still bind the
  published Engine v3.10.5 (commit `102330a77c36061483f60d914aeb14d3551b6e24`).
  They move to v3.11.0 with digests taken from the signed release download once
  that Engine is tagged.

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
