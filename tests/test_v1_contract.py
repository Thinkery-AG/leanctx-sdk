import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import leanctx_sdk as sdk
import leanctx_sdk.preview as preview
from leanctx_sdk import (
    ArtifactIntegrityError,
    CompatibilityError,
    ConfigurationError,
    ContextPlan,
    ContextReceipt,
    ContextSession,
    ContextSource,
    EngineTimeout,
    EngineUnavailable,
    FrameworkIntegrationError,
    PolicyAdmissionError,
    RecoveryUnavailableError,
    SourceUnavailableError,
    UnsupportedEngineError,
)
from tests.test_sdk import FakeEngine, _fixture_view


class V1ContractTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / "fixtures/sdk-v1/contract.json"
        self.contract = json.loads(path.read_text(encoding="utf-8"))

    def test_exact_product_primitives_exist_and_post_v1_is_absent(self):
        self.assertEqual(sdk.__version__, "1.1.0")
        self.assertEqual(
            self.contract["primitives"],
            [
                "ContextSession",
                "ContextSource",
                "ContextView",
                "ContextPlan",
                "ContextReceipt",
            ],
        )
        for name in self.contract["primitives"]:
            self.assertTrue(hasattr(sdk, name), name)
        for name in self.contract["post_v1_excluded"]:
            self.assertFalse(hasattr(sdk, name), name)

    def test_preview_namespace_is_explicit_and_aliases_are_frozen(self):
        self.assertIn("may change", (preview.__doc__ or "").lower())
        self.assertIs(preview.ContextCheckpoint, preview.ContextCheckpointV2)
        self.assertIs(preview.ContextDelta, preview.ContextDeltaV1)
        self.assertIs(preview.ContextHandoff, preview.ContextHandoffV1)
        for name in (
            "ContextWorkspace",
            "ContextCheckpoint",
            "ContextDelta",
            "ContextHandoff",
            "WorkspaceForkV1",
        ):
            self.assertIn(name, preview.__all__)

    def test_public_surface_manifest_names_only_five_stable_primitives(self):
        repository = Path(__file__).parents[1]
        manifest = (repository / "PUBLIC-SURFACE-MANIFEST.md").read_text(
            encoding="utf-8"
        )
        for name in self.contract["primitives"]:
            self.assertIn(f"`{name}`", manifest)
        self.assertIn("leanctx_sdk.preview", manifest)
        self.assertIn("may change", manifest.lower())

    def test_lifecycle_and_plan_identity_are_deterministic(self):
        self.assertEqual(
            self.contract["lifecycle"], ["Select", "Shape", "Reuse", "Recover"]
        )
        with tempfile.TemporaryDirectory() as root:
            source = ContextSource("source.txt", project_root=root)
            one = ContextPlan("session", "task", "shape", source)
            two = ContextPlan("session", "task", "shape", source)
            self.assertEqual(one.plan_id, two.plan_id)

    def test_error_guidance_is_stable_and_omits_message(self):
        error = EngineTimeout("sensitive host detail")
        self.assertEqual(
            error.as_dict(),
            {
                "abort_required": False,
                "code": "engine_timeout",
                "configuration_fix": False,
                "degrade_allowed": True,
                "guidance": "retry within host policy or use explicit bounded fail-open",
                "retryable": True,
                "version_change": False,
            },
        )
        self.assertNotIn("sensitive", json.dumps(error.as_dict()))

    def test_error_taxonomy_covers_every_release_family(self):
        families = {
            "configuration": ConfigurationError,
            "unsupported_engine": UnsupportedEngineError,
            "engine_unavailable": EngineUnavailable,
            "policy_admission": PolicyAdmissionError,
            "source_unavailable": SourceUnavailableError,
            "recovery_unavailable": RecoveryUnavailableError,
            "compatibility": CompatibilityError,
            "invalid_lifecycle": sdk.SessionStateError,
            "framework_integration": FrameworkIntegrationError,
            "artifact_integrity": ArtifactIntegrityError,
        }
        self.assertEqual(set(families), set(self.contract["error_families"]))
        for name, error_type in families.items():
            with self.subTest(name=name):
                guidance = error_type().as_dict()
                self.assertEqual(
                    set(guidance),
                    {
                        "abort_required",
                        "code",
                        "configuration_fix",
                        "degrade_allowed",
                        "guidance",
                        "retryable",
                        "version_change",
                    },
                )
                self.assertTrue(guidance["guidance"])

    def test_primitive_serialization_fingerprints_are_frozen(self):
        fixture = json.loads(
            (
                Path(__file__).parents[1] / "fixtures/sdk-v1/serialization-sha256.json"
            ).read_text(encoding="utf-8")
        )
        source = ContextSource("fixture/source.txt", project_root="/PROJECT")
        plan = ContextPlan("session-fixed", "task-fixed", "inspect", source)
        view = _fixture_view(source)
        receipt = ContextReceipt(
            "session-fixed",
            "task-fixed",
            plan.plan_id,
            view,
            "completed",
            "sealed",
            usage={"requests": 1},
        )
        session = ContextSession(
            "inspect",
            project_root="/PROJECT",
            session_id="session-fixed",
            task_id="task-fixed",
            engine=FakeEngine(),
        )
        values = {
            "ContextSource": dict(source.to_dict()),
            "ContextPlan": dict(plan.to_dict()),
            "ContextView": dict(view.to_dict()),
            "ContextReceipt": dict(receipt.to_dict()),
            "ContextSession": {
                "session_id": session.session_id,
                "task_id": session.task_id,
                "task": session.task,
                "state": session.state,
            },
        }
        actual = {
            name: hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            for name, value in values.items()
        }
        self.assertEqual(actual, fixture)

    def test_public_projections_are_detached_json_serializable_dicts(self):
        source = ContextSource("fixture/source.txt", project_root="/PROJECT")
        plan = ContextPlan("session-fixed", "task-fixed", "inspect", source)
        view = _fixture_view(source)
        receipt = ContextReceipt(
            "session-fixed",
            "task-fixed",
            plan.plan_id,
            view,
            "completed",
            "sealed",
            usage={"requests": 1},
        )
        projections = (
            source.descriptor(),
            source.to_dict(),
            plan.to_intent(),
            plan.to_dict(),
            view.recovery_binding(),
            view.to_dict(),
            receipt.to_dict(),
        )
        for projection in projections:
            with self.subTest(keys=sorted(projection)):
                self.assertIs(type(projection), dict)
                json.dumps(projection, sort_keys=True)

        source_projection = source.to_dict()
        source_projection["path"] = "changed.txt"
        self.assertEqual(source.to_dict()["path"], "fixture/source.txt")

        intent_projection = plan.to_intent()
        intent_projection["source"]["path"] = "changed.txt"
        self.assertEqual(plan.to_intent()["source"]["path"], "fixture/source.txt")


if __name__ == "__main__":
    unittest.main()
