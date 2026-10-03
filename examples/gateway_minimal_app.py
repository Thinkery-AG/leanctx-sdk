"""Reference app A: the smallest app that sends nothing it should not.

Before every model call the request goes through the local Engine's egress
admission. The app sends `admission.body` - never its own body - and only
when `admission.may_send`. No MCP, no UI, no cloud: the "model" here is a
local stand-in that records what it would have received.

    python examples/gateway_minimal_app.py --engine /path/to/lean-ctx

Prints one JSON summary line per request.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leanctx_sdk import SubprocessEngineClient  # noqa: E402
from leanctx_sdk.preview import admit_egress  # noqa: E402

OPENAI = "https://api.openai.com"


def send_to_model(body):
    """Stand-in for the provider call; returns what the provider would see."""
    return json.dumps(body, sort_keys=True)


def ask(engine, root, prompt):
    request = {"model": "gpt-4o-mini", "temperature": 0.2,
               "messages": [{"role": "user", "content": prompt}]}
    admission = admit_egress(engine, root, provider="openai", upstream_base=OPENAI, body=request)
    sent = send_to_model(admission.body) if admission.may_send else None
    receipt = admission.receipt
    return {
        "disposition": admission.disposition,
        "classification": admission.classification,
        "sent": sent is not None,
        "provider_saw": sent,
        "receipt_id": receipt.receipt_id if receipt else None,
        "outcome": receipt.outcome if receipt else None,
        "redactions": receipt.security["redactions"] if receipt else 0,
        "withheld_objects": receipt.security["blocked_objects"] if receipt else 0,
        "reasons": sorted({code for d in receipt.decisions for code in d.reason_codes}) if receipt else [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, help="path to the lean-ctx Engine binary")
    args = parser.parse_args()
    engine = SubprocessEngineClient(args.engine)
    root = tempfile.mkdtemp(prefix="leanctx-app-a-")
    credential = "AKIA" + "Q3EGRZ7DEMOX4KEY"
    for prompt in [
        "Summarise why CI is red.",
        f"Deploy fails with {credential} - what is wrong?",
        "Classification: Secret\nThe customer list affected by the incident.",
    ]:
        print(json.dumps(ask(engine, root, prompt), sort_keys=True))


if __name__ == "__main__":
    main()
