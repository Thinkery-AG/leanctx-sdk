# LeanCTX SDK v1 Status Map

This map is normative for the `leanctx-sdk` 1.0.0 release. A symbol is
Stable only when this file and `PUBLIC-SURFACE-MANIFEST.md` classify it Stable.

## Stable — v1 compatibility commitment

Exactly five product primitives form the Stable SDK v1 surface:

| Primitive | Import | Commitment |
| --- | --- | --- |
| `ContextSession` | `leanctx_sdk.ContextSession` | SemVer-governed |
| `ContextSource` | `leanctx_sdk.ContextSource` | SemVer-governed |
| `ContextView` | `leanctx_sdk.ContextView` | SemVer-governed |
| `ContextPlan` | `leanctx_sdk.ContextPlan` | SemVer-governed |
| `ContextReceipt` | `leanctx_sdk.ContextReceipt` | SemVer-governed |

Supporting public protocols, value types, engine adapters, and exceptions are
documented in the public-surface manifest. They support the five primitives but
do not create additional product primitives.

## Preview — usable, explicitly non-GA

Local-context capabilities are public Preview under `leanctx_sdk.preview`:

| Capability | Primary symbol | Contract |
| --- | --- | --- |
| Durable local workspace | `ContextWorkspace` | Preview API |
| Portable checkpoint | `ContextCheckpoint` | Preview API |
| Parallel local delta | `ContextDelta` | Preview API |
| Local handoff | `ContextHandoff` | Preview API |

Forking is the narrow `ContextWorkspace.fork` operation; `WorkspaceForkV1` is
its versioned Preview record, not a fifth primary capability.

Preview means the implementation is tested and usable for evaluation or pilots,
while its API may change in a minor release before GA. Meaningful changes get
migration notes; Preview has no long deprecation guarantee. Preview is not
Stable and does not expand the five-primitive Stable contract.

Engine-dependent package operations (`seal_checkpoint_package`,
`seed_workspace_from_package`, `migrate_snapshot_v1`, and their inspection/
engine classes) are **Preview** and require the supported public Engine release
declared in `COMPATIBILITY.md`.

## Private research — not shipped as SDK API

P8 Cloud Receipt Board and P9 Governed Optimization remain private research.
They are not importable, documented as available, packaged, or implied by the
SDK.

Research may be promoted only through an explicit product decision, threat and
privacy review, public-contract freeze, implementation proof, and a new status
map. No Cloud service is included in SDK v1.

## Status language

- **Stable**: SemVer compatibility commitment for production use.
- **Preview**: tested evaluation surface; non-GA and changeable with notice.
- **Internal**: implementation detail; no compatibility commitment.
- **Private research**: excluded from public artifacts and product claims.

Unknown or unlisted symbols are Internal. Marketing, examples, docstrings, and
release notes must use these exact status terms.
