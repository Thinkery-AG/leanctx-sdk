# Engine planning (unreleased v4 integration)

This integration-branch Python surface consumes the canonical public Engine protocol at
LeanCTX commit `4045cd5a2dd85f92a7792b0786e99c23010a5e62`; it does not implement a
planner, policy authority, or private intelligence runtime. The local executable
must also support bounded planning input on stdin (`91edced021` or compatible).
The existing `EngineClient` injection protocol and ContextSession operations are
unchanged; `EnginePlanningClient` is a separate, additive protocol.

```python
from leanctx_sdk import EnginePlanningRequest, SubprocessEngineClient

engine = SubprocessEngineClient("/opt/leanctx/bin/lean-ctx")
request = EnginePlanningRequest("investigate-issue", "invoice ledger", 2048)
result = engine.context_plan("/srv/project", request)
print(result["plan"]["projection_digest"])
```

`context_plan_sources(project_root, request, sources)` accepts up to 64
`EngineSource(descriptor, content)` values. Descriptors use the public fields
`object_ref`, `source_id`, `source_type`, `content_digest`, `revision`, `owner`,
`observed_at`, `valid_until`, `classification`, and `permission`. Per-source
content is bounded to 64 KiB, the complete request to 1 MiB. Source types are
`filesystem`, `issue_tracker`, `relational_database`, and `other`; these labels
describe supplied evidence, not a claim that a connector ran.

The host owns the project and executable. Source permission labels can restrict
planning but do not authenticate a remote caller. The subprocess receives
planning requests on stdin, without a temporary source-body file. Existing
context-view/recover transport remains compatible. Responses must match the
task, budget, projection digest and selected-source bindings; failures are typed.

## Standalone Enterprise source operations

`EnterpriseEngineClient(base_url, credential, tenant_id)` calls the authenticated
`POST /v1/engine/context-plan` contract at Enterprise commit `cff624549b`.
`context_plan(request, source_ids)` supplies at most 64 non-nil UUID source IDs;
the credential is a bearer header and tenant authorization remains server-owned.
The configured tenant is an expected response binding, never a tenant override
in the request. Results include the persisted governance revision and canonical
source plan. The SDK rejects another tenant or unrequested source references.

`context_materialize(request, source_ids, expected_governance_revision, expected_binding_digest, planning_evaluation_time=None)` calls the additive authenticated `POST /v1/engine/context-materialize` contract. It reuses the tenant, source, governance and binding joins, and validates the nested plan with the existing source-plan parser, including additive plan extensions. The optional evaluation-time value is a canonical UTC-second identity echo for retention-aware planning; it is not authentication, provenance, or a receipt.

The SDK validates the exact materialization envelope, UTF-8 content bound (1 MiB), and SHA-256 content digest. `materialized_token_count` is only a bounded host-reported metric: the SDK does not recompute it, bill it, or treat materialization as execution or a receipt. The method returns the server's materialized context and validated plan; it does not add a second ledger or outcome authority.

`context_execute(task, plan, request, source_ids, expected_governance_revision,
expected_binding_digest, planning_evaluation_time=None)` calls
`POST /v1/engine/context-execute` (Enterprise `90d54cf5c2` or compatible).
The caller declares canonical public `TaskEnvelopeV1`/`ExecutionPlanV1` mappings
for the local-native context capability. The task tenant must match the client's
expected tenant; the agent must be the server-authenticated stable key/session ID.
The server derives source bodies and owns signer configuration and admission.
No signer secret, artifact path or caller-supplied source body is accepted here.

The SDK verifies the exact declared plan, allowing only the Engine's context ID
and decision-ref additions, and joins task/source/invocation/observation/output
digests. Retention evaluation time is echoed and checked when supplied. Request
bytes are capped at1 MiB and response bytes at3 MiB. No POST is automatically
retried: failed disclosure may follow a durable attempt, so do not assume a
failure means execution never occurred. A fresh task is a new attempt.

The v1 canonical receipt remains an `unknown` outcome projection, not full signed
bytes or independent signature verification. Source execution does not imply
model invocation, accepted learning or provider billing. Opt-in Engine v2 signed
delivery is a separate contract and is not silently negotiated by this method.

