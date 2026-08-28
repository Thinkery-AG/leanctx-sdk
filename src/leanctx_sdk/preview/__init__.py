"""Preview local-context APIs.

These APIs are usable for evaluation and pilots but may change in minor
releases. They are not covered by the Stable v1 compatibility guarantee.
"""

from ..errors import (
    WorkspaceAlreadyExistsError,
    WorkspaceConflictError,
    WorkspaceCorruptError,
    WorkspaceError,
    WorkspaceIOError,
    WorkspaceIncompatibleError,
    WorkspaceLifecycleError,
    WorkspaceLockError,
    WorkspaceNotFoundError,
    WorkspacePolicyError,
    WorkspaceSensitiveDataError,
    WorkspaceValidationError,
)
from ..workspace import (
    ContextCheckpointV2,
    ContextWorkspace,
    PackagePin,
    ProjectContext,
    ProjectContextEntry,
    SourceAnchor,
    SourceFreshness,
    SourceRecovery,
    SourceRevision,
    SourceScope,
    SourceTrust,
    WorkspaceIdentity,
    WorkspacePolicy,
    WorkspaceReceipt,
    WorkspaceSessionAttachment,
    WorkspaceStatus,
)
from ..parallel_context import (
    ConflictEntryV1,
    ConflictReportV1,
    ContextDeltaV1,
    ContextHandoffV1,
    DeltaItemV1,
    EvidenceRefV1,
    ForkLineageV1,
    HandoffAdmissionV1,
    NarrowReconciliationV1,
    PolicyInheritanceV1,
    WorkspaceForkV1,
    WorkspaceStateRefV1,
)

ContextCheckpoint = ContextCheckpointV2
ContextFork = WorkspaceForkV1
ContextDelta = ContextDeltaV1
ContextHandoff = ContextHandoffV1

__all__ = [
    "ContextCheckpointV2",
    "ContextCheckpoint",
    "ContextFork",
    "ConflictEntryV1",
    "ConflictReportV1",
    "ContextWorkspace",
    "ContextDeltaV1",
    "ContextDelta",
    "ContextHandoffV1",
    "ContextHandoff",
    "DeltaItemV1",
    "EvidenceRefV1",
    "ForkLineageV1",
    "HandoffAdmissionV1",
    "PackagePin",
    "NarrowReconciliationV1",
    "PolicyInheritanceV1",
    "ProjectContext",
    "ProjectContextEntry",
    "SourceAnchor",
    "SourceFreshness",
    "SourceRecovery",
    "SourceRevision",
    "SourceScope",
    "SourceTrust",
    "WorkspaceAlreadyExistsError",
    "WorkspaceConflictError",
    "WorkspaceCorruptError",
    "WorkspaceError",
    "WorkspaceIOError",
    "WorkspaceIdentity",
    "WorkspaceForkV1",
    "WorkspaceIncompatibleError",
    "WorkspaceLifecycleError",
    "WorkspaceLockError",
    "WorkspaceNotFoundError",
    "WorkspacePolicy",
    "WorkspacePolicyError",
    "WorkspaceReceipt",
    "WorkspaceSensitiveDataError",
    "WorkspaceSessionAttachment",
    "WorkspaceStatus",
    "WorkspaceStateRefV1",
    "WorkspaceValidationError",
]
