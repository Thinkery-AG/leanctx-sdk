#!/usr/bin/env python3
"""Exercise the installed SDK against one real LeanCTX | OSS binary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import Optional

from leanctx_sdk import AgentContext, AgentPermissions, ExecutionPolicy
from leanctx_sdk.agent import SUPPORTED_AGENT_TOOLS_ENGINE_VERSION


FACT = "agent_contract_fact = bounded_context_reuse"


def verify(
    engine: Path,
    expected_engine_version: str,
    wire_evidence_out: Optional[Path] = None,
) -> dict[str, object]:
    if wire_evidence_out is None:
        return _verify(engine, expected_engine_version)
    # PRIVATE_PENDING_REVIEW (this branch and `agent_wire_evidence.py`): the same run,
    # with the lines of the session recorded at the SDK's byte seam and bound in a new
    # file. Self-collected evidence of this caller; no origin, release or pair claim.
    import agent_wire_evidence

    return agent_wire_evidence.verify_recorded(
        _verify, engine, expected_engine_version, wire_evidence_out, Path(__file__)
    )


def _verify(engine: Path, expected_engine_version: str) -> dict[str, object]:
    if expected_engine_version != SUPPORTED_AGENT_TOOLS_ENGINE_VERSION:
        raise RuntimeError(
            "SDK Engine version constant does not match the release gate"
        )
    with tempfile.TemporaryDirectory(prefix="leanctx-agent-e2e-") as directory:
        root = Path(directory)
        repeated = "\n".join(
            f"irrelevant_{index % 8} = repeated context" for index in range(512)
        )
        (root / "knowledge.py").write_text(
            f'"""Deterministic AgentContext fixture."""\n{FACT!s}\n{repeated}\n',
            encoding="utf-8",
        )

        permissions = AgentPermissions(write=True, execute=True)
        policy = ExecutionPolicy(max_timeout=30, allowed_executables=("git",))
        with AgentContext(
            root,
            task="Find agent_contract_fact and verify the workspace",
            permissions=permissions,
            execution_policy=policy,
            instance_binary=engine,
        ) as context:
            required = {
                "ctx_compose",
                "ctx_patch",
                "ctx_read",
                "ctx_search",
                "ctx_shell",
                "ctx_tree",
            }
            missing = required.difference(context.capabilities)
            if missing:
                raise RuntimeError("missing capabilities: " + ",".join(sorted(missing)))

            tree = context.tree(depth=2)
            search = context.search("agent_contract_fact", path=".")
            first = context.read("knowledge.py", "task")
            second = context.read("knowledge.py", "task")
            composed = context.compose()
            created = context.create_file("agent-output.txt", "status=pending\n")
            replaced = context.replace_unique("agent-output.txt", "pending", "verified")
            command = context.run(("git", "--version"), timeout=10)

            if "knowledge.py" not in tree.text:
                raise RuntimeError("tree result omitted the fixture")
            if "agent_contract_fact" not in search.text:
                raise RuntimeError("search result omitted the required fact")
            if "agent_contract_fact" not in first.text:
                raise RuntimeError("read result omitted the required fact")
            if not composed.text:
                raise RuntimeError("compose returned no context")
            if not created.text or not replaced.text:
                raise RuntimeError("write operations returned no Engine evidence")
            if (root / "agent-output.txt").read_text(
                encoding="utf-8"
            ) != "status=verified\n":
                raise RuntimeError("Engine-backed edit produced the wrong content")
            if command.shell is None or command.shell.get("exitCode") != 0:
                raise RuntimeError("structured argv execution failed")
            if context.metrics.original_tokens <= 0:
                raise RuntimeError("Engine did not report original token metrics")
            if context.metrics.saved_tokens <= 0:
                raise RuntimeError("fixture did not prove positive token savings")
            if second.saved_tokens < first.saved_tokens:
                raise RuntimeError("persistent session reuse regressed saved tokens")

            return {
                "agent_tools_interface_version": "1.0.0",
                "capabilities": sorted(required),
                "engine_version": expected_engine_version,
                "operations": 8,
                "output_tokens": context.metrics.output_tokens,
                "original_tokens": context.metrics.original_tokens,
                "saved_ratio": round(context.metrics.saved_ratio, 6),
                "saved_tokens": context.metrics.saved_tokens,
                "schema_version": 1,
                "sdk_version": "1.3.0",
                "status": "PASS",
                "transport_version": 1,
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--expected-engine-version", required=True)
    parser.add_argument("--wire-evidence-out", type=Path, default=None)
    args = parser.parse_args()
    report = verify(
        args.engine.resolve(strict=True),
        args.expected_engine_version,
        args.wire_evidence_out,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
