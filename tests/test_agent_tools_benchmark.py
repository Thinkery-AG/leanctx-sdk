import json
from pathlib import Path
import unittest
from unittest import mock

from benchmarks.agent_tools import retrieval_benchmark
from benchmarks.agent_tools.retrieval_benchmark import evaluate


class AgentToolsBenchmarkTests(unittest.TestCase):
    def row(self, task_id, raw_tokens, lean_tokens, raw_match=True, lean_match=True):
        return {
            "task_id": task_id,
            "expected": "fact",
            "raw": {
                "answer": "fact" if raw_match else None,
                "answer_match": raw_match,
                "context_input_tokens": raw_tokens,
                "tool_calls": 9,
            },
            "leanctx": {
                "answer": "fact" if lean_match else None,
                "answer_match": lean_match,
                "context_input_tokens": lean_tokens,
                "tool_calls": 1,
            },
        }

    def test_accepts_quality_parity_with_material_savings(self):
        report = evaluate((self.row("b", 1000, 100), self.row("a", 1000, 200)))
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["quality_match"])
        self.assertEqual(report["savings_percent"], 85.0)
        self.assertEqual([row["task_id"] for row in report["tasks"]], ["a", "b"])

    def test_rejects_quality_regression_even_with_savings(self):
        report = evaluate((self.row("a", 1000, 10, lean_match=False),))
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["quality_match"])

    def test_rejects_insufficient_savings(self):
        report = evaluate((self.row("a", 1000, 800),))
        self.assertEqual(report["status"], "FAIL")

    def test_report_is_canonical_json_compatible(self):
        report = evaluate((self.row("a", 1000, 100),))
        first = json.dumps(report, sort_keys=True, separators=(",", ":"))
        second = json.dumps(report, sort_keys=True, separators=(",", ":"))
        self.assertEqual(first, second)

    def test_empty_or_invalid_totals_fail_closed(self):
        with self.assertRaises(ValueError):
            evaluate(())
        with self.assertRaises(ValueError):
            evaluate((self.row("a", 0, 0),))

    def test_lane_types_and_ranges_fail_closed(self):
        for lane, field, value in (
            ("raw", "answer_match", "true"),
            ("leanctx", "answer_match", 1),
            ("raw", "context_input_tokens", "1000"),
            ("leanctx", "context_input_tokens", True),
            ("raw", "tool_calls", -1),
            ("leanctx", "answer", True),
        ):
            row = self.row("a", 1000, 100)
            row[lane][field] = value
            with self.subTest(lane=lane, field=field, value=value), self.assertRaises(ValueError):
                evaluate((row,))

    def test_run_requires_three_identical_provider_free_repeats(self):
        single = evaluate((self.row("a", 1000, 100),))
        with mock.patch.object(
            retrieval_benchmark, "_run_once", return_value=single
        ) as run_once:
            report = retrieval_benchmark.run(Path("engine"))
        self.assertEqual(run_once.call_count, 3)
        self.assertEqual(report["median_savings_percent"], 90.0)
        self.assertEqual(report["network_access"], "denied")
        self.assertEqual(report["status"], "PASS")

    def test_run_rejects_nondeterministic_repeats(self):
        first = evaluate((self.row("a", 1000, 100),))
        second = evaluate((self.row("a", 1000, 200),))
        with mock.patch.object(
            retrieval_benchmark,
            "_run_once",
            side_effect=(first, second, first),
        ), self.assertRaises(ValueError):
            retrieval_benchmark.run(Path("engine"))


if __name__ == "__main__":
    unittest.main()
