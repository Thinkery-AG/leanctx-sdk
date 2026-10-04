"""Reference app B: your own agent loop, governed at every model call.

A minimal tool-using agent without MCP, UI or cloud. Each turn the whole
conversation - including tool output the agent pulled in - goes through the
local Engine's egress admission before the model sees it. Tool output that
carries a credential is masked; tool output marked restricted is replaced by
a withheld marker before a remote model sees it (the receipt counts it as a
withheld object). A request the Engine refuses outright is not sent at all,
and the agent stops instead of falling back to its own messages. Every turn
leaves a decision receipt; the summary measures what the model actually saw.

The "model" is a deterministic local stand-in so the example runs offline.

    python examples/gateway_agent_loop.py --engine /path/to/lean-ctx

Prints one JSON summary of the run.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leanctx_sdk import SubprocessEngineClient  # noqa: E402
from leanctx_sdk.preview import admit_egress  # noqa: E402

ANTHROPIC = "https://api.anthropic.com"


def local_model(messages):
    """Deterministic stand-in: asks for the next file until it has read all."""
    read = [m for m in messages if m["role"] == "tool"]
    plan = ["deploy.env", "runbook.md", "incident.md"]
    if len(read) < len(plan):
        return {"tool": "read_file", "path": plan[len(read)]}
    return {"answer": "done"}


def run(engine_binary):
    engine = SubprocessEngineClient(engine_binary)
    root = Path(tempfile.mkdtemp(prefix="leanctx-app-b-"))
    credential = "AKIA" + "Q3EGRZ7DEMOX4KEY"
    files = {
        "deploy.env": f"AWS_ACCESS_KEY_ID={credential}\nREGION=eu-central-1\n",
        "runbook.md": "Restart the worker pool, then re-run the migration.\n",
        "incident.md": "Classification: Secret\nAffected customers: ...\n",
    }
    messages = [{"role": "user", "content": "Find out why the deploy fails."}]
    turns = []
    model_saw = []
    stopped = None
    for _ in range(8):
        admission = admit_egress(
            engine, str(root), provider="anthropic", upstream_base=ANTHROPIC,
            body={"model": "claude-x", "max_tokens": 512, "messages": messages},
        )
        receipt = admission.receipt
        turn = {
            "receipt_id": receipt.receipt_id if receipt else None,
            "classification": admission.classification,
            "outcome": receipt.outcome if receipt else None,
            "redactions": receipt.security["redactions"] if receipt else 0,
            "withheld_objects": receipt.security["blocked_objects"] if receipt else 0,
        }
        turns.append(turn)
        if not admission.may_send or (receipt and receipt.outcome == "failed"):
            # Never fall back to the original messages: stop instead.
            stopped = "request refused by the gateway; turn not sent"
            break
        # The model only ever sees the admitted conversation.
        model_saw.append(json.dumps(admission.body))
        step = local_model(admission.body["messages"])
        if "answer" in step:
            break
        messages.append({"role": "assistant", "content": f"read_file {step['path']}"})
        messages.append({"role": "tool", "content": files[step["path"]]})
    return {
        "turns": turns,
        "stopped": stopped,
        "model_calls": len(model_saw),
        "credential_reached_model": any(credential in body for body in model_saw),
        "restricted_reached_model": any("Affected customers" in body for body in model_saw),
        "receipts": sum(t["receipt_id"] is not None for t in turns),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, help="path to the lean-ctx Engine binary")
    args = parser.parse_args()
    print(json.dumps(run(args.engine), sort_keys=True))


if __name__ == "__main__":
    main()
