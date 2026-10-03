"""Flagship journey: "Fix the production login issue" through the real Engine.

A coding agent gets the task and 21 candidate sources: the login code, the
issue, a failing deploy log, an `.env` with credentials, and unrelated docs.
The local Engine decides what may be used and what may leave:

1. source plan  - `.env` is denied by permission and never planned; the
   planner selects the relevant permitted sources within the budget;
2. assembly     - the agent builds its model request from the selected
   sources only;
3. egress       - the request passes `egress-admit`: credentials that slipped
   into the selected log and issue are masked; a decision receipt is written;
4. HUD          - the status line is computed from the plan and the receipt,
   never typed in.

Every number in the output is measured by this run. No model is called.

    python examples/gateway_login_journey.py --engine /path/to/lean-ctx
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leanctx_sdk import SubprocessEngineClient  # noqa: E402
from leanctx_sdk.planning import (  # noqa: E402
    MAX_ENGINE_CONTEXT_PLAN_TOKENS,
    EnginePlanningRequest,
    EngineSource,
)
from leanctx_sdk.preview import admit_egress  # noqa: E402

TASK = "Fix the production login issue"
BUDGET_TOKENS = 1200
AWS_KEY = "AKIA" + "Q3EGRZ7LOGINX4KEY"
SECOND_KEY = "AKIA" + "ZZ9PQ7JOURNEYXKEY"
DB_PASSWORD = "Pr0d-" + "login-" + "s3cret!"


def sources():
    relevant = {
        "src/auth/login.py": (
            "filesystem",
            "def login(request):\n    user = users.find(request.email)\n"
            "    if not user or not verify(user, request.password):\n"
            "        raise LoginFailed('invalid credentials')\n"
            "    session = sessions.create(user, ttl=SESSION_TTL)\n"
            "    return redirect('/dashboard', cookie=session.cookie())\n",
        ),
        "src/auth/session.py": (
            "filesystem",
            "SESSION_TTL = int(os.environ['SESSION_TTL'])\n"
            "def create(user, ttl):\n    return Session(user.id, expires=now() + ttl)\n",
        ),
        "issue/LOGIN-482": (
            "issue_tracker",
            "Production login fails since the 14:05 deploy: every login redirects back "
            "to /login. Support pasted the deploy key they used: " + AWS_KEY + ".\n",
        ),
        "logs/deploy-14-05.log": (
            "other",
            "14:05:12 deploy: SESSION_TTL unset, defaulting to 0\n"
            "14:05:13 auth: session expired immediately (ttl=0)\n"
            "14:05:13 aws: using access key " + SECOND_KEY + "\n",
        ),
        "src/auth/middleware.py": (
            "filesystem",
            "def require_login(handler):\n    if session.expired():\n"
            "        return redirect('/login')\n    return handler()\n",
        ),
    }
    # Realistically sized unrelated documents (each a few hundred tokens).
    unrelated = {
        f"docs/{name}.md": (
            "filesystem",
            "".join(f"{name.replace('-', ' ')} section {n}: {text}.\n" for n in range(1, 41)),
        )
        for name, text in [
            ("brand-guide", "logo spacing and color palette for print"),
            ("holiday-calendar", "office closures for the winter break"),
            ("expense-policy", "meal limits and travel class rules"),
            ("onboarding-checklist", "laptop pickup and badge photo"),
            ("press-kit", "founder bios and product screenshots"),
            ("parking", "garage levels and visitor spaces"),
            ("cafeteria-menu", "weekly lunch rotation"),
            ("team-offsite", "agenda for the mountain retreat"),
            ("design-tokens", "spacing scale and typography ramp"),
            ("newsletter-archive", "past community newsletters"),
            ("pricing-faq", "plan comparison for sales"),
            ("accessibility-statement", "public accessibility commitments"),
            ("job-ladder", "engineering levels and expectations"),
            ("event-sponsorship", "conference booth checklist"),
            ("social-media-guide", "tone of voice for posts"),
        ]
    }
    entries = {**relevant, **unrelated}
    built = []
    for ref, (kind, content) in entries.items():
        built.append(_source(ref, kind, content, "permitted"))
    # The .env is in the workspace but not permitted for model context.
    env = f"DATABASE_URL=postgres://app:{DB_PASSWORD}@db.internal/prod\nAWS_ACCESS_KEY_ID={AWS_KEY}\n"
    built.append(_source(".env", "filesystem", env, "denied"))
    return built, set(relevant)


def _source(ref, kind, content, permission):
    return EngineSource(
        {
            "object_ref": ref,
            "source_id": "src-" + hashlib.sha256(ref.encode()).hexdigest()[:16],
            "source_type": kind,
            "content_digest": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
            "revision": "r1",
            "owner": "acme",
            "observed_at": None,
            "valid_until": None,
            "classification": "Internal",
            "permission": permission,
        },
        content,
    )


def kilo(tokens):
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


def run(engine_binary):
    engine = SubprocessEngineClient(engine_binary, timeout=60)
    root = tempfile.mkdtemp(prefix="leanctx-journey-")
    candidates, relevant = sources()
    plan = engine.context_plan_sources(
        root, EnginePlanningRequest("login-incident", TASK, BUDGET_TOKENS), candidates
    )["result"]["plan"]
    planned = {s["source_ref"]: s for s in plan["selections"]}
    by_ref = {c.descriptor["object_ref"]: c for c in candidates}
    selected = [by_ref[ref] for ref, s in planned.items() if s["disposition"] == "selected"]
    # The planner counts tokens only for what fits; to report what was
    # considered, the Engine measures every permitted candidate once more
    # without a budget limit. Nothing here is estimated by the app.
    measured = engine.context_plan_sources(
        root, EnginePlanningRequest("login-measure", TASK, MAX_ENGINE_CONTEXT_PLAN_TOKENS),
        candidates,
    )["result"]["plan"]["selections"]
    considered_tokens = sum(s["token_count"] for s in measured)
    blocked = [c for c in candidates if c.descriptor["permission"] == "denied"]

    messages = [{"role": "system", "content": "You are a careful on-call engineer."}]
    for source in selected:
        messages.append({"role": "user",
                         "content": f"[{source.descriptor['object_ref']}]\n{source.content}"})
    messages.append({"role": "user", "content": TASK})
    admission = admit_egress(engine, root, provider="anthropic",
                             upstream_base="https://api.anthropic.com",
                             body={"model": "claude-x", "max_tokens": 1024, "messages": messages})
    receipt = admission.receipt
    delivered = receipt.tokens["delivered"]
    reduction = round(100 * (1 - delivered / considered_tokens)) if considered_tokens else 0
    sent = json.dumps(admission.body)
    hud = (f"LeanCTX 🛡  {kilo(considered_tokens)} → {kilo(delivered)}  ↓{reduction}%\n"
           f"{receipt.security['redactions']} credentials redacted · "
           f"{len(blocked)} source blocked · {len(selected)}/{len(candidates)} sources used")
    return {
        "task": TASK,
        "sources_total": len(candidates),
        "sources_blocked": [c.descriptor["object_ref"] for c in blocked],
        "sources_selected": sorted(s.descriptor["object_ref"] for s in selected),
        "relevant_selected": sorted(relevant & {s.descriptor["object_ref"] for s in selected}),
        "irrelevant_selected": sorted({s.descriptor["object_ref"] for s in selected} - relevant),
        "considered_tokens": considered_tokens,
        "receipt_tokens": dict(receipt.tokens),
        "redactions": receipt.security["redactions"],
        "classification": admission.classification,
        "receipt_id": receipt.receipt_id,
        "final_context": receipt.final_context,
        "credentials_left": [name for name, value in
                             [("aws", AWS_KEY), ("aws2", SECOND_KEY), ("db", DB_PASSWORD)]
                             if value in sent],
        "hud": hud,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, help="path to the lean-ctx Engine binary")
    args = parser.parse_args()
    result = run(args.engine)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
