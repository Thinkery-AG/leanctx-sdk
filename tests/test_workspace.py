import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from leanctx_sdk import (
    ContextSession,
    ContextSource,
)
from leanctx_sdk.preview import (
    ContextCheckpointV2,
    ContextWorkspace,
    PackagePin,
    ProjectContextEntry,
    SourceAnchor,
    SourceFreshness,
    SourceRevision,
    SourceScope,
    SourceTrust,
    WorkspaceConflictError,
    WorkspaceCorruptError,
    WorkspaceIncompatibleError,
    WorkspaceLifecycleError,
    WorkspacePolicy,
    WorkspacePolicyError,
    WorkspaceSensitiveDataError,
    WorkspaceValidationError,
)
from leanctx_sdk.checkpoint_package import (
    CheckpointPackageInspection,
    LocalCheckpointPackageEngine,
    SnapshotV1MigrationProvenance,
    SnapshotV1Inspection,
    migrate_snapshot_v1,
    seal_checkpoint_package,
    seed_workspace_from_package,
)
from leanctx_sdk.protocol import canonical_bytes


def _anchor(root, source_id="source-1", *, path="source.txt"):
    source = ContextSource(path, project_root=root)
    return SourceAnchor(
        source_id,
        "filesystem",
        "file://" + path,
        freshness=SourceFreshness("2026-08-26T00:00:00Z", "current"),
        trust=SourceTrust("local"),
        scope=SourceScope("project", "project"),
        engine_binding=source.to_dict(),
    )


def _rewrite_event(workspace, mutate, *, kind=None):
    events = Path(workspace._workspace_path) / "events"
    event_path = None
    record = None
    for candidate in sorted(events.glob("*.json")):
        candidate_record = json.loads(candidate.read_text(encoding="utf-8"))
        if kind is None or candidate_record["kind"] == kind:
            event_path = candidate
            record = candidate_record
            break
    if event_path is None or record is None:
        raise AssertionError("requested event was not found")
    sequence_prefix = event_path.name.split("-", 1)[0]
    mutate(record)
    unsigned = {key: value for key, value in record.items() if key != "event_digest"}
    digest = "sha256:" + hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
    record["event_digest"] = digest
    replacement = events / (
        sequence_prefix + "-" + digest.removeprefix("sha256:") + ".json"
    )
    event_path.rename(replacement)
    replacement.write_bytes(canonical_bytes(record) + b"\n")


def _rewrite_first_event(workspace, mutate):
    _rewrite_event(workspace, mutate)


class _CheckpointPackageEngine:
    def __init__(self, inspection):
        self.inspection = inspection
        self.seal_calls = []

    def seal(self, checkpoint, destination, **options):
        self.seal_calls.append((checkpoint, destination, options))
        return self.inspection

    def inspect(self, package_path):
        return self.inspection

    def inspect_snapshot_v1(self, snapshot_path):
        return SnapshotV1Inspection(
            "d" * 64,
            "sha256:" + "e" * 64,
            "f" * 64,
        )


def _package_inspection(checkpoint, *, signed=True, migration=None):
    return CheckpointPackageInspection(
        "/portable/checkpoint.ctxpkg",
        "workspace-checkpoint",
        "1.0.0",
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        "signed_valid" if signed else "unsigned",
        "c" * 64 if signed else None,
        checkpoint,
        migration,
        (),
    )


def _package_pin():
    return PackagePin(
        "dependency",
        "1.0.0",
        "sha256:" + "3" * 64,
        "sha256:" + "4" * 64,
        "sha256:" + "5" * 64,
        "signed_valid",
        "6" * 64,
        "trusted",
    )


def _rehash_checkpoint_envelope(value):
    unsigned = {key: item for key, item in value.items() if key != "envelope_digest"}
    value["envelope_digest"] = (
        "sha256:"
        + hashlib.sha256(
            b"leanctx.checkpoint.envelope.v2\n" + canonical_bytes(unsigned)
        ).hexdigest()
    )


def _planned_session(
    root,
    source_id="source-1",
    *,
    path="source.txt",
    session_id=None,
    task_id=None,
    task="inspect",
    engine=None,
):
    from tests.test_sdk import FakeEngine

    source_path = Path(root, path)
    if not source_path.exists() and not source_path.is_symlink():
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("workspace source\n", encoding="utf-8")
    source = ContextSource(path, project_root=root)
    session = ContextSession(
        task,
        project_root=root,
        session_id=session_id,
        task_id=task_id,
        engine=engine if engine is not None else FakeEngine(),
    )
    session.plan(source)
    return session, source


