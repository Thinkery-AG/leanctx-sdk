# SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
"""Focused real-process checks; requires an explicitly digest-pinned Engine artifact."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from leanctx_sdk.engine import SubprocessEngineClient
from leanctx_sdk.errors import EngineExecutionError, EngineTimeout, ValidationError
from leanctx_sdk.planning import EnginePlanningRequest, EngineSource
from leanctx_sdk.protocol import ContextPlan, ContextSource, sha256_digest


class PlanningTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(
            prefix="leanctx-sdk-plan-", dir="/private/tmp"
        )
        cls.root = Path(cls.fixture.name)
        cls.binary = cls.root / "lean-ctx"
        source = Path(os.environ["LEANCTX_TEST_ENGINE_BINARY"])
        shutil.copy2(source, cls.binary)
        actual = hashlib.sha256(cls.binary.read_bytes()).hexdigest()
        if actual != os.environ["LEANCTX_TEST_ENGINE_SHA256"]:
            raise RuntimeError("test Engine artifact digest mismatch")
        print("executed Engine SHA256:", actual, flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.project = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.project.cleanup)
        self.client = SubprocessEngineClient(self.binary, timeout=15)
        self.request = EnginePlanningRequest("sdk-plan", "invoice ledger", 512)

    def source(self, identity, kind, permission="permitted"):
        content = "invoice ledger evidence from " + identity
        return EngineSource(
            {
                "object_ref": identity,
                "source_id": "source-" + identity,
                "source_type": kind,
                "content_digest": sha256_digest(content.encode()),
                "revision": "revision-one",
                "owner": "fixture-owner",
                "observed_at": None,
                "valid_until": None,
                "classification": "Internal",
                "permission": permission,
            },
            content,
        )

    def test_real_local_plan_is_bound_to_request(self):
        result = self.client.context_plan(self.project.name, self.request)
        self.assertEqual(result["plan"]["task_id"], "sdk-plan")
        self.assertLessEqual(result["plan"]["budget_tokens"], 512)
        self.assertNotIn("receipt", result)
        self.assertEqual(list(Path(self.project.name).glob(".leanctx-sdk-*.json")), [])

    def test_real_explicit_source_plan_and_policy_exclusion(self):
        sources = [
            self.source("file", "filesystem"),
            self.source("issue", "issue_tracker"),
            self.source("database", "relational_database"),
            self.source("denied", "other", "denied"),
        ]
        result = self.client.context_plan_sources(
            self.project.name, self.request, sources
        )
        bindings = result["source_bindings"]
        self.assertEqual(
            {entry["object_ref"] for entry in bindings}, {"file", "issue", "database"}
        )
        for binding in bindings:
            expected = next(
                source.to_dict()["descriptor"]
                for source in sources
                if source.to_dict()["descriptor"]["object_ref"] == binding["object_ref"]
            )
            self.assertEqual(binding, expected)
        self.assertEqual(list(Path(self.project.name).glob(".leanctx-sdk-*.json")), [])

    def test_duplicate_sources_fail_before_process(self):
        source = self.source("duplicate", "filesystem")
        self.client.engine_binary = str(self.root / "absent-engine")
        with self.assertRaises(ValidationError):
            self.client.context_plan_sources(
                self.project.name, self.request, [source, source]
            )

    def test_legacy_context_view_and_exact_recovery_remain_compatible(self):
        content = "invoice ledger compatibility evidence\n"
        (Path(self.project.name) / "source.txt").write_text(content)
        source = ContextSource("source.txt", project_root=self.project.name)
        view = self.client.context_view(
            ContextPlan("sdk-session", "sdk-legacy", "inspect", source)
        )
        self.assertTrue(view.verify())
        recovered = self.client.recover(
            self.project.name,
            "source.txt",
            view.recovery_ref,
            view.source_ref,
            view.source_digest,
        )
        self.assertEqual(recovered.text, content)

    def executable(self, content):
        script = Path(self.project.name) / "fixture-engine"
        script.write_text("#!" + sys.executable + "\n" + content)
        script.chmod(0o700)
        return SubprocessEngineClient(script, timeout=0.2)

    def test_deadline_includes_child_wait_after_pipe_eof(self):
        client = self.executable(
            "import os,time\nos.close(1)\nos.close(2)\ntime.sleep(10)\n"
        )
        start = time.monotonic()
        with self.assertRaises(EngineTimeout):
            client._run("context-plan", self.project.name, "-")
        self.assertLess(time.monotonic() - start, 2)

    def test_stdin_transfer_is_multiplexed_and_bounded(self):
        client = self.executable(
            "import sys\ndata=sys.stdin.buffer.read()\nprint(len(data))\n"
        )
        client.timeout = 2
        result = client._run(
            "context-plan-sources", self.project.name, "-", input_bytes=b"x" * 80000
        )
        self.assertEqual(result.strip(), b"80000")

    def test_stalled_stdin_consumer_cannot_bypass_deadline(self):
        client = self.executable(
            "import sys,time\nsys.stdin.buffer.read(1)\ntime.sleep(10)\n"
        )
        start = time.monotonic()
        with self.assertRaises(EngineTimeout):
            client._run(
                "context-plan-sources",
                self.project.name,
                "-",
                input_bytes=b"x" * 1048576,
            )
        self.assertLess(time.monotonic() - start, 2)

    def test_selector_allocation_failure_reaps_the_started_child(self):
        client = self.executable("import time\ntime.sleep(10)\n")
        started = []
        spawn = subprocess.Popen

        def capture(*args, **kwargs):
            process = spawn(*args, **kwargs)
            started.append(process)
            return process

        with mock.patch("leanctx_sdk.engine.subprocess.Popen", side_effect=capture):
            with mock.patch(
                "leanctx_sdk.engine.selectors.DefaultSelector",
                side_effect=OSError("fixture allocation failure"),
            ):
                with self.assertRaises(EngineExecutionError):
                    client._run("context-plan", self.project.name, "-")
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].poll())
        self.assertTrue(started[0].stdout.closed)
        self.assertTrue(started[0].stderr.closed)


if __name__ == "__main__":
    unittest.main()
