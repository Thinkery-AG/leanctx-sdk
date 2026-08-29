# LeanCTX Public/Private Boundary

## Public artifacts

- LeanCTX Engine: Apache-2.0 open-source runtime and protocol implementation.
- LeanCTX SDK: source-available `leanctx-sdk` package with Stable and Preview
  surfaces defined by the manifest. Exact license and artifact identities are
  bound by the protected release gate.
- Neutral wire schemas needed to use the public Engine/SDK boundary, when
  separately reviewed and intentionally published.

The SDK depends on the Engine boundary. The Engine does not depend on the SDK
or any private service. Public clients must remain usable without Cloud access.

## Private artifacts

The following remain private unless a later written promotion decision names
the exact artifact and license:

- P8 Cloud Receipt Board implementation and service contracts.
- P9 Governed Optimization implementation, policy internals, and proof data.
- Tenant, identity, billing, entitlement, hosted-control-plane, commercial
  operations, private telemetry, credentials, and security operations.
- Internal roadmaps, swarm transcripts, research reports, acceptance packets,
  threat-model working papers, and unpublished evidence bundles.

Private artifacts must not enter source distributions, wheels, generated docs,
examples, test fixtures, package metadata, repository history, or release
attachments. Public claims must not imply that a private capability ships.

## Dependency and data rules

1. Allowed direction: SDK → public Engine interface.
2. Forbidden directions: Engine → SDK; public SDK → private service module;
   Stable → Preview; public artifact → private proof path.
3. Credentials and tenant identifiers never cross a local API accidentally.
4. Receipts expose only the documented public schema and apply redaction before
   persistence or transport.
5. A neutral schema is not automatically public merely because public code can
   serialize a related value; publication requires an explicit allowlist entry.
6. Build jobs fail on forbidden imports, private path names, internal markers,
   secrets, or unapproved files in wheel/sdist inventories.

The only supported Preview import is `leanctx_sdk.preview`. Historical
`leanctx_product_sdk.research` paths are migration inputs, not public aliases.
Engine-dependent package operations stay Internal until the Engine release gate
closes.

## Promotion gate

Moving any item from Private to Preview or Stable requires all of:

- named customer or product need;
- explicit owner and status decision;
- privacy, security, abuse, and multi-tenant threat review;
- frozen public contract and migration policy;
- implementation, deterministic tests, package-purity proof, and docs;
- license and publication approval for the exact artifact.

Absence from this boundary is not permission. Unclassified material is Private.
