#!/usr/bin/env python3
"""Deterministic raw-vs-LeanCTX context retrieval benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Iterable

from leanctx_sdk import AgentContext


MINIMUM_SAVINGS_PERCENT = 30.0


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
            context_tokens += result.output_tokens
            combined.append(result.text)
    return {
        "answer_match": task.expected in "\n".join(combined),
        "context_input_tokens": context_tokens,
        "tool_calls": len(combined),
    }


def _lean_lane(root: Path, engine: Path, task: Task) -> dict[str, object]:
    with AgentContext(root, task=task.query, engine_binary=engine) as context:
        result = context.search(task.query, path=".", max_results=20)
    return {
        "answer_match": task.expected in result.text,
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
        lanes.append((raw, leanctx))
    raw_tokens = sum(raw["context_input_tokens"] for raw, _ in lanes)
    lean_tokens = sum(leanctx["context_input_tokens"] for _, leanctx in lanes)
    if raw_tokens <= 0 or lean_tokens < 0:
        raise ValueError("invalid benchmark token totals")
    quality_match = all(
        raw["answer_match"] and leanctx["answer_match"]
        for raw, leanctx in lanes
    )
    savings = 100.0 * (raw_tokens - lean_tokens) / raw_tokens
    passed = quality_match and savings >= MINIMUM_SAVINGS_PERCENT
    return {
        "context_input_tokens": {"leanctx": lean_tokens, "raw": raw_tokens},
        "minimum_savings_percent": MINIMUM_SAVINGS_PERCENT,
        "quality_match": quality_match,
        "savings_percent": round(savings, 6),
        "status": "PASS" if passed else "FAIL",
        "tasks": ordered,
    }


def run(engine: Path) -> dict[str, object]:
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
        report = evaluate(rows)
        report.update(
            {
                "benchmark": "leanctx.agent-tools-retrieval/v1",
                "engine_version": "3.10.1",
                "scope": "context retrieval only; no provider billing claim",
                "sdk_version": "1.1.0",
            }
        )
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.engine.resolve(strict=True))
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output is not None:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
