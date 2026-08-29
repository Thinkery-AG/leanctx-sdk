import json
import os
import sys
import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from leanctx_sdk import (
    ArtifactIntegrityError,
    CompatibilityError,
    ConfigurationError,
    ContextMeasurement,
    ContextPlan,
    ContextReceipt,
    ContextReceiptLink,
    ContextSession,
    ContextSource,
    ContextView,
    EngineExecutionError,
    EngineProtocolError,
    EngineRejected,
    EngineTimeout,
    EngineUnavailable,
    FrameworkCompatibilityError,
    FrameworkIntegrationError,
    RecoveryUnavailableError,
    UnsupportedEngineError,
    ValidationError,
)
from leanctx_sdk.engine import SubprocessEngineClient, _parse_response
from leanctx_sdk.integrations.openai_agents import OpenAIAgentsAdapter
from leanctx_sdk.protocol import canonical_bytes, sha256_digest
from scripts.validate_wheelhouse import load_manifest, validate_wheelhouse
from scripts.verify_installed import verify_installed
from scripts.verify_wheel_install import (
    _offline_env,
    _validate_direct_url,
    _validate_wheel_source,
    _verify_package_manager_integrity,
)


def _digest(text):
    return sha256_digest(text.encode("utf-8"))


def _fixture_view(source, source_text="fresh synthetic source\n"):
    text = "fresh synthetic view\n"
    output_digest = _digest(text)
    source_digest = _digest(source_text)
    source_ref = "source:synthetic-path-sha256:" + "a" * 64
    input_ref = "input:synthetic-request-sha256:" + "b" * 64
    invocation_id = "engine-invocation-synthetic"
    invocation = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "engine": {"engine_id": "lean-ctx-local", "engine_version": "3.9.20"},
        "operation": {
            "capability_id": "capability://leanctx/context-optimization",
            "capability_version": "1.0.0",
        },
        "input_ref": input_ref,
        "input_digest": "sha256:" + "c" * 64,
        "source_refs": (input_ref, source_ref),
        "policy_admission": {"policy_ref": "policy:synthetic", "decision": "admitted"},
    }
    observation = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "status": "succeeded",
        "output_ref": "output:" + output_digest.removeprefix("sha256:"),
        "output_digest": output_digest,
        "source_lineage": (input_ref, source_ref),
        "measurements": (
            ContextMeasurement("input_tokens", "token", "measured", 1),
            ContextMeasurement("output_tokens", "token", "measured", 2),
        ),
        "failure": None,
        "receipt_link": ContextReceiptLink(
            1,
            "engine-receipt-synthetic",
            "receipt:sha256:" + "d" * 64,
            "sha256:" + "d" * 64,
            invocation_id,
        ),
    }
    return ContextView(
        source=source,
        text=text,
        output_ref="output:" + output_digest.removeprefix("sha256:"),
        output_digest=output_digest,
        source_ref=source_ref,
        source_digest=source_digest,
        recovery_ref=input_ref,
        status="succeeded",
        measurements=observation["measurements"],
        failure=None,
        receipt_link=observation["receipt_link"],
        invocation=invocation,
        observation=observation,
    )


class FakeEngine:
    def __init__(self, error=None):
        self.error = error
        self.context_calls = 0
        self.recover_calls = []
        self.last_plan = None

    def context_view(self, plan):
        self.context_calls += 1
        self.last_plan = plan
        if self.error is not None:
            raise self.error
        return _fixture_view(plan.source)

    def recover(self, project_root, path, recovery_ref, source_ref, source_digest):
        self.recover_calls.append((project_root, path, recovery_ref, source_ref, source_digest))
        raise AssertionError("base fake recovery is not used")


