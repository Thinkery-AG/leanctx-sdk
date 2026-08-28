import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from leanctx_sdk import ContextSource
from leanctx_sdk.preview import (
    ContextDeltaV1,
    ContextHandoffV1,
    ContextWorkspace,
    HandoffAdmissionV1,
    NarrowReconciliationV1,
    PackagePin,
    ProjectContextEntry,
    SourceAnchor,
    SourceFreshness,
    SourceRevision,
    SourceScope,
    SourceTrust,
    WorkspaceConflictError,
    WorkspaceForkV1,
    WorkspacePolicy,
    WorkspacePolicyError,
    WorkspaceSensitiveDataError,
    WorkspaceValidationError,
)
from leanctx_sdk.protocol import canonical_bytes


ENTRY_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ENTRY_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ENTRY_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
SHARED_DECISION = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _source_fixture(root):
    project = Path(root, "project")
    project.mkdir()
    content = b"source truth\n"
    Path(project, "source.txt").write_bytes(content)
    source = ContextSource("source.txt", project_root=str(project))
    anchor = SourceAnchor(
        "source-1",
        "filesystem",
        "file://source.txt",
        revision=SourceRevision("filesystem", "sha256:" + hashlib.sha256(content).hexdigest()),
        freshness=SourceFreshness("2026-08-28T00:00:00Z", "current"),
        trust=SourceTrust("local"),
        scope=SourceScope("project", "project"),
        engine_binding=source.to_dict(),
    )
    return source, anchor


def _base(root, *, policy=None):
    state = Path(root, "state")
    source, anchor = _source_fixture(root)
    workspace = ContextWorkspace.create(state, "parent", policy=policy)
    workspace.attach_source(anchor)
    workspace.commit_context(
        [ProjectContextEntry(ENTRY_A, "facts", "base fact", ("source-1",))]
    )
    return state, source, workspace, workspace.checkpoint()


