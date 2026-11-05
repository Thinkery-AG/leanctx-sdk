"""Gateway preview conformance (fixtures shared by all six SDKs) and live Engine."""

import json
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leanctx_sdk import SubprocessInstanceClient  # noqa: E402
from leanctx_sdk._deprecation import instance_env as _instance_env  # noqa: E402
from leanctx_sdk.errors import InstanceExecutionError, InstanceProtocolError, ValidationError  # noqa: E402
from leanctx_sdk.protocol import canonical_bytes  # noqa: E402
from leanctx_sdk.preview import (  # noqa: E402
    admit_egress,
    keyed_object_digest,
    receipt_key_id,
    parse_egress_host_report,
    parse_egress_preparation,
    parse_egress_admission,
    prepare_egress,
    report_egress,
)

FIXTURES = ROOT / "fixtures" / "gateway-preview-v1"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


def _load(kind, name):
    return json.loads((FIXTURES / kind / f"{name}.json").read_text())


class GatewayPreviewConformance(unittest.TestCase):
    def test_real_engine_responses_parse_with_their_recorded_shape(self):
        for name, expected in MANIFEST["valid"].items():
            with self.subTest(name=name):
                admission = parse_egress_admission(_load("valid", name))
                receipt = admission.receipt
                self.assertEqual(admission.disposition, expected["disposition"])
                self.assertEqual(admission.classification, expected["classification"])
                self.assertEqual(receipt.outcome, expected["outcome"])
                self.assertEqual(len(receipt.decisions), expected["decisions"])
                self.assertEqual(
                    sum(d.disposition == "deny" for d in receipt.decisions), expected["denied"]
                )
                self.assertEqual(receipt.security["redactions"], expected["redactions"])
                self.assertEqual(len(receipt.signals), expected["signals"])
                self.assertTrue(admission.may_send)
                self.assertFalse(receipt.principal.is_known)

    def test_receipt_v2_objects_are_keyed_like_the_engine_keys_them(self):
        vector = MANIFEST["keyed_v2"]
        key = bytes.fromhex(vector["key_hex"])
        plain = "sha256:" + hashlib.sha256(vector["object_text"].encode()).hexdigest()
        self.assertEqual(receipt_key_id(key), vector["key_id"])
        self.assertEqual(keyed_object_digest(key, plain), vector["keyed_object"])
        receipt = parse_egress_admission(_load("valid", vector["fixture"])).receipt
        self.assertEqual((receipt.schema_version, receipt.object_digest.algorithm,
                          receipt.object_digest.key_id), (2, "hmac-sha256", vector["key_id"]))
        objects = [decision.object for decision in receipt.decisions]
        self.assertEqual(objects, [vector["keyed_object"]])
        self.assertNotIn(plain, objects, "a plain-SHA-256 guess confirms nothing")
        self.assertNotEqual(keyed_object_digest(bytes(32), plain), vector["keyed_object"])
        v1 = parse_egress_admission(_load("valid", "masked")).receipt
        self.assertEqual((v1.schema_version, v1.object_digest), (1, None))

    def test_every_invalid_case_is_rejected(self):
        for name in MANIFEST["invalid"]:
            with self.subTest(name=name):
                with self.assertRaises(InstanceProtocolError):
                    parse_egress_admission(_load("invalid", name))

    def test_prepare_preserves_exact_bytes_and_links_report_digest(self):
        prepared = '{ "model" : "m" }'
        body_digest = "sha256:" + hashlib.sha256(prepared.encode()).hexdigest()
        decision = {
            "schema_version": 1,
            "disposition": "forward",
            "body": {"model": "m"},
            "classification": "internal",
            "receipt": {
                "schema_version": 1, "receipt_id": "egress-test", "mode": "governed",
                "principal": {"kind": "unknown"},
                "destination": {"provider": "openai", "locality": "remote"},
                "sources": {"inspected": 0, "permitted": 0, "selected": 0, "blocked": 0},
                "security": {"redactions": 0, "blocked_objects": 0, "quarantined_objects": 0,
                              "injection_signals": 0, "incomplete_coverage": 0},
                "tokens": {"original": 0, "delivered": 0}, "outcome": "delivered",
                "duration_us": 0, "final_context": body_digest,
            },
        }
        response = {"schema_version": 2, "decision": decision, "prepared_body": prepared,
                    "admission_receipt_digest": "sha256:" + "b" * 64}
        parsed = parse_egress_preparation(response)
        self.assertEqual(parsed.prepared_body, prepared)
        self.assertTrue(parsed.may_send)
        self.assertEqual(parsed.admission_receipt_digest, response["admission_receipt_digest"])
        without_receipt = {
            "schema_version": 2,
            "decision": {"schema_version": 1, "disposition": "forward", "body": {"model": "m"}},
            "prepared_body": prepared,
        }
        self.assertTrue(parse_egress_preparation(without_receipt).may_send)
        rewritten = {**response, "decision": {**decision, "disposition": "rewritten"}}
        self.assertTrue(parse_egress_preparation(rewritten).may_send)
        for disposition in ("forward", "rewritten"):
            for outcome in ("withheld", "failed"):
                receipt = {**decision["receipt"], "outcome": outcome}
                receipt.pop("final_context")
                invalid = {**response, "decision": {
                    **decision, "disposition": disposition, "receipt": receipt,
                }}
                with self.subTest(disposition=disposition, outcome=outcome):
                    with self.assertRaises(InstanceProtocolError):
                        parse_egress_preparation(invalid)
        refused_delivered = {
            "schema_version": 2,
            "decision": {
                "schema_version": 1, "disposition": "refused", "refusal": "withheld",
                "receipt": decision["receipt"],
            },
        }
        with self.assertRaises(InstanceProtocolError):
            parse_egress_preparation(refused_delivered)
        calls = []

        class FakePrepareEngine:
            def _invoke_bytes(self, operation, project_root, request, **kwargs):
                calls.append(operation)
                return json.dumps(response).encode()

        client_result = prepare_egress(
            FakePrepareEngine(), ".", provider="openai", upstream_base="https://api.openai.com",
            body={"model": "m"},
        )
        self.assertEqual(calls, ["egress-prepare"])
        self.assertEqual(client_result.prepared_body, prepared)
        response["prepared_body"] = '{"model":"m"}'
        with self.assertRaises(InstanceProtocolError):
            parse_egress_preparation(response)
        refused = {"schema_version": 2, "decision": {
            "schema_version": 1, "disposition": "refused", "refusal": "withheld"}}
        self.assertFalse(parse_egress_preparation(refused).may_send)

    def test_report_retains_host_observation_and_rejects_engine_claims(self):
        class FakeEngine:
            def __init__(self, observer="host_reported"):
                self.operations = []
                self.observer = observer

            def _invoke_bytes(self, operation, project_root, request, **kwargs):
                self.operations.append(operation)
                observation = {**request, "observer": self.observer}
                response = {"schema_version": 1, "observation_digest": "sha256:" + hashlib.sha256(canonical_bytes(observation)).hexdigest(),
                            "observation": observation}
                return json.dumps(response).encode()

        engine = FakeEngine()
        result = report_egress(
            engine, ".", admission_receipt_digest="sha256:" + "b" * 64,
            attempt=2, transport="http", result="no_response_observed", failure="timeout",
        )
        self.assertEqual(engine.operations, ["egress-report"])
        self.assertEqual(result.observation["result"], "no_response_observed")
        self.assertEqual(result.observation["observer"], "host_reported")
        with self.assertRaises(InstanceProtocolError):
            report_egress(
                FakeEngine("engine_observed"), ".", admission_receipt_digest="sha256:" + "b" * 64,
                attempt=1, transport="http", result="response_observed", http_status=200,
            )
        with self.assertRaises(ValidationError):
            report_egress(
                engine, ".", admission_receipt_digest="sha256:" + "b" * 64,
                attempt=0, transport="http", result="no_response_observed",
            )


