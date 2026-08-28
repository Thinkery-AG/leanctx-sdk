# LeanCTX SDK v1 Public Surface Manifest

This manifest is the allowlist for `leanctx-sdk` 1.0.0. Public import tests and
wheel inspection must agree with it before release. Anything not listed is
Internal unless a later version updates this file deliberately.

## Stable product primitives

| Symbol | Module | Status | Since | Compatibility | Owner |
| --- | --- | --- | --- | --- | --- |
| `ContextSession` | `leanctx_sdk` | STABLE | 1.0.0 | SemVer | SDK API owner |
| `ContextSource` | `leanctx_sdk` | STABLE | 1.0.0 | SemVer | SDK API owner |
| `ContextView` | `leanctx_sdk` | STABLE | 1.0.0 | SemVer | SDK API owner |
| `ContextPlan` | `leanctx_sdk` | STABLE | 1.0.0 | SemVer | SDK API owner |
| `ContextReceipt` | `leanctx_sdk` | STABLE | 1.0.0 | SemVer | SDK API owner |

Status: Stable. These are the only product primitives covered by the v1
five-primitive compatibility promise.

## Stable supporting surface

These root exports are public support contracts for the five primitives:

- Protocol/value types: `ContextFailure`, `ContextMeasurement`,
  `ContextReceiptLink`, `EngineStatus`, `FailureCode`, `Freshness`,
  `HostOutcome`, `Integrity`, `RecoveredSource`, `SessionState`.
- Engine adapters: `EngineClient`, `SubprocessEngineClient`.
- Version constants: `ENGINE_INTERFACE_VERSION`, `SCHEMA_VERSION`,
  `TRANSPORT_VERSION`; package version: `__version__`.
- Base and operational errors: `SDKError`, `ArtifactIntegrityError`,
  `CompatibilityError`, `ConfigurationError`, `EngineError`,
  `EngineExecutionError`, `EngineProtocolError`, `EngineRejected`,
  `EngineTimeout`, `EngineUnavailable`, `FrameworkCompatibilityError`,
  `FrameworkIntegrationError`, `PolicyAdmissionError`,
  `RecoveryUnavailableError`, `SessionStateError`, `SourceUnavailableError`,
  `UnsupportedEngineError`, `ValidationError`.

Status: Stable supporting API. Changes follow SemVer but these symbols are not
additional product primitives.

The supporting symbols are exported from `leanctx_sdk` and inherit the same
owner. Their protocol/schema versions remain explicit; they do not authorize
Cloud, scheduler, or private-service behavior.

## Preview namespace

The following symbols are public only from `leanctx_sdk.preview`; they must not
be re-exported from the root package. All are `PREVIEW`, since 1.0.0, owned by
the SDK API owner, and may change in a minor release without the Stable v1
compatibility guarantee.

- Primary capabilities: `ContextWorkspace`, `ContextCheckpoint`, `ContextFork`,
  `ContextDelta`, `ContextHandoff`; versioned contracts `ContextCheckpointV2`,
  `ContextDeltaV1`, `ContextHandoffV1` remain public Preview aliases/types.
- Workspace values: `PackagePin`, `ProjectContext`, `ProjectContextEntry`,
  `SourceAnchor`, `SourceFreshness`, `SourceRecovery`, `SourceRevision`,
  `SourceScope`, `SourceTrust`, `WorkspaceIdentity`, `WorkspacePolicy`,
  `WorkspaceReceipt`, `WorkspaceSessionAttachment`, `WorkspaceStatus`.
- Parallel-context values: `ConflictEntryV1`, `ConflictReportV1`,
  `DeltaItemV1`, `EvidenceRefV1`, `ForkLineageV1`, `HandoffAdmissionV1`,
  `NarrowReconciliationV1`, `PolicyInheritanceV1`, `WorkspaceForkV1`,
  `WorkspaceStateRefV1`.
- Workspace errors: `WorkspaceError`, `WorkspaceAlreadyExistsError`,
  `WorkspaceConflictError`, `WorkspaceCorruptError`, `WorkspaceIOError`,
  `WorkspaceIncompatibleError`, `WorkspaceLifecycleError`,
  `WorkspaceLockError`, `WorkspaceNotFoundError`, `WorkspacePolicyError`,
  `WorkspaceSensitiveDataError`, `WorkspaceValidationError`.

Status: Preview. Preview symbols are tested but may change before GA. The
package must expose no `research` alias in the release artifact.

The unversioned primary names are aliases over the frozen wire contracts:
`ContextCheckpoint` → `ContextCheckpointV2` (`leanctx.context-checkpoint/v2`),
`ContextFork` → `WorkspaceForkV1` (`leanctx.workspace-fork/v1`),
`ContextDelta` → `ContextDeltaV1` (`leanctx.context-delta/v1`), and
`ContextHandoff` → `ContextHandoffV1` (`leanctx.context-handoff/v1`).
Versioned record names remain schema identifiers, not separate stability tiers.

## Internal Engine-dependent package surface

These implementation symbols are `INTERNAL`, have no public compatibility
promise, and remain excluded from the public Preview allowlist until a
supported Engine release/tag/package is available:

- `CheckpointPackageInspection`, `LocalCheckpointPackageEngine`,
  `SnapshotV1Inspection`, `SnapshotV1MigrationProvenance`,
  `SnapshotV1MigrationResult`.
- `migrate_snapshot_v1`, `seal_checkpoint_package`,
  `seed_workspace_from_package`.

They must not be re-exported from `leanctx_sdk.preview` or documented as
supported install/seed/seal operations. Their modules may be packaged when
required as Internal implementation support; packaging does not make them
public or grant a compatibility promise.

## Excluded surface

- P8 Cloud Receipt Board and P9 Governed Optimization symbols.
- Private service contracts, credentials, tenant logic, billing logic, policy
  internals, research datasets, proof bundles, and internal reports.
- Source modules not reachable through the root or Preview `__all__` allowlists.

## Release invariants

1. Root `__all__` equals Stable primitives plus Stable supporting surface.
2. Preview `__all__` equals the Preview namespace list above and excludes the
   Internal package surface.
3. Stable imports never depend on Preview modules.
4. Public modules never import private research or Cloud implementation.
5. Clean-wheel import and namespace-purity tests verify these invariants.
