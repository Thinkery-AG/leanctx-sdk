# LeanCTX SDK 1.0.0 Release Notes

Status: Stable release.

## Stable SDK v1

LeanCTX SDK 1.0.0 establishes five Stable product primitives:

- `ContextSession`
- `ContextSource`
- `ContextView`
- `ContextPlan`
- `ContextReceipt`

They are imported from `leanctx_sdk`. The Stable surface is typed,
provider-neutral, local-first, and governed by `SEMVER.md` and the public
surface manifest.

## Preview local context

The release includes tested local-context capabilities under the explicit
`leanctx_sdk.preview` namespace:

- `ContextWorkspace` for durable, project-scoped local context;
- `ContextCheckpoint` for portable, integrity-checked checkpoints;
- `ContextDelta` for bounded parallel local changes;
- `ContextHandoff` for explicit, evidence-linked handoff.

Forking remains available as the narrow `ContextWorkspace.fork` lifecycle
operation and the versioned `WorkspaceForkV1` Preview record.

Preview is usable for evaluation and pilots but is not GA. Preview APIs may
change in a MINOR release with release notes and migration guidance. Versioned
contract types remain available in the Preview namespace.

## Packaging identity

- Distribution: `leanctx-sdk`
- Import package: `leanctx_sdk`
- Candidate version: `1.0.0`
- Supported Python: 3.9–3.12
- Typed package marker: included

The old staging identity `leanctx-product-sdk-local` and import
`leanctx_product_sdk` are not aliases and are not shipped.

## Security and boundaries

- SDK-to-Engine dependency remains one-way and provider-neutral.
- Stable imports do not depend on Preview.
- Cloud Receipt Board and Governed Optimization research are absent from
  package imports and artifacts.
- Source, wheel, namespace, secret, provenance, and installed-artifact checks
  are release gates.

## Licensing

LeanCTX Engine remains Apache-2.0. LeanCTX SDK uses a separate source-available
license; commercial Production Use requires a written commercial agreement.
There is no automatic open-source conversion date.

The SDK license text is final. Final artifact hashes and the public repository,
tag, and registry identities remain gated on external account authority; until
then, publication commands are intentionally not advertised.

## Not included

- hosted Cloud service or Cloud control plane;
- Receipt Board or Governed Optimization public APIs or production claims;
- automatic migration from historical staging packages;
- publication before all release identities and account bindings are verified.
