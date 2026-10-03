# LeanCTX JVM SDK

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

This Java 21 and Kotlin SDK connects your host-owned model and workflow to a
local LeanCTX Engine. Your application keeps its model, agent loop, and UI. It
provides the five Stable lifecycle primitives plus the separate Stable Agent
Tools API.

```java
try (AgentContext tools = AgentContext.open(projectRoot)) {
    ToolResult result = tools.read("README.md", ReadMode.AUTO, false);
    System.out.println(result.text());
}
```

Production code has no runtime dependencies. Engine processes use structured
arguments, bounded streams, project-root containment, strict JSON validation,
secure temporary policy files, and fail-closed process termination.

The published SDK 1.1.1 release requires Engine 3.10.1; current `main` sources
target Engine 3.10.5. Check the release-specific compatibility record before
choosing an Engine binary. The SDK source license permits non-production use;
production use, OEM embedding, and commercial redistribution require a separate
written agreement signed by Thinkery AG.
