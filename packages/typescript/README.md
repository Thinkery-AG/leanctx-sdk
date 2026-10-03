# @thinkery/leanctx-sdk

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

This Node.js and TypeScript SDK connects your host-owned model and workflow to
a local LeanCTX Engine. Your application keeps its model, agent loop, and UI.
It exposes the five Stable lifecycle primitives plus the separate Stable
`AgentContext` and `AsyncAgentContext` tool clients.

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

The published SDK 1.1.1 release requires Engine 3.10.1; current `main` sources
target Engine 3.10.5. Check the release-specific compatibility record before
choosing an Engine binary. The SDK source license permits non-production use;
production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG.
