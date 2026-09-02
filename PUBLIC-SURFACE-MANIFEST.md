# LeanCTX SDK v1 Public Surface Manifest

This manifest is the allowlist for `thinkery-leanctx-sdk` 1.1.0. Public import tests and
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

## Stable Agent Tools

| Symbol | Module | Status | Since | Compatibility | Owner |
| --- | --- | --- | --- | --- | --- |
| `AgentContext` | `leanctx_sdk` | STABLE | 1.1.0 | SemVer | SDK API owner |
| `AsyncAgentContext` | `leanctx_sdk` | STABLE | 1.1.0 | SemVer | SDK API owner |
| `AgentPermissions` | `leanctx_sdk` | STABLE | 1.1.0 | SemVer | SDK API owner |
| `ExecutionPolicy` | `leanctx_sdk` | STABLE | 1.1.0 | SemVer | SDK API owner |
| `ReadMode` | `leanctx_sdk` | STABLE | 1.1.0 | SemVer | SDK API owner |
| `ToolResult` | `leanctx_sdk` | STABLE | 1.1.0 | SemVer | SDK API owner |
| `AgentMetrics` | `leanctx_sdk` | STABLE | 1.1.0 | SemVer | SDK API owner |

These symbols implement the separate Agent Tools Interface. They do not add
Product primitives to the five-primitive lifecycle contract.

## Stable supporting surface

These root exports are public support contracts for the five primitives:

- Protocol/value types: `ContextFailure`, `ContextMeasurement`,
  `ContextReceiptLink`, `EngineStatus`, `FailureCode`, `Freshness`,
  `HostOutcome`, `Integrity`, `RecoveredSource`, `SessionState`.
- Engine adapters: `EngineClient`, `SubprocessEngineClient`.
- Version constants: `ENGINE_INTERFACE_VERSION`, `SCHEMA_VERSION`,
  `TRANSPORT_VERSION`, `AGENT_TOOLS_INTERFACE_VERSION`,
  `AGENT_TOOLS_SCHEMA_VERSION`, `AGENT_TOOLS_TRANSPORT_VERSION`,
  `SUPPORTED_AGENT_TOOLS_ENGINE_VERSION`; package version: `__version__`.
- Base and operational errors: `SDKError`, `ArtifactIntegrityError`,
  `CompatibilityError`, `ConfigurationError`, `EngineError`,
  `EngineExecutionError`, `EngineProtocolError`, `EngineRejected`,
  `EngineTimeout`, `EngineUnavailable`, `FrameworkCompatibilityError`,
  `FrameworkIntegrationError`, `PolicyAdmissionError`,
  `RecoveryUnavailableError`, `SessionStateError`, `SourceUnavailableError`,
  `UnsupportedEngineError`, `ValidationError`, `EngineCrashed`,
  `AgentPermissionError`, `UnsupportedCapabilityError`.

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

- Primary capabilities: `ContextWorkspace`, `ContextCheckpoint`,
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
- Checkpoint-package values: `CheckpointPackageInspection`,
  `LocalCheckpointPackageEngine`, `SnapshotV1Inspection`,
  `SnapshotV1MigrationProvenance`, `SnapshotV1MigrationResult`.
- Checkpoint-package operations: `migrate_snapshot_v1`,
  `seal_checkpoint_package`, `seed_workspace_from_package`.
- Workspace errors: `WorkspaceError`, `WorkspaceAlreadyExistsError`,
  `WorkspaceConflictError`, `WorkspaceCorruptError`, `WorkspaceIOError`,
  `WorkspaceIncompatibleError`, `WorkspaceLifecycleError`,
  `WorkspaceLockError`, `WorkspaceNotFoundError`, `WorkspacePolicyError`,
  `WorkspaceSensitiveDataError`, `WorkspaceValidationError`.

Status: Preview. Preview symbols are tested but may change before GA. The
package must expose no `research` alias in the release artifact.

The unversioned primary names are aliases over the frozen wire contracts:
`ContextCheckpoint` → `ContextCheckpointV2` (`leanctx.context-checkpoint/v2`),
`ContextDelta` → `ContextDeltaV1` (`leanctx.context-delta/v1`), and
`ContextHandoff` → `ContextHandoffV1` (`leanctx.context-handoff/v1`).
Versioned record names remain schema identifiers, not separate stability tiers.

## Excluded surface

- Hosted Cloud and governed-optimization symbols.
- Private service contracts, credentials, tenant logic, billing logic, policy
  internals, research datasets, proof bundles, and internal reports.
- Source modules not reachable through the root or Preview `__all__` allowlists.

## Release invariants

1. Root `__all__` equals Stable primitives, Agent Tools, and Stable supporting
   surface.
2. Preview `__all__` equals the Preview namespace list above.
3. Stable imports never depend on Preview modules.
4. Public modules never import private research or Cloud implementation.
5. Clean-wheel import and namespace-purity tests verify these invariants.
