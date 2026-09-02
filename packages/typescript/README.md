# @thinkery-ag/leanctx-sdk

LeanCTX SDK 1.1 for Node.js and TypeScript. The package exposes the five
stable lifecycle values (`ContextSession`, `ContextSource`, `ContextView`,
`ContextPlan`, and `ContextReceipt`) plus the host-owned `AgentContext` and
`AsyncAgentContext` tool clients.

The package has no runtime dependencies. Engine subprocesses are always started
with `shell: false`, bounded request/response streams, secure temporary files,
strict v1 JSON validation, and explicit process-tree termination. Write and
execute capabilities require immutable, explicit permissions and allowlists.

```ts
import { AgentContext } from "@thinkery-ag/leanctx-sdk";

const tools = await AgentContext.open(".", { task: "Inspect the API" });
try {
  const result = await tools.search("ContextSession", { path: "src" });
  console.log(result.text);
} finally {
  await tools.close();
}
```

Agent Tools requires the not-yet-published LeanCTX Engine 3.10.1. The package
must not be published until that signed Engine release and Python SDK 1.1 are
available. The five Product primitives remain independently compatible with
Engine Interface v1 and provider-independent.
