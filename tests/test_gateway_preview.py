"""Gateway preview conformance (fixtures shared by all six SDKs) and live Engine."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leanctx_sdk import SubprocessEngineClient  # noqa: E402
from leanctx_sdk.errors import EngineProtocolError  # noqa: E402
from leanctx_sdk.preview import (  # noqa: E402
    admit_egress,
    parse_egress_admission,
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

    def test_every_invalid_case_is_rejected(self):
        for name in MANIFEST["invalid"]:
            with self.subTest(name=name):
                with self.assertRaises(EngineProtocolError):
                    parse_egress_admission(_load("invalid", name))


@unittest.skipUnless(os.environ.get("LEANCTX_ENGINE_BINARY"), "needs a real Engine")
class GatewayPreviewLiveEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SubprocessEngineClient(os.environ["LEANCTX_ENGINE_BINARY"])
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
        self.assertNotIn("customer list", json.dumps(admission.body))
        self.assertIn("destination.remote_restricted",
                      admission.receipt.decisions[0].reason_codes)

    def test_marked_content_is_classified_and_forwarded_unchanged(self):
        admission = self.admit("Classification: Confidential\nboard minutes")
        self.assertEqual(admission.disposition, "forward")
        self.assertEqual(admission.classification, "confidential")
        self.assertEqual(admission.receipt.destination.provider, "openai")
        self.assertEqual(admission.receipt.destination.locality, "remote")


@unittest.skipUnless(os.environ.get("LEANCTX_ENGINE_BINARY"), "needs a real Engine")
class GatewayReferenceApps(unittest.TestCase):
    def run_example(self, name):
        import subprocess

        result = subprocess.run(
            [sys.executable, str(ROOT / "examples" / name),
             "--engine", os.environ["LEANCTX_ENGINE_BINARY"]],
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
        self.assertNotIn("customer list", restricted["provider_saw"])

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
