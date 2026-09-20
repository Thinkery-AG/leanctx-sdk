# @thinkery/leanctx-sdk

LeanCTX SDK 1.1 for Node.js and TypeScript. The package exposes the five
stable lifecycle values (`ContextSession`, `ContextSource`, `ContextView`,
`ContextPlan`, and `ContextReceipt`) plus the host-owned `AgentContext` and
`AsyncAgentContext` tool clients.

The package has no runtime dependencies. Engine subprocesses are always started
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

Agent Tools requires the published LeanCTX Engine 3.10.1. Registry releases are
produced from the same commit and cross-SDK release gate as every other LeanCTX
SDK. The five Product primitives remain independently compatible with Engine
Interface v1 and provider-independent.

## Guarded Engine context reads (v4 integration candidate)

`EngineContextClient` uses the authenticated `/v1/tools/call` Engine boundary;
it does not start an Engine or grant access outside the host's project root.

```ts
import { EngineContextClient } from "@thinkery/leanctx-sdk";

const client = new EngineContextClient(engineUrl, engineCredential);
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
published 3.10.1 Engine supports this boundary.

## Explicit Engine source planning and materialization

`EngineSourcePlanningClient` is the additive local CLI adapter for
`context-plan-sources` and `context-materialize-sources`:

```ts
import {
  EnginePlanningRequest,
  EngineSource,
  EngineSourcePlanningClient,
  SubprocessEngineClient,
} from "@thinkery/leanctx-sdk";

const client = new EngineSourcePlanningClient(new SubprocessEngineClient({ engineBinary: "lean-ctx" }));
const request = new EnginePlanningRequest("invoice-task", "invoice ledger", 512);
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
provenance. Materialized token count is a bounded Engine report; this client does
not treat it as billing, compression savings, execution, acceptance, or a receipt.
The existing `EngineClient` context-view/recover interface is unchanged.

## Enterprise source planning (v4 integration candidate)

The authenticated `EnterpriseEngineClient.contextPlanSources(request, sourceIds)`
calls `POST /v1/engine/context-plan` for the configured tenant. It accepts
governed source IDs, not source bodies or operator settings:

```ts
import { EnterpriseEngineClient, EnginePlanningRequest } from "@thinkery/leanctx-sdk";

const enterprise = new EnterpriseEngineClient(engineUrl, credential, tenantId);
const planned = await enterprise.contextPlanSources(
  new EnginePlanningRequest("task-id", "source query", 512),
  sourceIds,
);
// planned.tenant_id, planned.governance_revision, planned.plan
```

The response must bind the tenant, request task/budget, permitted selections,
requested source IDs and canonical projection/binding digests. This reuses the
local source-planning validator and the existing guarded HTTP transport; it
does not execute a plan, acquire a source, or grant access to a foreign tenant.
HTTP materialization is not yet exposed by this TypeScript adapter.

## Enterprise source execution v2 (v4 integration candidate)

`EnterpriseEngineClient` uses the same bounded authenticated HTTP transport for
the tenant-scoped `POST /v2/engine/context-execute` adapter:

```ts
import {
  EnterpriseEngineClient,
  EnginePlanningRequest,
} from "@thinkery/leanctx-sdk";

const client = new EnterpriseEngineClient(engineUrl, credential, tenantId);
const result = await client.contextExecuteV2(
  task,
  localNativePlan,
  new EnginePlanningRequest("task-id", "source query", 512),
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
