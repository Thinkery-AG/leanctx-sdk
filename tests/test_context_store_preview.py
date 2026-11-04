"""Context Store preview conformance (fixtures shared by all six SDKs) and live Engine."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leanctx_sdk import SubprocessInstanceClient  # noqa: E402
from leanctx_sdk._deprecation import instance_env as _instance_env  # noqa: E402
from leanctx_sdk.errors import InstanceProtocolError, ValidationError  # noqa: E402
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
                    with self.assertRaises(InstanceProtocolError):
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

    def test_a_version_is_the_integer_not_a_value_equal_to_it(self):
        # `True == 1` and `1.0 == 1` in Python; neither is version 1. `False`
        # and `2.0` are refused as well: no boolean and no float is a version.
        class Engine:
            def __init__(self, document):
                self.document = document

            def _invoke_bytes(self, *_args, **_kwargs):
                return json.dumps(self.document).encode("utf-8")

        documents = {
            "evidence": (parse_policy_evidence, _load("evidence", "valid", "rich"),
                         lambda engine: read_policy_evidence(engine, "/tmp", project_id="p")),
            "lineage": (parse_task_lineage, _load("lineage", "valid", "complete"),
                        lambda engine: read_task_lineage(engine, "/tmp", "task", project_id="p")),
        }
        for kind, (parse, valid, read) in documents.items():
            envelope = {"schema_version": 1, "transport_version": 1,
                        "engine_interface_version": "1.0.0", kind: valid}
            # The integer itself still passes, in the document and in the envelope.
            self.assertEqual(valid["schema_version"], 1)
            parse(valid)
            read(Engine(envelope))
            for wrong in (True, 1.0, False, 2.0):
                with self.subTest(kind=kind, where="document", value=wrong):
                    with self.assertRaises(InstanceProtocolError):
                        parse({**valid, "schema_version": wrong})
                for field in ("schema_version", "transport_version"):
                    with self.subTest(kind=kind, where=field, value=wrong):
                        with self.assertRaises(InstanceProtocolError):
                            read(Engine({**envelope, field: wrong}))


@unittest.skipUnless(_instance_env("LEANCTX_INSTANCE_BINARY"), "needs a real LeanCTX instance (LEANCTX_INSTANCE_BINARY)")
class ContextStorePreviewLiveEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SubprocessInstanceClient(_instance_env("LEANCTX_INSTANCE_BINARY"))
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