class ParallelContextTests(unittest.TestCase):
    def test_fork_is_atomic_distinct_and_isolated_after_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            state, source, parent, checkpoint = _base(root)
            child_a = parent.fork(
                "implementer",
                from_checkpoint=checkpoint,
                source_bindings={"source-1": source},
            )
            child_b = parent.fork(
                "reviewer",
                from_checkpoint=checkpoint,
                source_bindings={"source-1": source},
            )
            self.assertEqual(len(list(Path(child_a._workspace_path, "events").glob("*.json"))), 2)
            fork_event = child_a._read_state().events[1]
            inherited_source = fork_event["payload"]["inherited_state"]["sources"][0]
            self.assertIsNone(inherited_source["engine_binding"])
            self.assertNotIn(str(Path(root, "project")), json.dumps(fork_event))
            self.assertNotEqual(parent.workspace_id, child_a.workspace_id)
            self.assertNotEqual(child_a.workspace_id, child_b.workspace_id)
            self.assertEqual(child_a.fork_lineage["parent_checkpoint_id"], checkpoint.checkpoint_id)
            self.assertEqual(
                child_a.project_context().entries,
                child_b.project_context().entries,
            )

            child_a.commit_context(
                [ProjectContextEntry(ENTRY_B, "decisions", "retry_limit=3", ("source-1",))]
            )
            self.assertEqual(len(parent.project_context().entries), 1)
            self.assertEqual(len(child_b.project_context().entries), 1)
            self.assertEqual(len(child_a.project_context().entries), 2)

            reopened_a = ContextWorkspace.open(state, child_a.workspace_id)
            reopened_b = ContextWorkspace.open(state, child_b.workspace_id)
            self.assertEqual(len(reopened_a.project_context().entries), 2)
            self.assertEqual(len(reopened_b.project_context().entries), 1)
            with self.assertRaisesRegex(WorkspaceValidationError, "explicitly rebound"):
                reopened_b.start_session("review", source_id="source-1", source=source)
            reopened_b.bind_source("source-1", source)

    def test_fork_policy_floor_rejects_relaxation_before_child_publication(self):
        with tempfile.TemporaryDirectory() as root:
            restricted = WorkspacePolicy(max_events=32, max_context_entries=16)
            state, _, parent, checkpoint = _base(root, policy=restricted)
            before = {path.name for path in Path(state, "workspaces").iterdir()}
            with self.assertRaises(WorkspacePolicyError):
                parent.fork(
                    "relaxed",
                    from_checkpoint=checkpoint,
                    policy=WorkspacePolicy(),
                )
            after = {path.name for path in Path(state, "workspaces").iterdir()}
            self.assertEqual(before, after)

    def test_delta_is_deterministic_and_reconciliation_surfaces_shared_key(self):
        with tempfile.TemporaryDirectory() as root:
            _, _, parent, checkpoint = _base(root)
            left = parent.fork("left", from_checkpoint=checkpoint)
            right = parent.fork("right", from_checkpoint=checkpoint)
            left.commit_context(
                [ProjectContextEntry(SHARED_DECISION, "decisions", "retry_limit=3")]
            )
            right.commit_context(
                [ProjectContextEntry(SHARED_DECISION, "decisions", "retry_limit=5")]
            )
            left_checkpoint = left.checkpoint()
            right_checkpoint = right.checkpoint()
            first = left.context_delta(checkpoint, left_checkpoint)
            second = left.context_delta(checkpoint, left_checkpoint)
            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual(first.delta_id, second.delta_id)
            self.assertEqual(ContextDeltaV1.from_dict(first.to_dict()), first)
            self.assertIsNotNone(first.target.fork_lineage)
            unverified = ContextDeltaV1.between(checkpoint, right_checkpoint)
            self.assertEqual(unverified.conflicts.ancestry, "unknown")
            reconciliation = left.narrow_reconciliation(
                right,
                checkpoint,
                left_checkpoint,
                right_checkpoint,
                mode="decisions",
                reconciliation_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            )
            self.assertEqual(reconciliation.result, "manual_required")
            self.assertEqual(
                NarrowReconciliationV1.from_dict(reconciliation.to_dict()),
                reconciliation,
            )
            self.assertEqual(len(reconciliation.conflicts.entries), 1)
            self.assertIsNotNone(reconciliation.left.fork_lineage)
            self.assertIsNotNone(reconciliation.right.fork_lineage)
            self.assertEqual(reconciliation.conflicts.entries[0].stable_key, SHARED_DECISION)

    def test_bounded_handoff_admission_apply_and_replay(self):
        from tests.test_sdk import FakeEngine

        with tempfile.TemporaryDirectory() as root:
            _, source, parent, checkpoint = _base(root)
            implementer = parent.fork("implementer", from_checkpoint=checkpoint)
            reviewer = parent.fork("reviewer", from_checkpoint=checkpoint)
            implementer.bind_source("source-1", source)
            with patch(
                "leanctx_sdk.session.SubprocessEngineClient",
                return_value=FakeEngine(),
            ):
                sender = implementer.start_session(
                    "implement bounded change",
                    source_id="source-1",
                    source=source,
                    session_id="p7-sender-session",
                    task_id="p7-sender-task",
                )
            sender.session.complete()
            implementer.commit_context(
                [
                    ProjectContextEntry(ENTRY_B, "facts", "finding", ("source-1",)),
                    ProjectContextEntry(ENTRY_C, "decisions", "decision", ("source-1",)),
                ],
                session=sender.session,
            )
            implementer_checkpoint = implementer.checkpoint()
            reviewer_checkpoint = reviewer.checkpoint()
            handoff = implementer.create_handoff(
                implementer_checkpoint,
                target_workspace_id=reviewer.workspace_id,
                target_role="reviewer",
                task="continue bounded review",
                entry_ids=[ENTRY_B, ENTRY_C],
                handoff_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
            )
            encoded = json.dumps(handoff.to_dict(), sort_keys=True)
            self.assertNotIn("source_lineage", handoff.to_dict())
            self.assertIsNotNone(handoff.to_dict()["source"]["fork_lineage"])
            self.assertNotIn("transcript", encoded.lower())
            self.assertNotIn("credentials", encoded.lower())
            self.assertIn("receipt_refs", encoded)
            admission = reviewer.admit_handoff(handoff, reviewer_checkpoint)
            self.assertEqual(admission.decision, "degraded")
            self.assertEqual(admission.source_result, "unavailable")
            self.assertEqual(
                HandoffAdmissionV1.evaluate(handoff, reviewer_checkpoint).decision,
                "rejected",
            )
            with self.assertRaises(WorkspaceConflictError):
                replace(admission, admission_digest="sha256:" + "0" * 64)
            receipt = reviewer.apply_handoff(
                handoff,
                receiver_checkpoint=reviewer_checkpoint,
                event_id="12121212-1212-4212-8212-121212121212",
            )
            self.assertEqual(reviewer.apply_handoff(handoff), receipt)
            values = {entry.value for entry in reviewer.project_context().entries}
            self.assertTrue({"finding", "decision"}.issubset(values))

            reviewer.bind_source("source-1", source)
            rebound_checkpoint = reviewer.checkpoint()
            admitted = reviewer.admit_handoff(handoff, rebound_checkpoint)
            self.assertEqual(admitted.decision, "admitted")
            accepted = implementer.narrow_reconciliation(
                reviewer,
                checkpoint,
                implementer_checkpoint,
                rebound_checkpoint,
                mode="accepted_handoff",
                accepted_handoff=handoff,
                admission=admitted,
            )
            self.assertEqual(accepted.selected_entry_ids, (ENTRY_B, ENTRY_C))
            with self.assertRaises(WorkspaceValidationError):
                implementer.narrow_reconciliation(
                    reviewer,
                    checkpoint,
                    implementer_checkpoint,
                    rebound_checkpoint,
                    mode="accepted_handoff",
                )

            other = parent.fork("other-reviewer", from_checkpoint=checkpoint)
            other.bind_source("source-1", source)
            other_checkpoint = other.checkpoint()
            other_handoff = implementer.create_handoff(
                implementer_checkpoint,
                target_workspace_id=other.workspace_id,
                task="wrong reconciliation endpoint",
                entry_ids=[ENTRY_B],
            )
            other_admission = other.admit_handoff(other_handoff, other_checkpoint)
            self.assertEqual(other_admission.decision, "admitted")
            with self.assertRaises(WorkspaceConflictError):
                implementer.narrow_reconciliation(
                    reviewer,
                    checkpoint,
                    implementer_checkpoint,
                    rebound_checkpoint,
                    mode="accepted_handoff",
                    accepted_handoff=other_handoff,
                    admission=other_admission,
                )
            with patch(
                "leanctx_sdk.session.SubprocessEngineClient",
                return_value=FakeEngine(),
            ):
                attachment = reviewer.start_session(
                    "continue review",
                    source_id="source-1",
                    source=source,
                    session_id="p7-review-session",
                    task_id="p7-review-task",
                )
            self.assertEqual(attachment.session.state, "executing")

    def test_tamper_wrong_target_and_secret_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            _, context_source, parent, checkpoint = _base(root)
            source = parent.fork("source", from_checkpoint=checkpoint)
            target = parent.fork("target", from_checkpoint=checkpoint)
            source.commit_context([ProjectContextEntry(ENTRY_B, "facts", "safe")])
            source_checkpoint = source.checkpoint()
            target_checkpoint = target.checkpoint()
            wrong = source.create_handoff(
                source_checkpoint,
                target_workspace_id=str(uuid.uuid4()),
                task="review",
                entry_ids=[ENTRY_B],
            )
            self.assertEqual(target.admit_handoff(wrong, target_checkpoint).decision, "rejected")
            portable = parent.create_handoff(
                checkpoint,
                target_workspace_id=target.workspace_id,
                task="portable source proof",
                entry_ids=[ENTRY_A],
            )
            self.assertIsNone(portable.source_anchors[0]["engine_binding"])
            bound_raw = json.loads(json.dumps(portable.to_dict()))
            bound_raw["source_anchors"][0]["engine_binding"] = context_source.to_dict()
            unsigned = dict(bound_raw)
            unsigned.pop("handoff_digest")
            bound_raw["handoff_digest"] = "sha256:" + hashlib.sha256(
                b"leanctx.context-handoff.v1\n" + canonical_bytes(unsigned)
            ).hexdigest()
            with self.assertRaises(WorkspaceValidationError):
                ContextHandoffV1.from_dict(bound_raw)
            valid = source.create_handoff(
                source_checkpoint,
                target_workspace_id=target.workspace_id,
                task="valid sibling lineage",
                entry_ids=[ENTRY_B],
            )
            forged_raw = json.loads(json.dumps(valid.to_dict()))
            forged_lineage = forged_raw["source"]["fork_lineage"]
            forged_lineage["fork_id"] = str(uuid.uuid4())
            forged_lineage["fork_event_ref"] = "event-id:" + str(uuid.uuid4())
            unsigned = dict(forged_raw)
            unsigned.pop("handoff_digest")
            forged_raw["handoff_digest"] = "sha256:" + hashlib.sha256(
                b"leanctx.context-handoff.v1\n" + canonical_bytes(unsigned)
            ).hexdigest()
            forged = ContextHandoffV1.from_dict(forged_raw)
            self.assertEqual(
                target.admit_handoff(forged, target_checkpoint).decision,
                "rejected",
            )
            raw = dict(wrong.to_dict())
            raw["task"] = "Bearer " + "secret-value"
            with self.assertRaises(WorkspaceSensitiveDataError):
                ContextHandoffV1.from_dict(raw)
            raw = dict(wrong.to_dict())
            raw["task"] = "changed"
            with self.assertRaises(WorkspaceConflictError):
                ContextHandoffV1.from_dict(raw)

            child = parent.fork(
                "digest-proof",
                from_checkpoint=checkpoint,
                execution_ref="execution:original",
            )
            fork_raw = dict(child._read_state().events[1]["payload"]["fork"])
            fork_raw["execution_ref"] = "execution:tampered"
            with self.assertRaises(WorkspaceConflictError):
                WorkspaceForkV1.from_dict(fork_raw)
            with self.assertRaises(WorkspaceSensitiveDataError):
                parent.fork(
                    "secret-rejected",
                    from_checkpoint=checkpoint,
                    execution_ref="auth" + "_token=" + "secret-value",
                )

    def test_package_trust_is_exact_and_handoff_bound_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            _, _, parent, _ = _base(root)
            pin = PackagePin(
                "fixture",
                "1.0.0",
                "sha256:" + "1" * 64,
                "sha256:" + "2" * 64,
                "sha256:" + "3" * 64,
                "signed_valid",
                "4" * 64,
                "trusted",
            )
            parent.pin_package(pin)
            checkpoint = parent.checkpoint()
            child = parent.fork("child", from_checkpoint=checkpoint)
            child_checkpoint = child.checkpoint()
            self.assertEqual(child_checkpoint.package_pins, checkpoint.package_pins)
            self.assertEqual(child_checkpoint.package_lock_digest, checkpoint.package_lock_digest)
            with self.assertRaises(WorkspacePolicyError):
                child.create_handoff(
                    child_checkpoint,
                    target_workspace_id=str(uuid.uuid4()),
                    task="too broad",
                    entry_ids=[str(uuid.uuid4()) for _ in range(65)],
                )

    def test_two_process_handoff_reopen_rebind_and_session_continuation(self):
        with tempfile.TemporaryDirectory() as root:
            state, source, parent, checkpoint = _base(root)
            implementer = parent.fork("implementer", from_checkpoint=checkpoint)
            reviewer = parent.fork("reviewer", from_checkpoint=checkpoint)
            unrelated = ContextWorkspace.create(state, "unrelated")
            implementer.commit_context(
                [ProjectContextEntry(ENTRY_B, "decisions", "bounded transfer", ("source-1",))]
            )
            implementer_checkpoint = implementer.checkpoint()
            reviewer_checkpoint = reviewer.checkpoint()
            handoff = implementer.create_handoff(
                implementer_checkpoint,
                target_workspace_id=reviewer.workspace_id,
                task="continue in another process",
                entry_ids=[ENTRY_B],
            )
            handoff_path = Path(root, "handoff.json")
            handoff_path.write_text(
                json.dumps(handoff.to_dict(), sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            script = """
import json
import sys
from unittest.mock import patch
from leanctx_sdk import ContextSource
from leanctx_sdk.preview import ContextHandoffV1, ContextWorkspace
from tests.test_sdk import FakeEngine

state_root, workspace_id, checkpoint_id, handoff_path, project_root = sys.argv[1:]
workspace = ContextWorkspace.open(state_root, workspace_id)
checkpoint = workspace.get_checkpoint(checkpoint_id)
handoff = ContextHandoffV1.from_dict(json.loads(open(handoff_path, encoding="utf-8").read()))
receipt = workspace.apply_handoff(handoff, receiver_checkpoint=checkpoint)
source = ContextSource("source.txt", project_root=project_root)
workspace.bind_source("source-1", source)
with patch("leanctx_sdk.session.SubprocessEngineClient", return_value=FakeEngine()):
    attachment = workspace.start_session(
        "continue",
        source_id="source-1",
        source=source,
        session_id="p7-process-session",
        task_id="p7-process-task",
    )
print(json.dumps({
    "event_kind": receipt.event_kind,
    "session_state": attachment.session.state,
    "entry_count": len(workspace.project_context().entries),
}, sort_keys=True))
"""
            environment = dict(os.environ)
            if environment.get("LEANCTX_TEST_INSTALLED_PACKAGE") == "1":
                environment.pop("PYTHONPATH", None)
            else:
                environment["PYTHONPATH"] = "src"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(state),
                    reviewer.workspace_id,
                    reviewer_checkpoint.checkpoint_id,
                    str(handoff_path),
                    str(Path(root, "project")),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["event_kind"], "handoff_applied")
            self.assertEqual(result["session_state"], "executing")
            self.assertEqual(result["entry_count"], 2)
            self.assertEqual(len(parent.project_context().entries), 1)
            self.assertEqual(len(unrelated.project_context().entries), 0)
            reopened = ContextWorkspace.open(state, reviewer.workspace_id)
            self.assertEqual(reopened.status().session_count, 1)


if __name__ == "__main__":
    unittest.main()
