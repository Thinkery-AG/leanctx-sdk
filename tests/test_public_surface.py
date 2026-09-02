import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import leanctx_sdk  # noqa: E402
import leanctx_sdk.preview as preview  # noqa: E402


ROOT_EXPORTS = {
    "__version__",
    "AGENT_TOOLS_INTERFACE_VERSION",
    "AGENT_TOOLS_SCHEMA_VERSION",
    "AGENT_TOOLS_TRANSPORT_VERSION",
    "AgentContext",
    "AgentMetrics",
    "AgentPermissionError",
    "AgentPermissions",
    "AsyncAgentContext",
    "ArtifactIntegrityError",
    "CompatibilityError",
    "ConfigurationError",
    "ContextFailure",
    "ContextMeasurement",
    "ContextPlan",
    "ContextReceipt",
    "ContextReceiptLink",
    "ContextSession",
    "ContextSource",
    "ContextView",
    "ENGINE_INTERFACE_VERSION",
    "EngineClient",
    "EngineCrashed",
    "EngineError",
    "EngineExecutionError",
    "EngineProtocolError",
    "EngineRejected",
    "EngineStatus",
    "EngineTimeout",
    "EngineUnavailable",
    "ExecutionPolicy",
    "FailureCode",
    "Freshness",
    "FrameworkCompatibilityError",
    "FrameworkIntegrationError",
    "HostOutcome",
    "Integrity",
    "PolicyAdmissionError",
    "RecoveredSource",
    "ReadMode",
    "RecoveryUnavailableError",
    "SCHEMA_VERSION",
    "SDKError",
    "SessionState",
    "SessionStateError",
    "SourceUnavailableError",
    "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION",
    "SubprocessEngineClient",
    "ToolResult",
    "TRANSPORT_VERSION",
    "UnsupportedEngineError",
    "UnsupportedCapabilityError",
    "ValidationError",
}

PREVIEW_EXPORTS = {
    "CheckpointPackageInspection",
    "ConflictEntryV1",
    "ConflictReportV1",
    "ContextCheckpoint",
    "ContextCheckpointV2",
    "ContextDelta",
    "ContextDeltaV1",
    "ContextHandoff",
    "ContextHandoffV1",
    "ContextWorkspace",
    "DeltaItemV1",
    "EvidenceRefV1",
    "ForkLineageV1",
    "HandoffAdmissionV1",
    "LocalCheckpointPackageEngine",
    "NarrowReconciliationV1",
    "PackagePin",
    "PolicyInheritanceV1",
    "ProjectContext",
    "ProjectContextEntry",
    "SourceAnchor",
    "SourceFreshness",
    "SourceRecovery",
    "SourceRevision",
    "SourceScope",
    "SourceTrust",
    "SnapshotV1Inspection",
    "SnapshotV1MigrationProvenance",
    "SnapshotV1MigrationResult",
    "WorkspaceAlreadyExistsError",
    "WorkspaceConflictError",
    "WorkspaceCorruptError",
    "WorkspaceError",
    "WorkspaceForkV1",
    "WorkspaceIOError",
    "WorkspaceIdentity",
    "WorkspaceIncompatibleError",
    "WorkspaceLifecycleError",
    "WorkspaceLockError",
    "WorkspaceNotFoundError",
    "WorkspacePolicy",
    "WorkspacePolicyError",
    "WorkspaceReceipt",
    "WorkspaceSensitiveDataError",
    "WorkspaceSessionAttachment",
    "WorkspaceStateRefV1",
    "WorkspaceStatus",
    "WorkspaceValidationError",
    "migrate_snapshot_v1",
    "seal_checkpoint_package",
    "seed_workspace_from_package",
}


class PublicSurfaceTests(unittest.TestCase):
    def test_root_exports_match_stable_manifest(self):
        self.assertEqual(set(leanctx_sdk.__all__), ROOT_EXPORTS)

    def test_preview_exports_match_preview_manifest(self):
        self.assertEqual(set(preview.__all__), PREVIEW_EXPORTS)

    def test_preview_aliases_bind_versioned_contracts(self):
        self.assertIs(preview.ContextCheckpoint, preview.ContextCheckpointV2)
        self.assertIs(preview.ContextDelta, preview.ContextDeltaV1)
        self.assertIs(preview.ContextHandoff, preview.ContextHandoffV1)

    def test_historical_research_namespaces_are_absent(self):
        self.assertIsNone(importlib.util.find_spec("leanctx_sdk.research"))
        self.assertIsNone(importlib.util.find_spec("leanctx_product_sdk"))

    def test_stable_import_does_not_load_preview_or_workspace_modules(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SRC)
        code = """
import sys
import leanctx_sdk
forbidden = {
    'leanctx_sdk.preview',
    'leanctx_sdk.workspace',
    'leanctx_sdk.parallel_context',
    'leanctx_sdk.checkpoint_package',
}
loaded = forbidden.intersection(sys.modules)
if loaded:
    raise SystemExit('Stable import loaded: ' + ', '.join(sorted(loaded)))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_no_private_research_names_are_exported(self):
        exported = {
            name.casefold() for name in (*leanctx_sdk.__all__, *preview.__all__)
        }
        forbidden_fragments = (
            "cloud",
            "receiptboard",
            "governed",
            "optimization",
            "autotune",
        )
        self.assertFalse(
            {
                name
                for name in exported
                if any(part in name for part in forbidden_fragments)
            }
        )

    def test_preview_workspace_example_runs_provider_free(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SRC)
        result = subprocess.run(
            [sys.executable, str(ROOT / "examples/preview_workspace.py")],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
