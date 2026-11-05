# @thinkery/leanctx-sdk

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

This Node.js and TypeScript SDK connects your host-owned model and workflow to
a local LeanCTX instance. Your application keeps its model, agent loop, and UI.
It exposes the five Stable lifecycle primitives plus the separate Stable
`AgentContext` and `AsyncAgentContext` tool clients.

The package has no runtime dependencies. LeanCTX instance subprocesses are always started
with `shell: false`, bounded request/response streams, secure temporary files,
strict v1 JSON validation, and explicit process-tree termination. Write and
execute capabilities require immutable, explicit permissions and allowlists.

```ts
import { AgentContext } from "@thinkery/leanctx-sdk";

const tools = await AgentContext.open(".", { task: "Inspect the API" });
try {
  const result = await tools.search("ContextSession", { path: "src" });
  console.log(result.text);
} finally {
  await tools.close();
}
```

The published SDK 1.1.1 release requires LeanCTX 3.10.1; current `main` sources
target LeanCTX 3.12.2. Check the release-specific compatibility record before
choosing a LeanCTX instance binary. The SDK source license permits non-production use;
production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG.

## Guarded instance context reads (v4 integration candidate)

`InstanceContextClient` uses the authenticated `/v1/tools/call` instance boundary;
it does not start a LeanCTX instance or grant access outside the host's project root.

```ts
import { InstanceContextClient } from "@thinkery/leanctx-sdk";

const client = new InstanceContextClient(engineUrl, engineCredential);
const context = await client.contextRead("src/api.ts");
// context.text, context.canonicalReceipt, context.rawResponse
```

HTTPS is required by default. Isolated development hosts may explicitly enable
`allowLoopbackHttp: true` for a literal loopback IP. Requests have a bounded
deadline and bounded payload/response sizes; redirects and retries are disabled.
Authentication rejection, policy denial, malformed responses, timeouts, and
unavailable hosts use the existing typed SDK errors. Credentials are not sent
to a redirect target.

The returned receipt metadata must identify a digest-bound receipt reference
with `outcome: "unknown"` and `delivery: "native_engine_view"`. Parsing that
metadata does **not** verify the receipt's signature, prove task acceptance, or
provide Enterprise tenant authorization. Full receipt verification remains a
separate responsibility. This additive v4 candidate is not a claim that the
published 3.10.1 instance supports this boundary.

## Explicit instance source planning and materialization

`InstanceSourcePlanningClient` is the additive local CLI adapter for
`context-plan-sources` and `context-materialize-sources`:

```ts
import {
  InstancePlanningRequest,
  InstanceSource,
  InstanceSourcePlanningClient,
  SubprocessInstanceClient,
} from "@thinkery/leanctx-sdk";

const client = new InstanceSourcePlanningClient(new SubprocessInstanceClient({ instanceBinary: "lean-ctx" }));
const request = new InstancePlanningRequest("invoice-task", "invoice ledger", 512);
const planned = await client.contextPlanSources(projectRoot, request, [source]);
const evaluationTime = (
  planned.result.plan.context_plan_evaluation_v1 as { evaluation_time?: string } | undefined
)?.evaluation_time;
const materialized = await client.contextMaterializeSources(
  projectRoot,
  request,
  [source],
  planned.binding_digest,
  evaluationTime,
);
```

Source bodies are sent through bounded stdin (`--json-file -`), never a temporary
request file. The request is capped at 1 MiB, each source body at 64 KiB, and the
response at 2 MiB. DTOs validate UTF-8, source content digests, selected-source
joins, projection/binding digests, budget bounds, and the optional second-precision
retention epoch. The epoch is an unsigned replay hint, not authorization or
provenance. Materialized token count is a bounded instance report; this client does
not treat it as billing, compression savings, execution, acceptance, or a receipt.
The existing `InstanceClient` context-view/recover interface is unchanged.

## Enterprise source planning (v4 integration candidate)

