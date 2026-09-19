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

## Standalone Enterprise planning

`EnterpriseEngineClient(base_url, credential, tenant_id)` calls the authenticated
`POST /v1/engine/context-plan` contract at Enterprise commit `cff624549b`.
`context_plan(request, source_ids)` supplies at most 64 non-nil UUID source IDs;
the credential is a bearer header and tenant authorization remains server-owned.
The configured tenant is an expected response binding, never a tenant override
in the request. Results include the persisted governance revision and canonical
source plan. The SDK rejects another tenant or unrequested source references.

`context_materialize(request, source_ids, expected_governance_revision, expected_binding_digest, planning_evaluation_time=None)` calls the additive authenticated `POST /v1/engine/context-materialize` contract. It reuses the tenant, source, governance and binding joins, and validates the nested plan with the existing source-plan parser, including additive plan extensions. The optional evaluation-time value is a canonical UTC-second identity echo for retention-aware planning; it is not authentication, provenance, or a receipt.

The SDK validates the exact materialization envelope, UTF-8 content bound (1 MiB), and SHA-256 content digest. `materialized_token_count` is only a bounded host-reported metric: the SDK does not recompute it, bill it, or treat materialization as execution or a receipt. The method returns the server's materialized context and validated plan; it does not add a second ledger or outcome authority.

HTTPS is required by default. Explicit `allow_loopback_http=True` permits literal
loopback IPs for local tests only. Redirects, environment proxies and automatic
POST retries are disabled. Responses are bounded; socket I/O and the connected
request have a deadline. The operating system's DNS resolver is not cancellable
by this synchronous standard-library client, so lookup time is not covered by a
hard wall-clock guarantee. Supply a reachable controlled endpoint.

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

`EnterpriseEngineClient` remains planning-only; `EngineContextClient` is the
separate guarded read surface. Neither client's component checks establish
six-language SDK parity, Windows transport, deployment or installed-channel
acceptance. Existing SDK license terms remain in `LICENSE`; new source headers
reference `LicenseRef-LeanCTX-SDK-Source-1.0`.
