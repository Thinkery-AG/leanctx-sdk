# Gateway preview — egress admission and decision receipts

> **Preview.** The contract `leanctx-gateway-preview` 0.1 may change in minor
> releases. It requires an Engine that implements `engine egress-admit`.

Before your app sends a model request, it hands the request to the local
LeanCTX Engine. The Engine does three things:

- It masks secrets, such as provider keys and tokens.
- It withholds content that must not reach a remote model, such as anything
  marked `Restricted`.
- It classifies the request (`public`, `internal`, `confidential` or
  `restricted`).

It returns three things:

- the body that may leave,
- the request's classification,
- a **decision receipt**: who asked, where the request goes, which detectors
  ran over how much of it, what was redacted or withheld and why, and the
  digest of what was delivered.

Two rules for callers:

1. Send `admission.body`, **never** your original body.
2. Send only when `may_send` is true. A refused request must not be sent.

```python
from leanctx_sdk import SubprocessEngineClient
from leanctx_sdk.preview import admit_egress

engine = SubprocessEngineClient("lean-ctx")
admission = admit_egress(engine, project_root, provider="openai",
                         upstream_base="https://api.openai.com", body=request)
if admission.may_send:
    response = my_openai_client.post(admission.body)
receipt = admission.receipt  # ContextDecisionReceipt
```

The same API exists in all six SDKs:

| SDK | Entry point |
|---|---|
| Python | `leanctx_sdk.preview.admit_egress` |
| TypeScript | `admitEgress` from `@thinkery/leanctx-sdk/preview` |
| Go | `(*SubprocessEngineClient).AdmitEgress` |
| Rust | `SubprocessEngineClient::admit_egress`, types in `leanctx_sdk::preview` |
| JVM | `com.thinkery.leanctx.GatewayPreview.admitEgress` |
| .NET | `Thinkery.LeanCtx.Preview.GatewayPreview.AdmitEgressAsync` |

## Types

| Type | Meaning |
|---|---|
| `ContextPrincipal` | Who asked. `unknown` is explicit and never authorizes. A host that knows the requester, such as the Enterprise Suite, binds the principal. |
| `ContextDestination` | Provider, model, region and locality (`local`, `remote` or `unknown`). Organisation management is only ever attested. |
| `ContextDecision` | One decision per inspected object, identified by digest and never by content. Each has a disposition (`allow` … `deny`), reason codes and required transformations. |
| `SecuritySignal` | One detector's result: category, severity, evidence count, coverage and status. It carries counts only, never the matched value. |
| `ContextDecisionReceipt` | The whole delivery: mode, principal, destination, policy, source and security counts, tokens, outcome, and the final context digest. |
| `EgressAdmission` | Disposition (`forward`, `rewritten` or `refused`), body, refusal, classification and receipt. |

Parsing is strict in every SDK and mirrors the Engine's own validation.
Unknown fields and unknown values are rejected, and so are inconsistent
receipts, for example a withheld receipt that names a delivered context.

## Conformance

- `fixtures/gateway-preview-v1/valid/*`: real Engine responses for a masked
  credential, withheld restricted content and a classified marking.
- `fixtures/gateway-preview-v1/invalid/*`: 29 documents that each violate
  exactly one invariant. Every SDK must reject all of them.
- Live: with `LEANCTX_ENGINE_BINARY` set, every SDK runs the same three
  journeys against a real Engine.

## Reference apps

These need no MCP, no UI and no cloud:

- `examples/gateway_minimal_app.py` (A): the smallest app that only sends
  admitted bodies.
- `examples/gateway_agent_loop.py` (B): your own tool-using agent loop. Every
  model call is admitted. A credential in tool output is masked, and
  restricted tool output is replaced by a withheld marker. The run summary
  measures what the model actually saw.
