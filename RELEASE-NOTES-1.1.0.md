# LeanCTX SDK 1.1.0

SDK 1.1 lets Python applications build custom coding agents on LeanCTX's local
context tools while retaining ownership of the model and agent loop.

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

## Compatibility

The five SDK 1.0 lifecycle primitives and Engine Interface v1 are unchanged.
Agent Tools requires LeanCTX Engine 3.10.1 with interface `1.0.0`, schema `1`,
and transport `1`.

The Engine 3.10.1 artifacts and companion Python packages must be published
before the `[agent]` installation extra is generally available. Until then,
use a source-built compatible Engine through `engine_binary=`.
