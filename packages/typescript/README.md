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