The same `EnterpriseInstanceClient` also supports
`contextOutcome(taskId, receiptDigest, contextDecisionDigest, signals)` for
authorized operator attestations. It preserves exact returned receipt JSON and
checks its byte digest, canonical identity and selected task/decision joins.
The host alone evaluates signals and admits signing authority; SDK parsing is
not signer verification, learning or accounting. Requests are never retried
automatically. `InstanceOutcomeSignal` and `InstanceOutcomeResponse` describe this
unreleased supporting contract without extending the stable lifecycle interface.

The authenticated `EnterpriseInstanceClient.contextPlanSources(request, sourceIds)`
calls `POST /v1/engine/context-plan` for the configured tenant. It accepts
governed source IDs, not source bodies or operator settings:

```ts
import { EnterpriseInstanceClient, InstancePlanningRequest } from "@thinkery/leanctx-sdk";

const enterprise = new EnterpriseInstanceClient(engineUrl, credential, tenantId);
const planned = await enterprise.contextPlanSources(
  new InstancePlanningRequest("task-id", "source query", 512),
  sourceIds,
);
// planned.tenant_id, planned.governance_revision, planned.plan
```

The response must bind the tenant, request task/budget, permitted selections,
requested source IDs and canonical projection/binding digests. This reuses the
local source-planning validator and the existing guarded HTTP transport; it
does not execute a plan, acquire a source, or grant access to a foreign tenant.

## Enterprise source materialization (v4 integration candidate)

`EnterpriseInstanceClient.contextMaterializeSources(request, sourceIds,
expectedGovernanceRevision, expectedBindingDigest, options)` calls
`POST /v1/engine/context-materialize` for the configured tenant, matching the
existing Python `context_materialize` and Go `ContextMaterializeSources` wire
contract:

```ts
const planned = await enterprise.contextPlanSources(request, sourceIds);
const evaluationTime = (
  planned.plan.result.plan.context_plan_evaluation_v1 as { evaluation_time?: string } | undefined
)?.evaluation_time;
const materialized = await enterprise.contextMaterializeSources(
  request,
  sourceIds,
  planned.governance_revision,
  planned.plan.binding_digest,
  { planningEvaluationTime: evaluationTime },
);
// materialized.materialization.content / .materialized_token_count / .plan
```

The caller sends only the governance revision and binding digest it observed
during planning; source bodies and signer settings stay host-owned. Before any
value reaches the caller the response must bind the tenant, echo the expected
governance revision, keep the plan within the requested and permitted source
IDs, reproduce the expected binding digest and planning evaluation epoch, hash
to the declared `materialized_digest`, and stay inside both the plan token
budget and the 1 MiB materialized-content bound. Envelope revisions, versions
and token counts are read through the strict integer JSON path, so `7.0` or
`4e0` is rejected instead of being silently rounded. The returned content is a
bounded instance projection: it is not an execution, a receipt, a signature
check, billing evidence, or proof that the host accepted the task.

## Enterprise source execution v2 (v4 integration candidate)

`EnterpriseInstanceClient` uses the same bounded authenticated HTTP transport for
the tenant-scoped `POST /v2/engine/context-execute` adapter:

```ts
import {
  EnterpriseInstanceClient,
  InstancePlanningRequest,
} from "@thinkery/leanctx-sdk";

const client = new EnterpriseInstanceClient(engineUrl, credential, tenantId);
const result = await client.contextExecuteV2(
  task,
  localNativePlan,
  new InstancePlanningRequest("task-id", "source query", 512),
  sourceIds,
  governanceRevision,
  bindingDigest,
  { planningEvaluationTime: "2026-09-20T12:34:56Z" },
);
// result.execution.execution and result.execution.receipt_document_json
```

The client sends only the declared task/plan, governed source IDs, planning
epoch, governance revision, and binding digest; source bodies, signer settings,
operator state, and tenant overrides are not caller inputs. Responses are
strictly validated through the v1 execution joins plus the v2 tenant/revision,
output, invocation, observation, and receipt-byte/digest joins. The exact
`receipt_document_json` string is preserved for independent verification; this
adapter does not verify signer trust or claim acceptance, billing, or learning.
Requests are bounded to 1 MiB, the v2 response to 4 MiB plus its wrapper, and
POSTs are not retried. HTTPS is required unless literal loopback HTTP is
explicitly enabled for local tests.