`context_execute_v2(task, plan, request, source_ids, expected_governance_revision,
expected_binding_digest, planning_evaluation_time=None)` explicitly calls
`POST /v2/engine/context-execute`. It returns the same authenticated outer v2
envelope as Enterprise: `schema_version: 2`, tenant and governance bindings,
and `execution: {schema_version: 2, execution: <validated v1 projection>,
receipt_document_json: <exact string>}`. UTF-8 encoding that returned string
recovers the exact signed receipt bytes; the SDK checks its 1 MiB bound and
digest join to `canonical_receipt.receipt_digest`, but does not parse the
ReceiptDocument, admit signer keys, or claim cryptographic verification.
Independent trust-store verification remains the caller's existing authority.

`context_outcome(task_id, receipt_digest, context_decision_digest, signals)`
calls the authenticated `POST /v1/engine/context-outcome` adapter. The request
contains only the task and digest bindings plus 1–16 restricted public signal
values (`boolean`, bounded `count`, or `unknown`); tenant, agent, signer,
filesystem and learning fields are never caller inputs. The Enterprise host
binds tenant and agent to the credential, rechecks its independently granted
`outcome:write` scope, and remains the authority for signature admission,
outcome evaluation and ledger mutation.

The SDK validates the strict `{schema_version, tenant_id, outcome}` envelope,
receipt/original-digest joins, acceptance state, UTF-8 receipt-document bound
(1 MiB), exact document digest, required document envelope, canonical JSON and
derived receipt ID, plus selected joins for outcome state, predecessor digest,
task lineage and runtime decision evidence. It
preserves `receipt_document_json` so a caller can recover the returned bytes,
but does not fully parse or cryptographically verify the receipt, admit signer
keys, or claim accepted learning, billing or accounting. POSTs are not retried.

`provider_execute(task, plan, request, source_ids, expected_governance_revision,
expected_binding_digest, *, max_output_tokens, planning_evaluation_time=None)`
calls `POST /v1/engine/provider-execute` using the public provider-execution v1
contract at `a2289b132b` (Enterprise `6ebe4a9` or compatible). Unlike the
local-native context operation, this requires an explicit provider/model,
zero retries, no fallbacks, a concrete context-plan ID and a bounded token plan.
The server validates exact context-plan equality and owns source grants, policy,
dispatch admission and wallet settlement. A planning result alone is not egress
permission. Request/response bytes are each capped at 1 MiB.

The direct response distinguishes provider execution status from acceptance
(always `unknown`) and measured usage from unavailable usage. Cost is separately
unavailable, a usage-priced estimate, or an acknowledged managed-wallet charge;
an estimate is never an invoice. The SDK checks task/plan/provider joins, output
SHA-256 and bounded counters. Host-owned request/context digests are syntax
checked, not independently reconstructed or signed. No receipt, signer admission,
accepted outcome, billing authority or automatic POST retry is added. A transport
failure may follow an already-dispatched attempt; do not retry under a new task
identity without resolving the original attempt.

TypeScript exposes the same provider boundary through
`EnterpriseEngineClient.providerExecute(task, plan, request, sourceIds,
expectedGovernanceRevision, expectedBindingDigest, {maxOutputTokens,
planningEvaluationTime?})`. It reuses the existing authenticated transport and
task/plan validators; local-native execution retains its separate guard. Wire
versions and usage/cost counters must use JSON integer tokens, not decimal or
exponent notation. JavaScript counters are restricted to safe integers. The
same unknown-acceptance, host-owned digest and no-retry limitations apply; this
addition does not establish parity for other SDK languages.

HTTPS is required by default. Explicit `allow_loopback_http=True` permits literal
loopback IPs for local tests only. Redirects, environment proxies and automatic
POST retries are disabled. Responses are bounded; socket I/O and the connected
request have a deadline. The operating system's DNS resolver is not cancellable
by this synchronous standard-library client, so lookup time is not covered by a
hard wall-clock guarantee. Supply a reachable controlled endpoint.

### Go Enterprise source planning and materialization (unreleased)

