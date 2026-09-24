"""Installed Python AgentContext reference; local source protection, no model calls."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import tempfile

import leanctx_sdk
from leanctx_sdk import AgentContext, EngineProtocolError
from leanctx_sdk.errors import AgentPermissionError, EngineExecutionError


def verify(engine: Path, previous_engine: Path) -> dict[str, object]:
    installed = Path(leanctx_sdk.__file__).resolve()
    if "site-packages" not in installed.parts:
        raise RuntimeError("reference must import the installed wheel")
    provenance = distribution("thinkery-leanctx-sdk").read_text("direct_url.json")
    if not provenance:
        raise RuntimeError("installed package provenance is missing")
    checks: dict[str, bool] = {}
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
        rules = (
            'name="installed-reference"\nversion="1.0.0"\ndescription="test"\n'
            '[filters]\nclassification="block"\n'
            '[redaction]\ncustomer="CUS-[0-9]{4}"\n'
        )
        policy.write_text(rules)
        try:
            with AgentContext(root, engine_binary=previous_engine):
                pass
        except EngineProtocolError as error:
            checks["old_engine_rejected"] = "Agent Tools hello is incompatible" in str(
                error
            )
        else:
            raise RuntimeError("old Engine unexpectedly admitted")
        with AgentContext(root, engine_binary=engine) as context:
            read = context.read("login.py", "full")
            composed = context.compose("investigate authentication retry")
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
            policy.write_text(rules + '[context]\ndeny_tools=["ctx_read"]\n')
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
            metrics = {
                "original_tokens": context.metrics.original_tokens,
                "output_tokens": context.metrics.output_tokens,
                "saved_tokens": context.metrics.saved_tokens,
            }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "responses": {
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
        "scope": "Installed Python package, actual local Engine subprocess; context returned to application. GitLab, Codex, other SDKs and model transmission not proven by this fixture.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--previous-engine", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(
        args.engine.resolve(strict=True), args.previous_engine.resolve(strict=True)
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "checks": result["checks"]}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