@unittest.skipUnless(_instance_env("LEANCTX_INSTANCE_BINARY"), "needs a real LeanCTX instance (LEANCTX_INSTANCE_BINARY)")
class GatewayPreviewLiveEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SubprocessInstanceClient(_instance_env("LEANCTX_INSTANCE_BINARY"))
        self.root = tempfile.mkdtemp(prefix="leanctx-gateway-")

    def admit(self, text, provider="openai", base="https://api.openai.com"):
        return admit_egress(
            self.engine, self.root, provider=provider, upstream_base=base,
            body={"model": "m", "messages": [{"role": "user", "content": text}]},
        )

    def test_a_credential_never_leaves_and_is_counted(self):
        credential = "AKIA" + "Q3EGRZ7LIVEX4KEY"
        admission = self.admit(f"deploy fails with {credential}")
        self.assertEqual(admission.disposition, "rewritten")
        self.assertNotIn(credential, json.dumps(admission.body))
        self.assertEqual(admission.receipt.security["redactions"], 1)
        self.assertTrue(any(s.category == "secret" and s.evidence_count == 1
                            for s in admission.receipt.signals))

    def test_restricted_content_is_withheld_from_a_remote_model(self):
        admission = self.admit("Classification: Secret\nroot cause and customer list",
                               provider="anthropic", base="https://api.anthropic.com")
        self.assertEqual(admission.classification, "restricted")
        self.assertEqual(admission.receipt.outcome, "withheld")
        self.assertEqual(admission.disposition, "refused")
        self.assertFalse(admission.may_send)
        self.assertIsNone(admission.body)
        self.assertIn("destination.remote_restricted",
                      admission.receipt.decisions[0].reason_codes)

    def test_marked_content_is_classified_and_forwarded_unchanged(self):
        admission = self.admit("Classification: Confidential\nboard minutes")
        self.assertEqual(admission.disposition, "forward")
        self.assertEqual(admission.classification, "confidential")
        self.assertEqual(admission.receipt.destination.provider, "openai")
        self.assertEqual(admission.receipt.destination.locality, "remote")

    def test_prepared_timeout_replay_and_conflict_remain_host_reported(self):
        model = "host-selected-model-2026"
        credential = "AKIA" + "Q3EGRZ7LIVEX4KEY"
        preparation = prepare_egress(
            self.engine, self.root, provider="openai", upstream_base="https://api.openai.com",
            body={"model": model, "messages": [{"role": "user", "content": f"deploy fails with {credential}"}]},
        )
        self.assertTrue(preparation.may_send)
        self.assertIsNotNone(preparation.prepared_body)
        self.assertIsNotNone(preparation.admission_receipt_digest)
        prepared_bytes = preparation.prepared_body.encode("utf-8")
        prepared = json.loads(preparation.prepared_body)
        receipt = preparation.decision.receipt
        self.assertEqual(prepared["model"], model)
        self.assertNotIn(credential, preparation.prepared_body)
        self.assertEqual(receipt.security["redactions"], 1)
        self.assertEqual(receipt.destination.provider, "openai")
        self.assertEqual(receipt.destination.model, model)
        self.assertEqual(receipt.final_context, "sha256:" + hashlib.sha256(prepared_bytes).hexdigest())

        report_args = {
            "admission_receipt_digest": preparation.admission_receipt_digest,
            "attempt": 1,
            "transport": "http",
            "result": "no_response_observed",
            "failure": "timeout",
        }
        first = report_egress(self.engine, self.root, **report_args)
        replay = report_egress(self.engine, self.root, **report_args)
        self.assertEqual(first.observation["observer"], "host_reported")
        self.assertEqual(first.observation["result"], "no_response_observed")
        self.assertEqual(first.observation["failure"], "timeout")
        self.assertEqual(first.observation_digest, replay.observation_digest)

        with self.assertRaisesRegex(InstanceExecutionError, "egress_observation_unavailable"):
            report_egress(
                self.engine, self.root,
                admission_receipt_digest=preparation.admission_receipt_digest,
                attempt=1, transport="http", result="response_observed", http_status=503,
            )