The unreleased Go package adds the narrower Enterprise source-planning slice:
`NewEnterpriseEngineClient(baseURL, credential, tenantID, options...)`,
`ContextPlanSources` and `ContextPlanSourcesContext`. It reuses the guarded HTTP
transport with caller cancellation and validates the canonical response's task,
budget, projection digest, selected-source bindings and expected tenant. The
source IDs are bounded non-nil UUIDs; the configured tenant is never sent as an
override. `ContextMaterializeSources` and `ContextMaterializeSourcesContext`
take an `EngineSourceMaterializationRequest` containing the original planning
request/source IDs, expected governance revision and binding digest, and optional
planning evaluation time. Preserve `context_plan_evaluation_v1.evaluation_time`
from the returned plan when present; a fresh evaluation can change its binding.
They verify the returned source plan and content digest,
and bound the reported token metric to the plan budget. That metric is not an
independent tokenizer measurement. Neither operation performs provider execution,
receipt delivery or outcome reporting. No methods are added to the existing
injected `EngineClient`.

The Go `ContextExecuteV2`/`ContextExecuteV2Context` adapter takes an
`EngineSourceExecutionV2Request` containing the caller's task, declared
local-native execution plan and bound materialization request. It checks the
returned invocation/observation, source/task/plan lineage, declared plan and
exact receipt-document byte digest. The returned receipt outcome remains
`unknown`. Receipt bytes are preserved for verification with independently
admitted keys; this adapter does not establish signer trust or successful task
acceptance. It is not the provider-execution API.

Go `ProviderExecute`/`ProviderExecuteContext` accept
`EngineProviderExecutionRequest` with a concrete non-local plan, bound
materialization and an output-token ceiling. The adapter validates task/plan,
provider/model, bounded output digest, usage totals and cost basis while
preserving unavailable counters as `nil`. It neither retries/falls back nor
upgrades a provider response into task acceptance or proof of a billed charge.

Go `ContextOutcome`/`ContextOutcomeContext` accept `EngineOutcomeRequest` with
task ID, original receipt digest, context-decision digest and restricted signals.
The fixed `/v1/engine/context-outcome` request carries no caller-supplied tenant
or signing identity. `EngineOutcomeResponse` preserves the host's accepted or
rejected successor and replay flag, with exact document bytes and selected
canonical identity/task/predecessor/runtime-evidence joins checked as in the
Python adapter above. This is an operator-attestation carrier, not an outcome
evaluator, independent signer verifier, learning trigger or accounting authority.
The caller must explicitly choose to retry; the transport never does so.

## Guarded Engine context read

`EngineContextClient(base_url, credential).context_read(path)` uses the same
bounded authenticated transport for `POST /v1/tools/call`, requesting exactly
the guarded Engine v1 single-path aggressive `ctx_read` operation. The host
owns project-root/path-jail enforcement and signer configuration; the SDK sends
no signer secret, tenant override, or receipt configuration.

The result is an `EngineContextReadResult` containing the returned text, the
host's canonical receipt metadata, and the complete raw JSON response. The SDK
requires `receipt_ref == "id:" + receipt_digest`, a schema version of `1`,
`outcome == "unknown"`, and `delivery == "native_engine_view"`; unknown raw
response fields are preserved. The current HTTP host exposes receipt metadata
only, not a receipt-artifact fetch route or signed receipt bytes, so the SDK does
not claim signature verification or response-level tenant binding.

The language-neutral SDK projection is recorded in
`contracts/guarded-context-read-v1.json`; its synthetic conformance fixture is
`fixtures/guarded-context-read-v1/conformance.json`. These describe the existing
Engine tool boundary, not a new Engine protocol or a signed runtime receipt.
Python and Go consume the same fixture; passing it does not prove a deployment.

The Go package exposes `NewEngineContextClient(baseURL, credential, options...)`
with `EngineContextClientOptions{AllowLoopbackHTTP: true}` for explicit local
tests. `ContextRead(path)` and `ContextReadContext(ctx, path)` return text,
canonical receipt metadata and the raw response. The additive HTTP adapter does
not add required methods to the existing Go `EngineClient` interface.

`EnterpriseEngineClient` provides source planning/materialization/execution;
`EngineContextClient` is the
separate guarded read surface. Neither client's component checks establish
six-language SDK parity, Windows transport, deployment or installed-channel
acceptance. Existing SDK license terms remain in `LICENSE`; new source headers
reference `LicenseRef-LeanCTX-SDK-Source-1.0`.
