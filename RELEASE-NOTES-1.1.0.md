# LeanCTX SDK 1.1.0

SDK 1.1 lets Python, TypeScript, Go, Rust, JVM, and .NET applications build
custom coding agents on LeanCTX's local context tools while retaining ownership
of the model and agent loop.

## Added

- Stable synchronous `AgentContext` and asynchronous `AsyncAgentContext`.
- Read, search, glob, tree, compose, and symbol tools.
- Explicitly gated create, patch, replace, and command execution tools.
- Persistent Engine session, shared cache, and per-call plus aggregate token
  measurements.
- Structured argv execution with Engine-side executable, environment, timeout,
  and working-directory enforcement.
- Explicit cancellation, fresh-process reconnect, and preserved non-text MCP
  content blocks.
- Versioned Agent Tools Interface negotiation and typed fail-closed errors.
- Optional OpenAI Agents 0.8.4 function-tool adapter.
- Custom-agent guide, security boundary, migration guidance, and runnable
  framework-neutral example.
- Provider-free real-Engine verification plus a deterministic raw-vs-LeanCTX
  retrieval benchmark with matched-answer and minimum-savings gates.
- Language-native 1.1 preview packages under `packages/` for TypeScript/Node,
  Go, Rust, Java/Kotlin, and .NET, sharing frozen wire fingerprints and the PR #8
  Agent Tools contract.

## Compatibility

The five SDK 1.0 lifecycle primitives and Engine Interface v1 are unchanged.
Agent Tools requires LeanCTX Engine 3.10.1 with interface `1.0.0`, schema `1`,
and transport `1`.

The Engine 3.10.1 artifacts and companion packages must be published before any
1.1 SDK is generally available. Until then, use a source-built compatible
Engine through the language-specific binary-path option.