class WorkspaceTests(unittest.TestCase):
    def test_public_projections_are_detached_json_objects(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            anchor = _anchor(root)
            source_receipt = workspace.attach_source(anchor)
            entry = ProjectContextEntry(str(uuid.uuid4()), "facts", "durable fact")
            context_receipt = workspace.commit_context([entry])
            context = workspace.project_context()
            status = workspace.status()
            projections = (
                workspace.identity.to_dict(),
                status.policy.to_dict(),
                workspace.creation_receipt.to_dict(),
                anchor.to_dict(),
                source_receipt.to_dict(),
                entry.to_dict(),
                context_receipt.to_dict(),
                context.to_dict(),
                status.to_dict(),
            )
            for projection in projections:
                self.assertIsInstance(projection, dict)
                json.dumps(projection, sort_keys=True)

            detached = status.to_dict()
            detached["lifecycle"] = "tampered"
            detached["identity"]["name"] = "tampered"
            self.assertEqual(workspace.status().lifecycle, "active")
            self.assertEqual(workspace.identity.name, "fixture")

    def test_value_validation_and_canonical_identity(self):
        with self.assertRaises(WorkspaceValidationError):
            SourceFreshness("2026-08-26T00:00:00+01:00", "current")
        with self.assertRaises(WorkspaceValidationError):
            SourceFreshness("2026-08-26T00:00:00Z", "current", "2026-08-25T00:00:00Z")
        with self.assertRaises(WorkspaceValidationError):
            ContextWorkspace.create(
                tempfile.gettempdir(), "x", workspace_id="../escape"
            )
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            with self.assertRaises(WorkspaceSensitiveDataError) as raised:
                workspace.commit_context(
                    [
                        ProjectContextEntry(
                            str(uuid.uuid4()), "facts", "api_key = secret"
                        )
                    ]
                )
        self.assertEqual(str(raised.exception), "workspace_sensitive_data:value")

    def test_create_open_source_session_context_and_terminal_lifecycle(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            self.assertEqual(workspace.status().lifecycle, "active")
            reopened = ContextWorkspace.open(root, workspace.workspace_id)
            self.assertEqual(reopened.identity.state_id, workspace.identity.state_id)
            anchor = _anchor(root)
            source_receipt = reopened.attach_source(anchor)
            self.assertEqual(source_receipt.source_ids, ("source-1",))
            session, source = _planned_session(root)
            attached = reopened.attach_session(session, source_ids=["source-1"])
            self.assertIs(attached.session, session)
            session.prepare()
            engine_receipt = session.complete()
            entry = ProjectContextEntry(
                str(uuid.uuid4()),
                "facts",
                "deliberate fact",
                source_ids=("source-1",),
                session_id=session.session_id,
            )
            receipt = reopened.commit_context([entry], session=session)
            expected = ProjectContextEntry(
                entry.entry_id,
                entry.category,
                entry.value,
                source_ids=entry.source_ids,
                session_id=entry.session_id,
                receipt_refs=(engine_receipt.receipt_link.receipt_ref,),
                recovery_refs=(engine_receipt.recovery_ref,),
            )
            self.assertEqual(
                receipt.engine_receipt_refs, (engine_receipt.receipt_link.receipt_ref,)
            )
            context = reopened.project_context()
            self.assertEqual(context.entries, (expected,))
            self.assertEqual(context.filtered_count, 0)
            self.assertEqual(context.omitted_by_bounds, 0)
            reopened.complete()
            self.assertEqual(
                ContextWorkspace.open(root, workspace.workspace_id).status().lifecycle,
                "completed",
            )
            with self.assertRaises(WorkspaceLifecycleError):
                reopened.commit_context(
                    [ProjectContextEntry(str(uuid.uuid4()), "facts", "late")]
                )

    def test_idempotent_event_retry_conflict_and_policy_tightening(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            anchor = _anchor(root)
            event_id = str(uuid.uuid4())
            first = workspace.attach_source(anchor, event_id=event_id)
            self.assertEqual(workspace.attach_source(anchor, event_id=event_id), first)
            with self.assertRaises(WorkspaceConflictError):
                workspace.attach_source(_anchor(root, "source-2"), event_id=event_id)
            workspace.commit_context(
                [ProjectContextEntry(str(uuid.uuid4()), "facts", "current fact")]
            )
            with self.assertRaises(WorkspacePolicyError):
                workspace.tighten_policy(WorkspacePolicy(max_events=1))
            with self.assertRaises(WorkspacePolicyError):
                workspace.tighten_policy(
                    WorkspacePolicy(allowed_categories=("decisions",))
                )

    def test_checkpoint_is_immutable_and_restore_is_append_only(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            source_bytes = b"checkpoint source\n"
            Path(root, "source.txt").write_bytes(source_bytes)
            workspace.attach_source(
                replace(
                    _anchor(root),
                    revision=SourceRevision(
                        "filesystem",
                        "sha256:" + hashlib.sha256(source_bytes).hexdigest(),
                    ),
                )
            )
            first = ProjectContextEntry(
                str(uuid.uuid4()),
                "facts",
                "checkpoint fact",
                source_ids=("source-1",),
            )
            workspace.commit_context([first])
            checkpoint = workspace.checkpoint(
                checkpoint_id=str(uuid.uuid4()),
                event_id=str(uuid.uuid4()),
            )
            self.assertIsInstance(checkpoint, ContextCheckpointV2)
            self.assertNotEqual(
                checkpoint.checkpoint_id,
                checkpoint.state_digest.removeprefix("sha256:"),
            )
            self.assertEqual(
                checkpoint.state_digest,
                workspace.project_context().state_digest,
            )
            checkpoint_event = next(
                path
                for path in (Path(workspace._workspace_path) / "events").glob("*.json")
                if json.loads(path.read_text(encoding="utf-8"))["kind"]
                == "checkpoint_created"
            )
            checkpoint_bytes = checkpoint_event.read_bytes()
            workspace.commit_context(
                [ProjectContextEntry(str(uuid.uuid4()), "facts", "later fact")]
            )
            diverged_count = workspace.status().event_count
            workspace.restore(checkpoint)
            restored = workspace.project_context()
            self.assertEqual(restored.entries, (first,))
            self.assertEqual(restored.state_digest, checkpoint.state_digest)
            self.assertEqual(workspace.status().event_count, diverged_count + 1)
            self.assertEqual(checkpoint_event.read_bytes(), checkpoint_bytes)

            reopened = ContextWorkspace.open(root, workspace.workspace_id)
            self.assertEqual(
                reopened.get_checkpoint(checkpoint.checkpoint_id), checkpoint
            )
            first_restore_head = reopened.status().state_digest
            reopened.restore(checkpoint)
            self.assertEqual(
                reopened.project_context().state_digest,
                checkpoint.state_digest,
            )
            self.assertNotEqual(reopened.status().state_digest, first_restore_head)

    def test_checkpoint_rejects_cross_workspace_policy_downgrade_and_tampering(self):
        with tempfile.TemporaryDirectory() as root:
            first = ContextWorkspace.create(root, "first")
            second = ContextWorkspace.create(root, "second")
            checkpoint = first.checkpoint()
            with self.assertRaises(WorkspaceConflictError):
                second.restore(checkpoint)

            first.tighten_policy(WorkspacePolicy(max_context_entries=128))
            with self.assertRaises(WorkspacePolicyError):
                first.restore(checkpoint)

            source_workspace = ContextWorkspace.create(root, "source")
            source_bytes = b"stable source\n"
            Path(root, "source.txt").write_bytes(source_bytes)
            source_workspace.attach_source(
                replace(
                    _anchor(root),
                    revision=SourceRevision(
                        "filesystem",
                        "sha256:" + hashlib.sha256(source_bytes).hexdigest(),
                    ),
                )
            )
            source_checkpoint = source_workspace.checkpoint()
            Path(root, "source.txt").write_text("changed source\n", encoding="utf-8")
            with self.assertRaises(WorkspaceConflictError):
                source_workspace.restore(source_checkpoint)

            isolated = ContextWorkspace.create(root, "isolated")
            isolated.checkpoint()
            _rewrite_event(
                isolated,
                lambda record: record["payload"]["checkpoint"].update(
                    {"state_digest": "sha256:" + ("0" * 64)}
                ),
                kind="checkpoint_created",
            )
            with self.assertRaises(WorkspaceIncompatibleError):
                ContextWorkspace.open(root, isolated.workspace_id)

    def test_checkpoint_rejects_rehashed_cross_field_tampering(self):
        with tempfile.TemporaryDirectory() as root:
            checkpoint = ContextWorkspace.create(root, "fixture").checkpoint()
            mutations = (
                lambda value: value.update({"policy_digest": "sha256:" + "0" * 64}),
                lambda value: value.update(
                    {"project_context_digest": "sha256:" + "1" * 64}
                ),
                lambda value: value.update(
                    {
                        "source_anchors": [
                            {
                                "source_id": "forged",
                                "kind": "filesystem",
                                "canonical_id": "file://forged",
                            }
                        ]
                    }
                ),
                lambda value: value.update(
                    {"recovery_refs": ["receipt:sha256:" + "2" * 64]}
                ),
                lambda value: value.update(
                    {
                        "package_pins": [
                            {"name": "forged", "digest": "sha256:" + "3" * 64}
                        ]
                    }
                ),
                lambda value: value.update(
                    {"package_lock_digest": "sha256:" + "4" * 64}
                ),
            )
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    payload = dict(checkpoint.to_dict())
                    mutate(payload)
                    _rehash_checkpoint_envelope(payload)
                    with self.assertRaises(WorkspaceCorruptError):
                        ContextCheckpointV2.from_dict(payload)

    def test_checkpoint_caps_semantics_and_verified_trust_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            checkpoint = workspace.checkpoint()
            oversized = checkpoint.to_dict()
            oversized["recovery_refs"] = [
                "recovery:sha256:" + f"{index:064x}" for index in range(4097)
            ]
            with self.assertRaises(WorkspacePolicyError):
                ContextCheckpointV2.from_dict(oversized)

            invalid_engine = checkpoint.to_dict()
            invalid_engine["engine_identity"]["interface_version"] = "99.0.0"
            with self.assertRaises(WorkspaceIncompatibleError):
                ContextCheckpointV2.from_dict(invalid_engine)

            Path(root, "source.txt").write_text("source\n", encoding="utf-8")
            forged = ContextWorkspace.create(root, "forged")
            forged.attach_source(_anchor(root))
            _rewrite_event(
                forged,
                lambda record: record["payload"]["anchor"]["trust"].update(
                    {
                        "level": "verified",
                        "evidence_refs": ["receipt:sha256:" + ("a" * 64)],
                    }
                ),
                kind="source_attached",
            )
            reopened = ContextWorkspace.open(root, forged.workspace_id)
            with self.assertRaises(WorkspacePolicyError):
                reopened.checkpoint()

    def test_replay_rejects_corrupt_and_unknown_schema(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            event_path = next(
                (Path(root) / "workspaces" / workspace.workspace_id / "events").glob(
                    "*.json"
                )
            )
            record = json.loads(event_path.read_text(encoding="utf-8"))
            record["schema_version"] = "leanctx.workspace-event/v99"
            event_path.write_text(
                json.dumps(record, separators=(",", ":")) + "\n", encoding="utf-8"
            )
            with self.assertRaises(WorkspaceIncompatibleError):
                ContextWorkspace.open(root, workspace.workspace_id)

            other = ContextWorkspace.create(root, "other")
            other_event = next(
                (Path(root) / "workspaces" / other.workspace_id / "events").glob(
                    "*.json"
                )
            )
            other_event.write_bytes(b"truncated\n")
            with self.assertRaises(WorkspaceCorruptError):
                ContextWorkspace.open(root, other.workspace_id)

    def test_two_process_reopen_and_adjacent_workspace_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            first = ContextWorkspace.create(root, "one")
            second = ContextWorkspace.create(root, "two")
            first.attach_source(_anchor(root))
            first.commit_context(
                [
                    ProjectContextEntry(
                        str(uuid.uuid4()),
                        "decisions",
                        "keep this deliberate decision",
                        source_ids=("source-1",),
                    )
                ]
            )
            script = (
                "import sys\n"
                "from leanctx_sdk.preview import ContextWorkspace\n"
                "w=ContextWorkspace.open(sys.argv[1], sys.argv[2])\n"
                "print(w.project_context().entries[0].value)\n"
            )
            output = subprocess.check_output(
                [sys.executable, "-c", script, root, first.workspace_id],
                text=True,
            )
            self.assertEqual(output.strip(), "keep this deliberate decision")
            self.assertEqual(second.project_context().entries, ())

    def test_security_sensitive_identity_url_and_replay_payload(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(WorkspaceSensitiveDataError):
                ContextWorkspace.create(root, "https://example.invalid/?token=secret")
            workspace = ContextWorkspace.create(root, "safe")
            _rewrite_first_event(
                workspace,
                lambda record: record["payload"]["identity"].update(
                    {"name": "https://example.invalid/?api_key=secret"}
                ),
            )
            with self.assertRaises(WorkspaceIncompatibleError):
                ContextWorkspace.open(root, workspace.workspace_id)

    def test_source_freshness_compares_instants_and_json_arrays_are_strict(self):
        with self.assertRaises(WorkspaceValidationError):
            SourceFreshness(
                "2026-08-26T00:00:00.9Z",
                "current",
                "2026-08-26T00:00:00.10Z",
            )
        with self.assertRaises(WorkspaceIncompatibleError):
            SourceTrust.from_dict({"level": "local", "evidence_refs": ()})
        with self.assertRaises(WorkspaceIncompatibleError):
            ProjectContextEntry.from_dict(
                {
                    "schema_version": "leanctx.project-context-entry/v1",
                    "entry_id": str(uuid.uuid4()),
                    "category": "facts",
                    "value": "x",
                    "source_ids": (),
                    "session_id": None,
                    "receipt_refs": [],
                    "recovery_refs": [],
                }
            )
        with self.assertRaises(WorkspaceIncompatibleError):
            WorkspacePolicy.from_dict(
                {
                    "schema_version": "leanctx.workspace-policy/v1",
                    "allowed_categories": tuple(sorted({"facts"})),
                    "max_events": 4,
                    "max_context_entries": 4,
                    "max_entry_bytes": 4096,
                    "max_context_bytes": 4096,
                    "max_sources": 4,
                    "max_sessions": 4,
                    "allow_external_sources": False,
                }
            )

    def test_replay_rejects_malformed_kind_sequence_and_nested_shapes(self):
        mutations = (
            lambda record: record.update({"kind": {"not": "a string"}}),
            lambda record: record.update({"sequence": 1.0}),
            lambda record: record.update({"sequence": True}),
            lambda record: record.update({"payload": []}),
            lambda record: record["payload"].update({"identity": []}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                with tempfile.TemporaryDirectory() as root:
                    workspace = ContextWorkspace.create(root, "fixture")
                    _rewrite_first_event(workspace, mutate)
                    with self.assertRaises(WorkspaceIncompatibleError):
                        ContextWorkspace.open(root, workspace.workspace_id)

    def test_crash_twin_is_accepted_but_arbitrary_event_hardlink_is_not(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            events = Path(workspace._workspace_path) / "events"
            event_path = next(events.glob("*.json"))
            twin = events / ".event-crash-twin"
            os.link(event_path, twin)
            self.assertEqual(
                ContextWorkspace.open(root, workspace.workspace_id).status().health,
                "healthy",
            )
            twin.unlink()
            arbitrary = events / "not-an-event"
            os.link(event_path, arbitrary)
            with self.assertRaises(WorkspaceCorruptError):
                ContextWorkspace.open(root, workspace.workspace_id)

    def test_create_race_serializes_same_uuid_collision(self):
        with tempfile.TemporaryDirectory() as root:
            workspace_id = str(uuid.uuid4())
            script = (
                "import sys\n"
                "from leanctx_sdk.preview import ContextWorkspace\n"
                "try:\n"
                "    ContextWorkspace.create(sys.argv[1], 'race', workspace_id=sys.argv[2])\n"
                "    print('created')\n"
                "except Exception as exc:\n"
                "    print(type(exc).__name__)\n"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, root, workspace_id],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                for _ in range(2)
            ]
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                results.append((process.returncode, stdout, stderr))
            self.assertEqual([result[0] for result in results], [0, 0])
            output = [result[1].strip() for result in results]
            self.assertEqual(output.count("created"), 1)
            self.assertEqual(output.count("WorkspaceAlreadyExistsError"), 1)

    def test_same_uuid_different_roots_does_not_cross_bind_session(self):
        with tempfile.TemporaryDirectory() as base:
            root_a = Path(base) / "a"
            root_b = Path(base) / "b"
            project = Path(base) / "project"
            root_a.mkdir()
            root_b.mkdir()
            project.mkdir()
            workspace_id = str(uuid.uuid4())
            first = ContextWorkspace.create(root_a, "one", workspace_id=workspace_id)
            second = ContextWorkspace.create(root_b, "two", workspace_id=workspace_id)
            first.attach_source(_anchor(str(project)))
            second.attach_source(_anchor(str(project)))
            session, _ = _planned_session(str(project))
            first.attach_session(session, source_ids=["source-1"])
            with self.assertRaises(WorkspaceConflictError):
                second.attach_session(session, source_ids=["source-1"])

    def test_unplanned_session_and_attach_event_id_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            workspace.attach_source(_anchor(root))
            unplanned = ContextSession("inspect", project_root=root)
            with self.assertRaises(WorkspaceValidationError):
                workspace.attach_session(unplanned, source_ids=["source-1"])
            session, _ = _planned_session(root)
            first_id = str(uuid.uuid4())
            second_id = str(uuid.uuid4())
            workspace.attach_session(
                session, source_ids=["source-1"], event_id=first_id
            )
            with self.assertRaises(WorkspaceConflictError):
                workspace.attach_session(
                    session, source_ids=["source-1"], event_id=second_id
                )

    def test_forged_trust_and_caller_lineage_are_rejected(self):
        with self.assertRaises(WorkspaceValidationError):
            SourceTrust("verified", ("forged-ref",))
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            with self.assertRaises(WorkspaceValidationError):
                workspace.commit_context(
                    [
                        ProjectContextEntry(
                            str(uuid.uuid4()),
                            "facts",
                            "fact",
                            receipt_refs=("receipt:forged",),
                        )
                    ]
                )

    def test_valid_looking_fake_verified_trust_is_rejected(self):
        with self.assertRaises(WorkspaceValidationError):
            SourceTrust("verified", ("receipt:sha256:" + "a" * 64,))
        with self.assertRaises(WorkspaceIncompatibleError):
            SourceTrust.from_dict(
                {
                    "level": "verified",
                    "evidence_refs": ["receipt:sha256:" + "a" * 64],
                }
            )
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            forged = dict(_anchor(root).to_dict())
            forged["trust"] = {
                "level": "verified",
                "evidence_refs": ["receipt:sha256:" + "a" * 64],
            }
            with self.assertRaises(WorkspaceValidationError):
                workspace._append(
                    "source_attached",
                    {"anchor": forged},
                    event_id=None,
                )

    def test_attach_source_derives_verified_trust_from_exact_sealed_receipt(self):
        from tests.test_sdk import FakeEngine

        with tempfile.TemporaryDirectory() as root:
            Path(root, "source.txt").write_text("source\n", encoding="utf-8")
            Path(root, "other.txt").write_text("other\n", encoding="utf-8")
            workspace = ContextWorkspace.create(root, "fixture")
            session, source = _planned_session(
                root,
                path="source.txt",
                session_id="session-evidence",
                task_id="task-evidence",
                engine=FakeEngine(),
            )
            session.prepare(source)
            evidence = session.complete()
            self.assertTrue(evidence.sealed)
            self.assertTrue(evidence.verify())

            wrong_anchor = _anchor(root, "source-other", path="other.txt")
            with self.assertRaises(WorkspaceConflictError):
                workspace.attach_source(
                    wrong_anchor,
                    evidence_receipts=(evidence,),
                )

            unsealed = replace(evidence, integrity_status="unsealed")
            with self.assertRaises(WorkspaceValidationError):
                workspace.attach_source(
                    _anchor(root, path="source.txt"),
                    evidence_receipts=(unsealed,),
                )

            receipt = workspace.attach_source(
                _anchor(root, path="source.txt"),
                evidence_receipts=(evidence,),
            )
            self.assertEqual(receipt.source_ids, ("source-1",))
            persisted = ContextWorkspace.open(
                root, workspace.workspace_id
            )._read_state()
            stored = persisted.sources["source-1"]
            self.assertEqual(stored.trust.level, "verified")
            self.assertEqual(
                stored.trust.evidence_refs,
                (evidence.receipt_link.receipt_ref,),
            )
            with self.assertRaises(WorkspaceConflictError):
                workspace.update_source(
                    _anchor(root, path="other.txt"),
                    evidence_receipts=(evidence,),
                )
            workspace.update_source(
                _anchor(root, path="source.txt"),
                evidence_receipts=(evidence,),
            )
            updated = ContextWorkspace.open(root, workspace.workspace_id)._read_state()
            self.assertEqual(updated.sources["source-1"].trust.level, "verified")

    def test_commit_context_requires_exact_attached_session_and_derives_provenance(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            workspace.attach_source(_anchor(root, path="source.txt"))
            session, source = _planned_session(
                root,
                path="source.txt",
                session_id="session-commit",
                task_id="task-commit",
            )
            attached = workspace.attach_session(session, source_ids=["source-1"])
            self.assertIs(attached.session, session)
            session.prepare(source)
            engine_receipt = session.complete()

            entry = ProjectContextEntry(
                "11111111-1111-4111-8111-111111111111",
                "facts",
                "attached-session fact",
            )
            committed = workspace.commit_context([entry], session=session)
            receipt_ref = engine_receipt.receipt_link.receipt_ref
            recovery_ref = engine_receipt.recovery_ref
            self.assertEqual(committed.engine_receipt_refs, (receipt_ref,))
            self.assertEqual(committed.recovery_refs, (recovery_ref,))
            projected = workspace.project_context()
            self.assertEqual(
                projected.entries,
                (
                    ProjectContextEntry(
                        entry.entry_id,
                        entry.category,
                        entry.value,
                        source_ids=("source-1",),
                        session_id="session-commit",
                        receipt_refs=(receipt_ref,),
                        recovery_refs=(recovery_ref,),
                    ),
                ),
            )
            context_event = next(
                event
                for event in workspace._read_state().events
                if event["kind"] == "context_committed"
            )
            provenance = context_event["payload"]["provenance"]
            self.assertEqual(provenance["session_id"], "session-commit")
            self.assertEqual(provenance["task_id"], "task-commit")
            self.assertEqual(provenance["source_ids"], ["source-1"])
            self.assertEqual(provenance["receipt_refs"], [receipt_ref])
            self.assertEqual(provenance["recovery_refs"], [recovery_ref])
            self.assertEqual(
                provenance["receipt_proof"]["receipt_link"],
                dict(engine_receipt.receipt_link.to_dict()),
            )
            self.assertTrue(provenance["receipt_proof_digest"].startswith("sha256:"))

            foreign, foreign_source = _planned_session(
                root,
                path="source.txt",
                session_id="session-commit",
                task_id="task-foreign",
                task="foreign-task",
            )
            foreign.prepare(foreign_source)
            foreign_receipt = foreign.complete()
            self.assertIsNot(foreign_receipt, engine_receipt)
            self.assertNotEqual(foreign_receipt.task_id, engine_receipt.task_id)
            with self.assertRaises(WorkspaceConflictError):
                workspace.commit_context(
                    [
                        ProjectContextEntry(
                            "22222222-2222-4222-8222-222222222222",
                            "facts",
                            "foreign-session fact",
                        )
                    ],
                    session=foreign,
                )

    def test_replay_rejects_rehashed_context_provenance_forgery(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            workspace.attach_source(_anchor(root, path="source.txt"))
            session, source = _planned_session(
                root,
                path="source.txt",
                session_id="session-replay",
                task_id="task-replay",
            )
            workspace.attach_session(session, source_ids=["source-1"])
            session.prepare(source)
            session.complete()
            workspace.commit_context(
                [
                    ProjectContextEntry(
                        "33333333-3333-4333-8333-333333333333",
                        "facts",
                        "replay fact",
                    )
                ],
                session=session,
            )
            forged_ref = "receipt:sha256:" + "f" * 64

            def forge(record):
                record["payload"]["entries"][0]["receipt_refs"] = [forged_ref]
                provenance = record["payload"]["provenance"]
                provenance["receipt_refs"] = [forged_ref]
                provenance["receipt_proof"]["receipt_link"]["receipt_ref"] = forged_ref
                provenance["receipt_proof_digest"] = (
                    "sha256:"
                    + hashlib.sha256(
                        canonical_bytes(provenance["receipt_proof"])
                    ).hexdigest()
                )

            _rewrite_event(workspace, forge, kind="context_committed")
            with self.assertRaises(WorkspaceIncompatibleError):
                ContextWorkspace.open(root, workspace.workspace_id)

    def test_start_session_rejects_symlink_and_uses_default_p4_prepare(self):
        from tests.test_sdk import FakeEngine

        with tempfile.TemporaryDirectory() as root:
            project_root = Path(root) / "project"
            state_root = Path(root) / "state"
            project_root.mkdir()
            Path(project_root, "real.txt").write_text("real\n", encoding="utf-8")
            Path(project_root, "link.txt").symlink_to("real.txt")
            workspace = ContextWorkspace.create(state_root, "fixture")
            link_source = ContextSource("link.txt", project_root=str(project_root))
            real_source = ContextSource("real.txt", project_root=str(project_root))
            workspace.attach_source(
                _anchor(str(project_root), "source-link", path="link.txt")
            )
            workspace.attach_source(
                _anchor(str(project_root), "source-real", path="real.txt")
            )
            link_session, _ = _planned_session(
                str(project_root),
                source_id="source-link",
                path="link.txt",
            )
            with self.assertRaises(WorkspaceValidationError):
                workspace.attach_session(link_session, source_ids=["source-link"])

            engine = FakeEngine()
            with patch(
                "leanctx_sdk.session.SubprocessEngineClient",
                return_value=engine,
            ) as default_engine:
                with self.assertRaises(WorkspaceValidationError):
                    workspace.start_session(
                        "inspect-link",
                        source_id="source-link",
                        source=link_source,
                        session_id="session-link",
                        task_id="task-link",
                    )
                default_engine.assert_not_called()

                attachment = workspace.start_session(
                    "inspect-real",
                    source_id="source-real",
                    source=real_source,
                    session_id="session-real",
                    task_id="task-real",
                )
                default_engine.assert_called_once_with()
            self.assertIs(attachment.session.current_plan.source, real_source)
            self.assertEqual(engine.context_calls, 1)
            self.assertEqual(attachment.session.state, "executing")

    def test_append_publication_stays_on_locked_events_fd_during_path_swap(self):
        from leanctx_sdk import workspace as workspace_module

        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            events = Path(workspace._workspace_path) / "events"
            locked_events = Path(workspace._workspace_path) / "events-locked"
            redirect_events = Path(root) / "redirect-events"
            redirect_events.mkdir(mode=0o700)
            original_write_event = workspace_module._write_event

            def swap_then_publish(events_path, filename, data, *, directory_fd=None):
                self.assertIsNotNone(directory_fd)
                events.rename(locked_events)
                swapped = False
                try:
                    redirect_events.rename(events)
                    swapped = True
                    result = original_write_event(
                        str(events),
                        filename,
                        data,
                        directory_fd=directory_fd,
                    )
                    self.assertFalse((events / filename).exists())
                    self.assertTrue((locked_events / filename).is_file())
                    return result
                finally:
                    if swapped:
                        events.rename(redirect_events)
                    locked_events.rename(events)

            with patch.object(
                workspace_module,
                "_write_event",
                side_effect=swap_then_publish,
            ):
                receipt = workspace.commit_context(
                    [
                        ProjectContextEntry(
                            "44444444-4444-4444-8444-444444444444",
                            "facts",
                            "fd-published fact",
                        )
                    ]
                )
            self.assertEqual(receipt.event_kind, "context_committed")
            self.assertEqual(tuple(redirect_events.iterdir()), ())
            self.assertEqual(len(tuple(events.glob("*.json"))), 2)

    def test_checkpoint_package_seal_and_fresh_seed_preserve_logical_state(self):
        with (
            tempfile.TemporaryDirectory() as source_root,
            tempfile.TemporaryDirectory() as seed_root,
        ):
            source = ContextWorkspace.create(source_root, "source")
            source.pin_package(_package_pin())
            checkpoint = source.checkpoint()
            migration = SnapshotV1MigrationProvenance(
                "snapshot-1",
                "sha256:" + "1" * 64,
                checkpoint.checkpoint_id,
                checkpoint.state_digest,
                ("legacy snapshot had no package lock",),
            )
            inspection = _package_inspection(checkpoint, migration=migration)
            engine = _CheckpointPackageEngine(inspection)
            sealed = seal_checkpoint_package(
                source,
                checkpoint,
                Path(source_root) / "checkpoint.ctxpkg",
                package_name="workspace-checkpoint",
                package_version="1.0.0",
                engine=engine,
                migration_provenance=migration,
            )
            self.assertTrue(sealed.signed)
            self.assertEqual(len(engine.seal_calls), 1)

            seeded = seed_workspace_from_package(
                seed_root,
                "checkpoint.ctxpkg",
                "seeded",
                engine=engine,
                trusted_signer=True,
            )
            self.assertEqual(seeded.workspace_id, checkpoint.workspace_id)
            self.assertEqual(
                seeded.get_checkpoint(checkpoint.checkpoint_id).state_digest,
                checkpoint.state_digest,
            )
            self.assertEqual(seeded.checkpoint().state_digest, checkpoint.state_digest)
            self.assertEqual(len(checkpoint.package_pins), 1)
            self.assertIsNotNone(checkpoint.package_lock_digest)

    def test_trusted_signed_seed_preserves_verified_source_trust(self):
        with (
            tempfile.TemporaryDirectory() as source_root,
            tempfile.TemporaryDirectory() as seed_root,
        ):
            Path(source_root, "source.txt").write_text("source\n", encoding="utf-8")
            source = ContextWorkspace.create(source_root, "source")
            source.attach_source(_anchor(source_root))
            session, context_source = _planned_session(
                source_root,
                session_id="session-seed-trust",
                task_id="task-seed-trust",
            )
            source.attach_session(session, source_ids=["source-1"])
            session.prepare(context_source)
            evidence = session.complete()
            source.update_source(_anchor(source_root), evidence_receipts=(evidence,))
            source.commit_context(
                [
                    ProjectContextEntry(
                        "55555555-5555-4555-8555-555555555555",
                        "facts",
                        "verified portable fact",
                    )
                ],
                session=session,
            )
            checkpoint = source.checkpoint()
            engine = _CheckpointPackageEngine(_package_inspection(checkpoint))

            seeded = seed_workspace_from_package(
                seed_root,
                "checkpoint.ctxpkg",
                "seeded",
                engine=engine,
                trusted_signer=True,
            )

            self.assertEqual(seeded.checkpoint().state_digest, checkpoint.state_digest)
            self.assertEqual(
                seeded._read_state().sources["source-1"].trust.level,
                "verified",
            )

    def test_checkpoint_package_admission_keeps_signature_and_trust_separate(self):
        with (
            tempfile.TemporaryDirectory() as source_root,
            tempfile.TemporaryDirectory() as target,
        ):
            checkpoint = ContextWorkspace.create(source_root, "source").checkpoint()
            signed_engine = _CheckpointPackageEngine(_package_inspection(checkpoint))
            with self.assertRaises(WorkspacePolicyError):
                seed_workspace_from_package(
                    target,
                    "signed.ctxpkg",
                    "seeded",
                    engine=signed_engine,
                )

        with (
            tempfile.TemporaryDirectory() as source_root,
            tempfile.TemporaryDirectory() as target,
        ):
            checkpoint = ContextWorkspace.create(source_root, "source").checkpoint()
            unsigned_engine = _CheckpointPackageEngine(
                _package_inspection(checkpoint, signed=False)
            )
            with self.assertRaises(WorkspacePolicyError):
                seed_workspace_from_package(
                    target,
                    "unsigned.ctxpkg",
                    "seeded",
                    engine=unsigned_engine,
                )
            seeded = seed_workspace_from_package(
                target,
                "unsigned.ctxpkg",
                "seeded",
                engine=unsigned_engine,
                allow_unsigned=True,
            )
            self.assertEqual(seeded.workspace_id, checkpoint.workspace_id)

    def test_checkpoint_package_bridge_rejects_symlinks_and_invalid_crypto_identity(
        self,
    ):
        from leanctx_sdk import checkpoint_package as package_module

        self.assertEqual(
            package_module._non_portable_fields(
                {
                    "canonical_id": "/machine/source",
                    "immutable_ref": "C:\\machine\\recovery",
                }
            ),
            (
                "$.checkpoint.canonical_id",
                "$.checkpoint.immutable_ref",
            ),
        )
        with tempfile.TemporaryDirectory() as root:
            executable = Path(root) / "lean-ctx"
            executable.write_text("engine", encoding="utf-8")
            executable.chmod(0o700)
            executable_link = Path(root) / "lean-ctx-link"
            executable_link.symlink_to(executable)
            with self.assertRaises(WorkspaceIncompatibleError):
                LocalCheckpointPackageEngine(str(executable_link))

            engine = LocalCheckpointPackageEngine(str(executable))
            snapshot = Path(root) / "snapshot.ctxsnapshot.json"
            snapshot.write_text("{}", encoding="utf-8")
            snapshot_link = Path(root) / "snapshot-link.ctxsnapshot.json"
            snapshot_link.symlink_to(snapshot)
            with self.assertRaises(WorkspacePolicyError):
                engine.inspect_snapshot_v1(snapshot_link)

            package = Path(root) / "checkpoint.ctxpkg"
            package.write_text("{}", encoding="utf-8")
            bad_signer_response = {
                "schema_version": "leanctx.ctxpkg-checkpoint-inspect/v1",
                "package": {
                    "schema_version": 2,
                    "kind": "context",
                    "layers": ["checkpoint"],
                    "name": "checkpoint",
                    "version": "1.0.0",
                    "package_digest": "sha256:" + "a" * 64,
                    "content_hash": "sha256:" + "b" * 64,
                    "signature_state": "signed_valid",
                    "signer_public_key": None,
                },
                "checkpoint": {},
            }
            with patch.object(engine, "_run", return_value=bad_signer_response):
                with self.assertRaises(WorkspaceIncompatibleError):
                    engine.inspect(package)

            bad_snapshot_response = {
                "schema_version": "leanctx.snapshot-v1-inspect/v1",
                "snapshot_id": "G" * 64,
                "artifact_digest": "sha256:" + "Z" * 64,
                "signature_state": "signed_valid",
                "signer_public_key": "Q" * 64,
            }
            with patch.object(engine, "_run", return_value=bad_snapshot_response):
                with self.assertRaises(WorkspaceIncompatibleError):
                    engine.inspect_snapshot_v1(snapshot)

    def test_snapshot_v1_migration_is_explicit_and_limited(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "source")
            engine = _CheckpointPackageEngine(None)
            with self.assertRaises(WorkspaceValidationError):
                migrate_snapshot_v1(
                    workspace,
                    "snapshot.json",
                    engine=engine,
                    limitations=(),
                )
            result = migrate_snapshot_v1(
                workspace,
                "snapshot.json",
                engine=engine,
                limitations=("SnapshotV1 has no Workspace policy",),
            )
            self.assertEqual(
                result.classification,
                "MIGRATABLE_WITH_EXPLICIT_LIMITATIONS",
            )
            self.assertEqual(result.provenance.origin, "SnapshotV1")
            self.assertEqual(
                result.provenance.checkpoint_id,
                result.checkpoint.checkpoint_id,
            )

    def test_real_engine_signed_checkpoint_roundtrip_when_enabled(self):
        executable = os.environ.get("LEANCTX_P6_ENGINE_BIN")
        if not executable:
            self.skipTest("set LEANCTX_P6_ENGINE_BIN for the cross-repository P6 proof")
        with (
            tempfile.TemporaryDirectory() as source_root,
            tempfile.TemporaryDirectory() as target_root,
            tempfile.TemporaryDirectory() as engine_data,
        ):
            workspace = ContextWorkspace.create(source_root, "source")
            workspace.pin_package(_package_pin())
            package = Path(source_root) / "checkpoint.ctxpkg"
            engine = LocalCheckpointPackageEngine(
                executable,
                environment={"LEAN_CTX_DATA_DIR": engine_data},
            )
            snapshot_path = os.environ.get("LEANCTX_P6_SNAPSHOT_V1")
            migration = None
            if snapshot_path:
                migrated = migrate_snapshot_v1(
                    workspace,
                    snapshot_path,
                    engine=engine,
                    limitations=("SnapshotV1 has no Workspace policy or package lock",),
                )
                checkpoint = migrated.checkpoint
                migration = migrated.provenance
            else:
                checkpoint = workspace.checkpoint()
            inspection = seal_checkpoint_package(
                workspace,
                checkpoint,
                package,
                package_name="checkpoint-proof",
                package_version="1.0.0",
                engine=engine,
                migration_provenance=migration,
            )
            self.assertTrue(inspection.signed)
            seeded = seed_workspace_from_package(
                target_root,
                package,
                "seeded",
                engine=engine,
                trusted_signer=True,
            )
            self.assertEqual(seeded.checkpoint().state_digest, checkpoint.state_digest)

            tampered = json.loads(package.read_text(encoding="utf-8"))
            tampered["content"]["checkpoint"]["checkpoint"]["checkpoint_id"] = str(
                uuid.uuid4()
            )
            package.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaises(WorkspaceIncompatibleError):
                engine.inspect(package)

    def test_workspace_and_events_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = ContextWorkspace.create(root, "fixture")
            events = Path(workspace._workspace_path) / "events"
            moved = Path(workspace._workspace_path) / "events-real"
            events.rename(moved)
            try:
                os.symlink(moved, events)
                with self.assertRaises(WorkspaceCorruptError):
                    ContextWorkspace.open(root, workspace.workspace_id)
            finally:
                events.unlink()
                moved.rename(events)


if __name__ == "__main__":
    unittest.main()
