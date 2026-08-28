# LeanCTX SDK v1 Status Map

This map is normative for the `leanctx-sdk` 1.0.0 release candidate. A symbol is
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

P5–P7 are public Preview capabilities under `leanctx_sdk.preview`:

| Capability | Primary symbol | Evidence authority |
| --- | --- | --- |
| Durable local workspace | `ContextWorkspace` | `ba7241aa04ac1fda90f86437df90fbf03c1bf1ab` |
| Portable checkpoint | `ContextCheckpoint` | `d8695ebd87d5ab2a3dd40d87e9a98b67b15ed1cb` |
| Isolated local fork | `ContextFork` | `e3790afa5b4a7820073a6e3a82d715f14e24719d` |
| Parallel local delta | `ContextDelta` | `e3790afa5b4a7820073a6e3a82d715f14e24719d` |
| Local handoff | `ContextHandoff` | `e3790afa5b4a7820073a6e3a82d715f14e24719d` |

Preview means the implementation is tested and usable for evaluation or pilots,
while its API may change in a minor release before GA. Meaningful changes get
migration notes; Preview has no long deprecation guarantee. Preview is not
Stable and does not expand the five-primitive Stable contract.

Engine-dependent package operations (`seal_checkpoint_package`,
`seed_workspace_from_package`, `migrate_snapshot_v1`, and their inspection/
engine classes) are **Internal** pending a supported Engine release/tag/package;
they are not Preview promises.

## Private research — not shipped as SDK API

P8 Cloud Receipt Board and P9 Governed Optimization remain private research.
They are not importable, documented as available, packaged, or implied by the
SDK. Their evidence authorities are:

- P8: `02381fea98c43f3e8c3ba118341740649a411015`
- P9: `e2b0d355d64e177b497266c9a59295abe0aae105`

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