## Process containment (session starter)

The SDK starts the LeanCTX instance binary with `LEAN_CTX_SESSION_CONTAINMENT=1`.
A LeanCTX instance binary that has a session starter answers with one line on standard
output before the LeanCTX instance itself starts (`{"leanctx_session_containment":1}`).
The SDK removes that line and reports one of two states (type
`SessionContainment`):

- `"engine-starter"`: the process the SDK started is the LeanCTX instance's session
  starter. It runs the Engine in a process group of its own, ends that group
  when the Engine exits, when the SDK ends the starter or when the SDK's
  process is gone, and exits only afterwards.
- `"unavailable"`: no acknowledgement. The SDK's own process-group handling is
  all there is; Node reaps an exited Engine by itself, so what such an Engine
  left behind is not ended.

Read it from `AgentContext.sessionContainment` (final once `ready()` has
settled) and, for the one-shot client, from
`SubprocessInstanceClient.lastSessionContainment` (the most recent completed
run).

On Windows there is no starter process: a LeanCTX instance that has the mode joins a
kill-on-close job of its own before it starts anything and acknowledges with
the same line (reported as the same state); everything it started ends when
the LeanCTX instance process ends. A host job that forbids nesting leaves it
unacknowledged.

Not covered: descendants that leave their
process group (`setsid`, `setpgid`), and a graceful stop — the SDK ends the
starter, and the LeanCTX instance's group is ended right after, which can be a few
milliseconds after `cancel()`/`close()` resolved. Whether a given instance build
has the starter is told only by the reported state.

## Deprecated names (until 2.0)

SDK 1.3.0 renames the public "Engine" names to "Instance". The old names stay
as deprecated aliases until 2.0: each is the same runtime value, so `instanceof`
and `catch` keep working. The engine interface contract (wire fields such as
`engine_interface_version`, `engine_id`, `ENGINE_INTERFACE_VERSION`) is unchanged.

| Deprecated | Use |
|---|---|
| `EngineError`, `EngineUnavailable`, `EngineTimeout`, `EngineCrashed` | `InstanceError`, `InstanceUnavailable`, `InstanceTimeout`, `InstanceCrashed` |
| `EngineProtocolError`, `EngineRejected`, `EngineExecutionError` | `InstanceProtocolError`, `InstanceRejected`, `InstanceExecutionError` |
| `UnsupportedEngineError` | `UnsupportedInstanceError` |
| `EngineStatus`, `EngineClient` | `InstanceStatus`, `InstanceClient` |
| `SubprocessEngineClient`, `EnterpriseEngineClient`, `EngineContextClient` | `SubprocessInstanceClient`, `EnterpriseInstanceClient`, `InstanceContextClient` |
| `EnginePlanningRequest`, `EngineSource`, `EngineSourcePlanningClient` | `InstancePlanningRequest`, `InstanceSource`, `InstanceSourcePlanningClient` |
| `EngineSourcePlan`, `EnginePresenterGrant`, `EngineContextReadResult`, other `Engine*` types | the same name with `Instance` |
| option `engineBinary` | `instanceBinary` |
| test env `LEANCTX_ENGINE_BIN`, `LEANCTX_ENGINE_BINARY` | `LEANCTX_INSTANCE_BIN`, `LEANCTX_INSTANCE_BINARY` |

## Context Store preview (source proposal)

`readPolicyEvidence` and `readTaskLineage` read tenant/project-scoped evidence
and lineage. `parsePolicyEvidence` and `parseTaskLineage` validate the wire
documents; unmeasured evidence stays unmeasured and incomplete lineage names gaps.
Context-store constants, enumerations, functions and types are explicitly
exported from both the root and preview entry points in this source union.
The preview contract is `leanctx-context-store-preview` 0.1.0.

This source is paired with LeanCTX 3.12.2 (published); the
compatibility matrix also admits 3.11.0, 3.11.3, 3.11.4, 3.11.5, 3.12.0 and 3.12.1.