@unittest.skipUnless(_instance_env("LEANCTX_INSTANCE_BINARY"), "needs a real LeanCTX instance (LEANCTX_INSTANCE_BINARY)")
class GatewayReferenceApps(unittest.TestCase):
    def run_example(self, name):
        import subprocess

        result = subprocess.run(
            [sys.executable, str(ROOT / "examples" / name),
             "--engine", _instance_env("LEANCTX_INSTANCE_BINARY")],
            capture_output=True, text=True, timeout=180, check=True,
        )
        return [json.loads(line) for line in result.stdout.splitlines() if line]

    def test_minimal_app_sends_only_admitted_bodies(self):
        clean, credential, restricted = self.run_example("gateway_minimal_app.py")
        self.assertEqual((clean["disposition"], clean["redactions"]), ("forward", 0))
        self.assertEqual(credential["redactions"], 1)
        self.assertNotIn("AKIA", credential["provider_saw"])
        self.assertEqual((restricted["classification"], restricted["outcome"]),
                         ("restricted", "withheld"))
        self.assertEqual(restricted["disposition"], "refused")
        self.assertFalse(restricted["sent"])
        self.assertIsNone(restricted["provider_saw"])

    def test_login_journey_hud_is_measured_and_nothing_forbidden_leaves(self):
        (journey,) = self.run_example("gateway_login_journey.py")
        self.assertEqual(journey["sources_total"], 21)
        self.assertEqual(journey["sources_blocked"], [".env"])
        self.assertEqual(journey["credentials_left"], [])
        self.assertEqual(journey["redactions"], 2)
        relevant = {"src/auth/login.py", "src/auth/session.py", "src/auth/middleware.py",
                    "issue/LOGIN-482", "logs/deploy-14-05.log"}
        self.assertEqual(set(journey["relevant_selected"]), relevant)
        # Scenario 24: unrelated documents stay out even with budget left.
        self.assertEqual(journey["irrelevant_selected"], [])
        self.assertLess(journey["receipt_tokens"]["delivered"], journey["considered_tokens"])
        # The HUD line is derived from the measured plan and receipt.
        used = len(journey["sources_selected"])
        self.assertIn(f"{journey['redactions']} credentials redacted", journey["hud"])
        self.assertIn(f"1 source blocked · {used}/21 sources used", journey["hud"])

    def test_agent_loop_never_shows_the_model_a_secret(self):
        (summary,) = self.run_example("gateway_agent_loop.py")
        self.assertFalse(summary["credential_reached_model"])
        self.assertFalse(summary["restricted_reached_model"])
        self.assertEqual(summary["receipts"], len(summary["turns"]))
        self.assertGreaterEqual(summary["model_calls"], 3)
        self.assertTrue(any(turn["withheld_objects"] for turn in summary["turns"]))


if __name__ == "__main__":
    unittest.main()
