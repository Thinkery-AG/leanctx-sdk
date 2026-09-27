"""Installed Python AgentContext reference; local source protection, no model calls.

Optional --project PATH uses a caller-prepared project; otherwise an isolated fixture is created.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from importlib.metadata import distribution
from pathlib import Path
from typing import Iterator

import leanctx_sdk
from leanctx_sdk import AgentContext, EngineProtocolError, GitLabSource
from leanctx_sdk.errors import AgentPermissionError, EngineExecutionError


@contextmanager
def _reference_project(project: Path | None) -> Iterator[Path]:
    if project is not None:
        root = project.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(root)
        yield root
        return

    with tempfile.TemporaryDirectory(
        prefix="leanctx-pro-agent-reference-"
    ) as temporary:
        root = Path(temporary)
        (root / ".git").mkdir()
        (root / "login.py").write_text(
            "# authentication retry: refresh the expired session before retrying\n"
            'def authenticate():\n    return "REFRESH_SESSION_FIRST CUS-1234"\n'
        )
        (root / "private.py").write_text(
            '# CONFIDENTIAL\ndef authentication_secret():\n    return "PRIVATE_CANARY"\n'
        )
        policy = root / ".lean-ctx/policy.toml"
        policy.parent.mkdir()
        policy.write_text(
            'name="installed-reference"\nversion="1.0.0"\ndescription="test"\n'
            '[filters]\nclassification="block"\n'
            '[redaction]\ncustomer="CUS-[0-9]{4}"\n'
        )
        yield root


def verify(
    engine: Path, previous_engine: Path, project: Path | None = None
) -> dict[str, object]:
    installed = Path(leanctx_sdk.__file__).resolve()
    if "site-packages" not in installed.parts:
        raise RuntimeError("reference must import the installed wheel")
    provenance = distribution("thinkery-leanctx-sdk").read_text("direct_url.json")
    if not provenance:
        raise RuntimeError("installed package provenance is missing")
    checks: dict[str, bool] = {}
    source_path = os.environ.get("LEANCTX_REFERENCE_GITLAB_SOURCE")
    source_config = json.loads(Path(source_path).read_text()) if source_path else None
    source = GitLabSource(**source_config) if source_config else None
    with _reference_project(project) as root:
        policy = root / ".lean-ctx/policy.toml"
        rules = policy.read_text()
        try:
            with AgentContext(root, engine_binary=previous_engine):
                pass
        except EngineProtocolError as error:
            checks["old_engine_rejected"] = "Agent Tools hello is incompatible" in str(
                error
            )
        else:
            raise RuntimeError("old Engine unexpectedly admitted")
        with AgentContext(root, engine_binary=engine, gitlab_source=source) as context:
            read = context.read("login.py", "full")
            composed = context.compose("investigate authentication retry")
            if os.environ.get("LEANCTX_REFERENCE_PRO") == "1":
                checks["pro_context_selection"] = (
                    "Pro context selection:" in composed.text
                    and "Pro context selection unavailable" not in composed.text
                )
            checks["useful_masked_read"] = (
                "REFRESH_SESSION_FIRST" in read.text
                and "REDACTED" in read.text
                and "CUS-1234" not in read.text
            )
            checks["useful_protected_compose"] = (
                "REFRESH_SESSION_FIRST" in composed.text
                and "login.py" in composed.text
                and all(
                    value not in composed.text
                    for value in ("CUS-1234", "private.py", "PRIVATE_CANARY")
                )
            )
            temporary_rules = rules + ("" if rules.endswith("\n") else "\n")
            denied_context = '[context]\ndeny_tools=["ctx_read"]\n'
            policy.write_text(
                temporary_rules.replace('[context]', denied_context, 1)
                if '[context]' in temporary_rules else temporary_rules + denied_context
            )
            try:
                denied = context.read("login.py", "full")
            except (AgentPermissionError, EngineExecutionError) as error:
                checks["changed_rule_blocks_read"] = (
                    "policy" in str(error).lower()
                    and "REFRESH_SESSION_FIRST" not in str(error)
                    and "CUS-1234" not in str(error)
                )
            else:
                checks["changed_rule_blocks_read"] = (
                    "POLICY BLOCKED" in denied.text
                    and "REFRESH_SESSION_FIRST" not in denied.text
                    and "CUS-1234" not in denied.text
                )
            policy.write_text(rules)
            restored = context.read("login.py", "full")
            checks["same_session_rule_repair"] = (
                "REFRESH_SESSION_FIRST" in restored.text
                and "CUS-1234" not in restored.text
            )
            if source_config:
                query = dict(action="query", provider="gitlab", resource="merge_requests",
                             project=str(source_config["project"]), mode="snapshot", limit=1)
                snapshot = context.call("ctx_provider", query)
                observed = subprocess.run(
                    [sys.executable, os.environ["LEANCTX_REFERENCE_SNAPSHOT_OBSERVER"]],
                    input=snapshot.text.encode(), capture_output=True, timeout=15,
                )
                checks["live_selected_gitlab"] = observed.returncode == 0
                for name, change in [("foreign_project_refused", {"project": "other/project"}),
                                     ("unsupported_source_action_refused", {"action": "refresh"})]:
                    try:
                        context.call("ctx_provider", {**query, **change})
                    except AgentPermissionError:
                        checks[name] = True
                    else:
                        checks[name] = False
                try:
                    policy.unlink()
                    try:
                        context.read("login.py", "full")
                    except AgentPermissionError:
                        checks["source_policy_removal_closes_session"] = True
                    else:
                        checks["source_policy_removal_closes_session"] = False
                finally:
                    policy.write_text(rules)
            metrics = {
                "original_tokens": context.metrics.original_tokens,
                "output_tokens": context.metrics.output_tokens,
                "saved_tokens": context.metrics.saved_tokens,
            }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "responses": {} if source_config else {
            "read": read.text,
            "compose": composed.text,
            "restored": restored.text,
        },
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "previous_engine_sha256": hashlib.sha256(
            previous_engine.read_bytes()
        ).hexdigest(),
        "installed_module": str(installed),
        "installed_provenance": json.loads(provenance),
        "metrics": metrics,
        "scope": "Installed Python package, actual local Engine subprocess; context returned to application. Optional selected live GitLab is checked only when configured; no Codex or model transmission claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--previous-engine", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--project",
        type=Path,
        help="use a caller-prepared project (default: create an isolated fixture)",
    )
    args = parser.parse_args()
    result = verify(
        args.engine.resolve(strict=True),
        args.previous_engine.resolve(strict=True),
        args.project,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "checks": result["checks"]}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
