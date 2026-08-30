#!/usr/bin/env python3
"""Deterministic raw-vs-LeanCTX context retrieval benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import socket
from statistics import median
import tempfile
from typing import Iterable, cast
from unittest.mock import patch

from leanctx_sdk import AgentContext


MINIMUM_SAVINGS_PERCENT = 30.0
DEFAULT_REPEATS = 3


@dataclass(frozen=True)
class Task:
    task_id: str
    query: str
    expected: str
    source: str


TASKS = (
    Task("retention", "RETENTION_DAYS", "RETENTION_DAYS = 37", "retention.txt"),
    Task(
        "cache",
        "CACHE_STRATEGY",
        'CACHE_STRATEGY = "content-addressed"',
        "cache.txt",
    ),
    Task(
        "entrypoint",
        "AGENT_ENTRYPOINT",
        'AGENT_ENTRYPOINT = "AgentContext"',
        "entrypoint.txt",
    ),
)


def create_fixture(root: Path) -> None:
    noise = "\n".join(
        f"background_{index:04d} = repeated context that is unrelated to the task"
        for index in range(400)
    )
    facts = {task.source: task.expected for task in TASKS}
    for index in range(6):
        name = f"noise-{index}.txt"
        (root / name).write_text(f"fixture={name}\n{noise}\n", encoding="utf-8")
    for name, fact in facts.items():
        (root / name).write_text(f"{fact}\n{noise}\n", encoding="utf-8")


def _raw_lane(root: Path, engine: Path, task: Task) -> dict[str, object]:
    context_tokens = 0
    combined = []
    with AgentContext(root, task=task.query, engine_binary=engine) as context:
        for path in sorted(root.glob("*.txt")):
            result = context.read(path.name, "full", fresh=True)
            context_tokens += result.original_tokens
            combined.append(path.read_text(encoding="utf-8"))
    answer = task.expected if task.expected in "\n".join(combined) else None
    return {
        "answer": answer,
        "answer_match": answer == task.expected,
        "context_input_tokens": context_tokens,
        "tool_calls": len(combined),
    }


def _lean_lane(root: Path, engine: Path, task: Task) -> dict[str, object]:
    with AgentContext(root, task=task.query, engine_binary=engine) as context:
        result = context.search(task.query, path=".", max_results=20)
    answer = task.expected if task.expected in result.text else None
    return {
        "answer": answer,
        "answer_match": answer == task.expected,
        "context_input_tokens": result.output_tokens,
        "tool_calls": 1,
    }


def evaluate(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    ordered = sorted(rows, key=lambda row: str(row["task_id"]))
    if not ordered:
        raise ValueError("benchmark requires tasks")
    lanes = []
    for row in ordered:
        raw = row.get("raw")
        leanctx = row.get("leanctx")
        if not isinstance(raw, dict) or not isinstance(leanctx, dict):
            raise ValueError("benchmark row is missing a lane")
        for label, lane in (("raw", raw), ("leanctx", leanctx)):
            if type(lane.get("answer_match")) is not bool:
                raise ValueError(f"{label} answer_match must be boolean")
            for field in ("context_input_tokens", "tool_calls"):
                value = lane.get(field)
                if type(value) is not int or value < 0:
                    raise ValueError(f"{label} {field} must be a non-negative integer")
            if lane.get("answer") is not None and not isinstance(lane.get("answer"), str):
                raise ValueError(f"{label} answer must be a string or null")
        lanes.append((raw, leanctx))
    raw_tokens = sum(raw["context_input_tokens"] for raw, _ in lanes)
    lean_tokens = sum(leanctx["context_input_tokens"] for _, leanctx in lanes)
    if raw_tokens <= 0 or lean_tokens < 0:
        raise ValueError("invalid benchmark token totals")
    quality_match = all(
        raw["answer"] == row["expected"]
        and leanctx["answer"] == row["expected"]
        and raw["answer"] == leanctx["answer"]
        and raw["answer_match"] is True
        and leanctx["answer_match"] is True
        for row, (raw, leanctx) in zip(ordered, lanes)
    )
    savings = 100.0 * (raw_tokens - lean_tokens) / raw_tokens
    task_savings = [
        100.0 * (raw["context_input_tokens"] - leanctx["context_input_tokens"])
        / raw["context_input_tokens"]
        for raw, leanctx in lanes
        if raw["context_input_tokens"] > 0
    ]
    minimum_task_savings = min(task_savings, default=float("-inf"))
    passed = (
        quality_match
        and savings >= MINIMUM_SAVINGS_PERCENT
        and minimum_task_savings >= MINIMUM_SAVINGS_PERCENT
        and len(task_savings) == len(lanes)
    )
    return {
        "context_input_tokens": {"leanctx": lean_tokens, "raw": raw_tokens},
        "minimum_savings_percent": MINIMUM_SAVINGS_PERCENT,
        "minimum_task_savings_percent": round(minimum_task_savings, 6),
        "quality_match": quality_match,
        "savings_percent": round(savings, 6),
        "status": "PASS" if passed else "FAIL",
        "tasks": ordered,
    }


def _run_once(engine: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="leanctx-agent-benchmark-") as directory:
        root = Path(directory)
        create_fixture(root)
        rows: list[dict[str, object]] = []
        for task in TASKS:
            rows.append(
                {
                    "expected": task.expected,
                    "leanctx": _lean_lane(root, engine, task),
                    "raw": _raw_lane(root, engine, task),
                    "task_id": task.task_id,
                }
            )
        return evaluate(rows)


def run(engine: Path, *, repeats: int = DEFAULT_REPEATS) -> dict[str, object]:
    if type(repeats) is not int or not 2 <= repeats <= 10:
        raise ValueError("repeats must be between 2 and 10")
    def deny_network(*_args, **_kwargs):
        raise RuntimeError("network access is forbidden during the benchmark")

    with patch.object(socket.socket, "connect", deny_network):
        reports = [_run_once(engine) for _ in range(repeats)]
    canonical = [json.dumps(report, sort_keys=True, separators=(",", ":")) for report in reports]
    if len(set(canonical)) != 1:
        raise ValueError("benchmark results are not deterministic")
    report = reports[0]
    median_savings = round(
        median(cast(float, item["savings_percent"]) for item in reports), 6
    )
    report.update(
        {
            "benchmark": "leanctx.agent-tools-retrieval/v1",
            "engine_version": "3.10.1",
            "median_savings_percent": median_savings,
            "network_access": "denied",
            "repeats": repeats,
            "scope": "controlled context retrieval only; no provider billing claim",
            "sdk_version": "1.1.0",
            "status": "PASS"
            if all(item["status"] == "PASS" for item in reports)
            and median_savings >= MINIMUM_SAVINGS_PERCENT
            else "FAIL",
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    args = parser.parse_args()
    try:
        report = run(args.engine.resolve(strict=True), repeats=args.repeats)
    except Exception as error:
        report = {
            "benchmark": "leanctx.agent-tools-retrieval/v1",
            "error": type(error).__name__,
            "status": "FAIL",
        }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output is not None:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
