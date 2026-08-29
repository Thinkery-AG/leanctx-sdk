# Versioning Policy

`thinkery-leanctx-sdk` follows Semantic Versioning for the Stable public surface listed
in `PUBLIC-SURFACE-MANIFEST.md`.

## Stable surface

- **PATCH**: compatible defect, security, performance, documentation, or
  packaging correction.
- **MINOR**: backward-compatible Stable additions or newly promoted Preview
  capability.
- **MAJOR**: removal, rename, incompatible signature/behavior/schema change,
  or stricter requirement that can break a supported Stable consumer.

The five Stable product primitives and the Stable supporting API receive the
same compatibility protection. A bug fix may reject behavior that was unsafe,
undocumented, or already specified as invalid; release notes must identify the
change and its security or correctness basis.

## Preview surface

`leanctx_sdk.preview` is not covered by the Stable compatibility guarantee.
Preview may change in a MINOR release, but every material change must include:

- release-note entry and before/after import or call example;
- migration path or explicit reason one cannot be safe;
- updated Preview contract and installed-wheel tests;
- status review confirming the Stable root remains unchanged.

Preview changes must not be smuggled into PATCH releases when they alter an
accepted input, output, persisted format, or observable failure mode. Removing
a Preview capability requires at least one prior release notice unless a
security issue requires immediate removal.

## Engine and persisted-data compatibility

SDK releases declare supported Engine interface, schema, and transport versions
in `COMPATIBILITY.md`. Widening a compatible range is MINOR or PATCH according
to user impact; dropping a previously supported Stable combination is MAJOR.
Persisted Preview formats may evolve only through explicit versioning,
validation, and tested migration or fail-closed rejection.

## Status promotion

Promotion from Preview to Stable requires a frozen public contract, product and
security review, clean-wheel proof, migration policy, and an updated status map.
Private research cannot be versioned into the public package without first
passing the promotion gate in `PUBLIC-PRIVATE-BOUNDARY.md`.
