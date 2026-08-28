# Integration modes

## Attach

Use existing Engine CLI, MCP, hooks, and local integrations directly. This is
an Engine surface, not an SDK abstraction.

## Embed

Use `ContextSession` with `SubprocessEngineClient`. The host keeps its native
model and result objects while the SDK owns context lifecycle and evidence.
This is the Stable SDK path.

## Wrap

`leanctx_sdk.integrations.openai_agents` supports the exact certified
`openai-agents==0.8.4` provider-free reference path on Python 3.10+. Native
objects and success/exception semantics remain host-owned. No other framework
or Agents version is claimed.

## Preview local workspace

`leanctx_sdk.preview` exposes local Workspace, Checkpoint, Fork, Delta, and
Handoff evaluation APIs. Cloud is not required and is not a public Product.