class SDKTests(unittest.TestCase):
    def make_source(self, root):
        return ContextSource("fixture/source.txt", project_root=root)

    def test_plan_is_deterministic_and_engine_mapping_has_no_product_fields(self):
        with tempfile.TemporaryDirectory() as root:
            source = self.make_source(root)
            first = ContextPlan("session-fixed", "task-fixed", "inspect", source)
            second = ContextPlan("session-fixed", "task-fixed", "inspect", source)
            self.assertEqual(first.plan_id, second.plan_id)
            self.assertEqual(
                {
                    "schema_version": 1,
                    "transport_version": 1,
                    "engine_interface_version": "1.0.0",
                    "path": "fixture/source.txt",
                    "mode": "aggressive",
                },
                {
                    "schema_version": 1,
                    "transport_version": 1,
                    "engine_interface_version": "1.0.0",
                    "path": first.source.relative_path,
                    "mode": first.mode,
                },
            )
            self.assertNotIn("task", first.to_intent()["source"])

    def test_lifecycle_idempotency_and_truthful_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeEngine()
            session = ContextSession(
                "inspect",
                project_root=root,
                session_id="session-fixed",
                task_id="task-fixed",
                engine=fake,
            )
            source = self.make_source(root)
            first = session.plan(source)
            self.assertEqual(session.current_plan, first)
            self.assertEqual(session.plan, first)
            self.assertEqual(session.plan.plan_id, first.plan_id)
            view = session.prepare()
            self.assertTrue(view.verify())
            self.assertIs(session.prepare(), view)
            self.assertEqual(fake.context_calls, 1)
            receipt = session.complete({"host": "opaque"})
            self.assertTrue(receipt.sealed)
            self.assertTrue(receipt.verify())
            self.assertEqual(receipt.outcome, "unknown")
            self.assertIs(session.complete({"different": "opaque"}), receipt)
            self.assertEqual(session.state, "completed")
            session.close()
            session.close()
            self.assertEqual(session.state, "closed")

    def test_abort_preserves_host_exception_identity_and_hides_message(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeEngine()
            session = ContextSession("inspect", engine=fake, session_id="s", task_id="t")
            session.prepare(self.make_source(root))
            error = RuntimeError("secret host message")
            receipt = session.abort(error)
            self.assertEqual(receipt.outcome, "aborted")
            self.assertEqual(receipt.host_exception_type, "builtins.RuntimeError")
            self.assertIs(receipt.exception, error)
            self.assertNotIn("secret host message", json.dumps(dict(receipt.to_dict())))
            self.assertIs(session.abort(error), receipt)
            with self.assertRaises(Exception):
                session.complete()

    def test_fail_open_is_limited_to_unavailable_and_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            for failure in (EngineUnavailable("missing"), EngineTimeout("slow")):
                session = ContextSession("inspect", engine=FakeEngine(failure), fail_open=True)
                self.assertIsNone(session.prepare(self.make_source(root)))
                self.assertEqual(session.state, "executing")
                self.assertTrue(session.degradations)
                self.assertTrue(session.complete().integrity_status == "unsealed")
            for failure in (
                EngineProtocolError("bad wire"),
                EngineRejected("unsafe"),
                EngineExecutionError("failed"),
            ):
                session = ContextSession("inspect", engine=FakeEngine(failure), fail_open=True)
                with self.assertRaises(type(failure)):
                    session.prepare(self.make_source(root))
                self.assertEqual(session.state, "aborted")

    def test_exact_recovery_binding(self):
        from leanctx_sdk.protocol import RecoveredSource

        class RecoveringFake(FakeEngine):
            def recover(self, project_root, path, recovery_ref, source_ref, source_digest):
                self.recover_calls.append((project_root, path, recovery_ref, source_ref, source_digest))
                text = "fresh synthetic source\n"
                return RecoveredSource(text, source_ref, source_digest, recovery_ref)

        with tempfile.TemporaryDirectory() as root:
            fake = RecoveringFake()
            session = ContextSession("inspect", engine=fake)
            view = session.prepare(self.make_source(root))
            recovered = session.recover(view)
            self.assertEqual(recovered.text, "fresh synthetic source\n")
            self.assertEqual(fake.recover_calls[0][1], "fixture/source.txt")

    def test_strict_response_rejects_unknown_and_duplicate_fields(self):
        response = {
            "schema_version": 1,
            "transport_version": 1,
            "engine_interface_version": "1.0.0",
            "view": {"text": "x", "output_ref": None, "output_digest": None},
            "invocation": None,
            "observation": None,
            "recovery": {
                "recovery_ref": "input:x",
                "source_ref": "source:x",
                "source_digest": "sha256:" + "a" * 64,
            },
        }
        unknown = dict(response)
        unknown["extra"] = True
        with self.assertRaises(EngineProtocolError):
            _parse_response(canonical_bytes(unknown))
        duplicate = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaises(EngineProtocolError):
            _parse_response(duplicate)

    def test_configuration_compatibility_and_recovery_errors_are_distinct(self):
        with self.assertRaises(ConfigurationError):
            SubprocessEngineClient(timeout=0)

        response = {
            "schema_version": 1,
            "transport_version": 2,
            "engine_interface_version": "1.0.0",
            "view": {"text": "x", "output_ref": None, "output_digest": None},
            "invocation": None,
            "observation": None,
            "recovery": {
                "recovery_ref": "input:x",
                "source_ref": "source:x",
                "source_digest": "sha256:" + "a" * 64,
            },
        }
        with self.assertRaises(CompatibilityError):
            _parse_response(canonical_bytes(response))

        fixture = json.loads(
            (
                Path(__file__).parents[1]
                / "fixtures/engine-interface-v1/r1-success.json"
            ).read_text(encoding="utf-8")
        )["response"]
        fixture["invocation"]["engine"]["engine_version"] = "4.0.0"
        with self.assertRaises(UnsupportedEngineError):
            _parse_response(canonical_bytes(fixture))

        with tempfile.TemporaryDirectory() as root:
            session = ContextSession(
                "inspect",
                engine=FakeEngine(EngineUnavailable("missing")),
                fail_open=True,
            )
            session.prepare(self.make_source(root))
            with self.assertRaises(RecoveryUnavailableError):
                session.recover()

    def test_receipt_integrity_failure_is_actionable(self):
        receipt = ContextReceipt("s", "t", None, None, "failed", "unsealed")
        with self.assertRaises(ArtifactIntegrityError):
            receipt.require_verified()

    def test_adversarial_product_bounds_and_wire_types_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValidationError):
                ContextSource("../escape.txt", project_root=root)
            with self.assertRaises(ValidationError):
                ContextSource("bad\x00path", project_root=root)
            with self.assertRaises(ValidationError):
                ContextPlan("s", "t", "task", self.make_source(root), mode="full")

        response = {
            "schema_version": 1,
            "transport_version": 1,
            "engine_interface_version": "1.0.0",
            "view": {"text": "x", "output_ref": None, "output_digest": None},
            "invocation": None,
            "observation": None,
            "recovery": {
                "recovery_ref": "input:x",
                "source_ref": "source:x",
                "source_digest": "sha256:" + "a" * 64,
            },
        }
        wrong_transport = dict(response)
        wrong_transport["transport_version"] = "1.0.0"
        with self.assertRaises(EngineProtocolError):
            _parse_response(canonical_bytes(wrong_transport))
        nested_unknown = dict(response)
        nested_unknown["view"] = dict(response["view"], unknown=True)
        with self.assertRaises(EngineProtocolError):
            _parse_response(canonical_bytes(nested_unknown))

    def test_fixture_is_independently_authored_and_has_no_placeholder(self):
        fixture_path = Path(__file__).parents[1] / "fixtures/engine-interface-v1/r1-success.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["expected"]["plan_id"], "plan:sha256:25f29db61cbb19986896152ecf2c8b1b60a1187c83a8e4ceefb0b7203542296e")
        self.assertEqual(fixture["contract"]["transport_version"], 1)

    def test_fixture_projects_factual_observation_evidence(self):
        fixture_path = Path(__file__).parents[1] / "fixtures/engine-interface-v1/r1-success.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        view, evidence, recovery = _parse_response(canonical_bytes(fixture["response"]))
        observation = evidence["observation"]
        self.assertEqual(observation["status"], "succeeded")
        self.assertEqual(observation["source_lineage"], evidence["invocation"]["source_refs"])
        self.assertEqual(
            [(item.name, item.classification, item.value) for item in observation["measurements"]],
            [("input_tokens", "measured", 3), ("output_tokens", "measured", 3)],
        )
        self.assertEqual(view["output_digest"], observation["output_digest"])
        self.assertEqual(recovery["source_digest"], fixture["expected"]["source_digest"])

    def test_native_embed_preserves_explicit_outcome(self):
        from leanctx_sdk.integrations.native_embed import complete, prepare

        with tempfile.TemporaryDirectory() as root:
            session = ContextSession("inspect", engine=FakeEngine())
            self.assertIsNotNone(prepare(session, self.make_source(root)))
            sentinel = object()
            receipt = complete(session, sentinel, outcome="accepted")
            self.assertEqual(receipt.outcome, "accepted")
            self.assertIs(receipt.host_result, sentinel)

    def test_reference_application_inspects_receipt_before_recovery(self):
        from examples.reference_application import run
        from leanctx_sdk.protocol import RecoveredSource

        class OrderedFake(FakeEngine):
            completed = False

            def recover(
                self, project_root, path, recovery_ref, source_ref, source_digest
            ):
                if not self.completed:
                    raise AssertionError("reference app recovered before completion")
                return RecoveredSource(
                    "fresh synthetic source\n",
                    source_ref,
                    source_digest,
                    recovery_ref,
                )

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "fixture/source.txt"
            source.parent.mkdir()
            source.write_text("fresh synthetic source\n", encoding="utf-8")
            fake = OrderedFake()
            original_complete = ContextSession.complete

            def complete(session, *args, **kwargs):
                receipt = original_complete(session, *args, **kwargs)
                fake.completed = True
                return receipt

            with patch(
                "examples.reference_application.SubprocessEngineClient",
                return_value=fake,
            ), patch.object(ContextSession, "complete", complete):
                result = run(Path("/unused/engine"), root_path, "fixture/source.txt")
            self.assertTrue(result["receipt_verified"])
            self.assertTrue(result["recovery_exact"])
            self.assertEqual(result["receipt"]["integrity_status"], "sealed")

    def test_context_reactor_fans_out_one_verified_view_and_recovers_exactly(self):
        from examples.context_reactor import SPECIALISTS, run
        from leanctx_sdk.protocol import RecoveredSource

        text = "# Architecture\nInstall with pip install demo.\nVerify and recover safely.\n"

        class ReactorFake(FakeEngine):
            def context_view(self, plan):
                self.context_calls += 1
                return _fixture_view(plan.source, text)

            def recover(
                self, project_root, path, recovery_ref, source_ref, source_digest
            ):
                return RecoveredSource(
                    text,
                    source_ref,
                    source_digest,
                    recovery_ref,
                )

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source.md"
            source.write_text(text, encoding="utf-8")
            fake = ReactorFake()
            with patch(
                "examples.context_reactor.SubprocessEngineClient",
                return_value=fake,
            ):
                result = run(Path("/unused/engine"), root_path, "source.md")

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(fake.context_calls, 1)
        self.assertEqual(result["context"]["sdk_prepare_calls"], 3)
        self.assertEqual(result["context"]["materialized_views"], 1)
        self.assertTrue(result["context"]["same_view_identity"])
        self.assertEqual(result["context"]["parallel_specialists"], len(SPECIALISTS))
        self.assertEqual(result["context"]["reused_prepare_calls"], 2)
        self.assertEqual(
            {item["context_fingerprint"] for item in result["specialists"].values()},
            {result["context"]["fingerprint"]},
        )
        self.assertTrue(result["receipt"]["verified"])
        self.assertTrue(result["recovery_exact"])

    def test_context_reactor_fails_closed_on_inexact_recovery(self):
        from examples.context_reactor import run

        class InexactRecoveryFake(FakeEngine):
            def context_view(self, plan):
                self.context_calls += 1
                return _fixture_view(plan.source, "shaped\n")

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            Path(root, "source.md").write_text("original\n", encoding="utf-8")
            with patch(
                "examples.context_reactor.SubprocessEngineClient",
                return_value=InexactRecoveryFake(),
            ), patch.object(
                ContextSession,
                "recover",
                return_value=SimpleNamespace(text="not the original\n"),
            ):
                with self.assertRaisesRegex(RuntimeError, "exact source recovery failed"):
                    run(Path("/unused/engine"), root_path, "source.md")

    def test_installed_gate_uses_native_embed_adapter_for_real_lifecycle(self):
        from leanctx_sdk.integrations import native_embed

        class RecoveringFake(FakeEngine):
            def context_view(self, plan):
                self.context_calls += 1
                self.last_plan = plan
                return _fixture_view(plan.source, "installed SDK verification\n")

            def recover(self, project_root, path, recovery_ref, source_ref, source_digest):
                from leanctx_sdk.protocol import RecoveredSource

                text = "installed SDK verification\n"
                return RecoveredSource(text, source_ref, source_digest, recovery_ref)

        with tempfile.TemporaryDirectory() as root:
            engine = Path(root, "lean-ctx")
            engine.touch()
            with patch(
                "scripts.verify_installed.SubprocessEngineClient",
                return_value=RecoveringFake(),
            ), patch(
                "scripts.verify_installed.version",
                return_value="1.0.0",
            ), patch(
                "scripts.verify_installed.prepare",
                wraps=native_embed.prepare,
            ) as prepare, patch(
                "scripts.verify_installed.complete",
                wraps=native_embed.complete,
            ) as complete:
                result = verify_installed(engine)
            self.assertEqual(result["status"], "succeeded")
            prepare.assert_called_once()
            complete.assert_called_once()

    def test_offline_agents_manifest_pins_certified_stack(self):
        manifest_path = Path(__file__).parents[1] / "fixtures/openai-agents-0.8.4/wheelhouse-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["stack"], "openai-agents")
        self.assertEqual(manifest["stack_version"], "0.8.4")
        self.assertEqual(
            [(item["distribution"], item["version"]) for item in manifest["requirements"]],
            [
                ("openai-agents", "0.8.4"),
                ("openai", "2.19.0"),
                ("pydantic", "2.12.3"),
                ("requests", "2.33.0"),
                ("urllib3", "2.7.0"),
            ],
        )
        wheelhouse = Path(
            os.environ.get(
                "LEANCTX_SDK_WHEELHOUSE",
                Path(__file__).parents[1].parent
                / "leanctx-product-sdk-artifacts/openai-agents-0.8.4-wheelhouse-py311",
            )
        )
        if not wheelhouse.is_dir():
            self.skipTest("private certified OpenAI Agents wheelhouse is not mounted")
        self.assertEqual(validate_wheelhouse(manifest_path, wheelhouse)["artifacts_checked"], 41)
        self.assertEqual(len(load_manifest(manifest_path)["artifacts"]), 41)

    def test_offline_full_stack_manifest_rejects_reduced_or_changed_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(__file__).parents[1] / "fixtures/openai-agents-0.8.4/wheelhouse-manifest.json"
            manifest = json.loads(source.read_text(encoding="utf-8"))
            manifest["artifacts"] = manifest["artifacts"][:-1]
            reduced = Path(root) / "reduced.json"
            reduced.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_manifest(reduced)
            manifest = json.loads(source.read_text(encoding="utf-8"))
            manifest["requirements"][0]["version"] = "0.8.3"
            changed = Path(root) / "changed.json"
            changed.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_manifest(changed)

    def test_wheel_source_binding_checks_git_bytes(self):
        import zipfile
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as root:
            wheel = Path(root) / "sdk.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("leanctx_sdk/protocol.py", b"source bytes")

            def git(command, **kwargs):
                if command[1] == "ls-tree":
                    return SimpleNamespace(stdout="src/leanctx_sdk/protocol.py\n")
                return SimpleNamespace(stdout=b"source bytes")

            with patch(
                "scripts.verify_wheel_install.inspect_wheel",
                return_value={"status": "PASS"},
            ), patch("scripts.verify_wheel_install.subprocess.run", side_effect=git):
                _validate_wheel_source(wheel, Path(root), "a" * 40)

    def test_wheel_provenance_requires_matching_archive_hash(self):
        digest = "a" * 64
        wheel_url = "file:///private/tmp/sdk.whl"
        with self.assertRaises(SystemExit):
            _validate_direct_url(json.dumps({"archive_info": {}, "url": wheel_url}), digest, wheel_url)
        _validate_direct_url(
            json.dumps({"archive_info": {"hashes": {"sha256": digest}}, "url": wheel_url}),
            digest,
            wheel_url,
        )
        _validate_direct_url(
            json.dumps({"archive_info": {"hash": "sha256=" + digest}, "url": wheel_url}),
            digest,
            wheel_url,
        )
        with self.assertRaises(SystemExit):
            _validate_direct_url(
                json.dumps({"archive_info": {"hashes": {"sha256": "b" * 64}}, "url": wheel_url}),
                digest,
                wheel_url,
            )
        with self.assertRaises(SystemExit):
            _validate_direct_url(json.dumps({"archive_info": {}, "url": "file:///other.whl"}), digest, wheel_url)

    def test_offline_gate_removes_provider_credentials(self):
        with patch.dict(
            os.environ,
            {
                "PYTHONPATH": "/source",
                "OPENAI_API_KEY": "secret",
                "VENDOR_ACCESS_TOKEN": "secret",
                "VENDOR_CREDENTIALS": "secret",
                "SAFE_SETTING": "retained",
            },
            clear=True,
        ):
            environment = _offline_env()
        self.assertEqual(environment["SAFE_SETTING"], "retained")
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("VENDOR_ACCESS_TOKEN", environment)
        self.assertNotIn("VENDOR_CREDENTIALS", environment)

    def test_clean_install_gate_runs_pip_check(self):
        python = Path("/isolated/venv/bin/python")
        repository = Path("/sdk")
        environment = {"PIP_NO_INDEX": "1"}
        with patch("scripts.verify_wheel_install._run") as run:
            _verify_package_manager_integrity(
                python,
                cwd=repository,
                environment=environment,
            )
        run.assert_called_once_with(
            [str(python), "-m", "pip", "check"],
            cwd=repository,
            environment=environment,
        )

    @unittest.skipIf(sys.version_info < (3, 10), "certified Agents path requires Python 3.10+")
    def test_openai_agents_084_adapter_preserves_runner_result(self):
        import types

        calls = []
        sentinel = object()
        fake_agents = types.ModuleType("agents")

        class Runner:
            @staticmethod
            def run_sync(agent, input=None):
                calls.append((agent, input))
                return sentinel

        fake_agents.Runner = Runner
        old = sys.modules.get("agents")
        sys.modules["agents"] = fake_agents
        try:
            with tempfile.TemporaryDirectory() as root:
                session = ContextSession("inspect", engine=FakeEngine())
                session.plan(self.make_source(root))
                adapter = OpenAIAgentsAdapter(object(), session)
                with patch(
                    "leanctx_sdk.integrations.openai_agents.package_version",
                    return_value="0.8.4",
                ):
                    result = adapter.run_sync("caller input")
                self.assertIs(result, sentinel)
                self.assertEqual(session.state, "completed")
                self.assertIs(adapter.receipt.host_result, sentinel)
        finally:
            if old is None:
                sys.modules.pop("agents", None)
            else:
                sys.modules["agents"] = old

    @unittest.skipIf(sys.version_info < (3, 10), "certified Agents path requires Python 3.10+")
    def test_openai_agents_084_adapter_preserves_exact_exception(self):
        import types

        error = RuntimeError("host secret")
        fake_agents = types.ModuleType("agents")

        class Runner:
            @staticmethod
            def run_sync(agent, input=None):
                raise error

        fake_agents.Runner = Runner
        old = sys.modules.get("agents")
        sys.modules["agents"] = fake_agents
        try:
            with tempfile.TemporaryDirectory() as root:
                session = ContextSession("inspect", engine=FakeEngine())
                session.plan(self.make_source(root))
                adapter = OpenAIAgentsAdapter(object(), session)
                with patch(
                    "leanctx_sdk.integrations.openai_agents.package_version",
                    return_value="0.8.4",
                ), self.assertRaises(RuntimeError) as caught:
                    adapter.run_sync("caller input")
                self.assertIs(caught.exception, error)
                self.assertIs(adapter.receipt.exception, error)
                self.assertEqual(adapter.receipt.outcome, "aborted")
                self.assertNotIn("host secret", json.dumps(dict(adapter.receipt.to_dict())))
        finally:
            if old is None:
                sys.modules.pop("agents", None)
            else:
                sys.modules["agents"] = old

    @unittest.skipIf(sys.version_info < (3, 10), "certified Agents path requires Python 3.10+")
    def test_openai_agents_adapter_rejects_uncertified_version(self):
        with tempfile.TemporaryDirectory() as root:
            session = ContextSession("inspect", engine=FakeEngine())
            session.plan(self.make_source(root))
            adapter = OpenAIAgentsAdapter(object(), session)
            with patch(
                "leanctx_sdk.integrations.openai_agents.package_version",
                return_value="0.8.3",
            ), self.assertRaises(FrameworkCompatibilityError):
                adapter.run_sync("caller input")

    @unittest.skipIf(sys.version_info < (3, 10), "certified Agents path requires Python 3.10+")
    def test_openai_agents_adapter_rejects_missing_distribution(self):
        with tempfile.TemporaryDirectory() as root:
            session = ContextSession("inspect", engine=FakeEngine())
            session.plan(self.make_source(root))
            adapter = OpenAIAgentsAdapter(object(), session)
            with patch(
                "leanctx_sdk.integrations.openai_agents.package_version",
                side_effect=PackageNotFoundError,
            ), self.assertRaises(FrameworkIntegrationError):
                adapter.run_sync("caller input")


if __name__ == "__main__":
    unittest.main()
