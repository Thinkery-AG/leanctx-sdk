"""Context Store preview conformance (fixtures shared by all six SDKs) and live Engine."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leanctx_sdk import SubprocessEngineClient  # noqa: E402
from leanctx_sdk.errors import EngineProtocolError, ValidationError  # noqa: E402
from leanctx_sdk.preview import (  # noqa: E402
    parse_policy_evidence,
    parse_task_lineage,
    read_policy_evidence,
    read_task_lineage,
)

FIXTURES = ROOT / "fixtures" / "context-store-preview-v1"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
PARSERS = {"evidence": parse_policy_evidence, "lineage": parse_task_lineage}


def _load(kind, validity, name):
    return json.loads((FIXTURES / kind / validity / f"{name}.json").read_text())


class ContextStorePreviewConformance(unittest.TestCase):
    def test_every_valid_document_parses(self):
        for kind, parse in PARSERS.items():
            for name in MANIFEST["documents"][kind]["valid"]:
                with self.subTest(kind=kind, name=name):
                    parse(_load(kind, "valid", name))

    def test_every_invalid_document_is_rejected(self):
        for kind, parse in PARSERS.items():
            for name in MANIFEST["documents"][kind]["invalid"]:
                with self.subTest(kind=kind, name=name):
                    with self.assertRaises(EngineProtocolError):
                        parse(_load(kind, "invalid", name))

    def test_unmeasured_never_reads_as_measured(self):
        evidence = parse_policy_evidence(_load("evidence", "valid", "rich"))
        unmeasured = evidence.records[1]
        self.assertFalse(unmeasured.quality.measured)
        self.assertIsNone(unmeasured.quality.retained)
        self.assertFalse(unmeasured.security.measured)
        self.assertIsNone(unmeasured.security.regressions)
        measured = evidence.records[0]
        self.assertEqual((measured.security.measured, measured.security.regressions), (True, 0))
        self.assertEqual([e.verdict for e in evidence.evaluations],
                         ["non_inferior", "underpowered"])

    def test_lineage_gaps_name_every_missing_link(self):
        complete = parse_task_lineage(_load("lineage", "valid", "complete"))
        self.assertTrue(complete.is_complete)
        self.assertEqual(complete.deliveries[0].summary.tokens_delivered, 3000)
        gapped = parse_task_lineage(_load("lineage", "valid", "gapped"))
        self.assertFalse(gapped.is_complete)
        self.assertEqual(gapped.deliveries[0].error, "tampered")
        self.assertIsNone(gapped.deliveries[0].summary)

    def test_requests_refuse_blank_identifiers(self):
        with self.assertRaises(ValidationError):
            read_task_lineage(None, "/tmp", " ")


@unittest.skipUnless(os.environ.get("LEANCTX_ENGINE_BINARY"), "needs a real Engine")
class ContextStorePreviewLiveEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SubprocessEngineClient(os.environ["LEANCTX_ENGINE_BINARY"])
        # Private Engine storage refuses symlinked ancestors (macOS /var).
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="leanctx-store-"))

    def test_a_fresh_scope_has_no_evidence(self):
        evidence = read_policy_evidence(self.engine, self.root, project_id="sdk-preview-fresh")
        self.assertEqual(evidence.records, ())
        self.assertEqual(evidence.evaluations, ())

    def test_an_unknown_task_reports_its_gaps_instead_of_completeness(self):
        lineage = read_task_lineage(self.engine, self.root, "sdk-preview-unknown-task",
                                    project_id="sdk-preview-fresh", tenant_id="tenant-a")
        self.assertEqual(lineage.task_id, "sdk-preview-unknown-task")
        self.assertEqual(lineage.outcome, "unknown")
        self.assertIn("no_plan_recorded", lineage.gaps)
        self.assertIn("no_outcome_recorded", lineage.gaps)
        self.assertIn("tenant-a", lineage.scope)


if __name__ == "__main__":
    unittest.main()
