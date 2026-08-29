# LeanCTX SDK 1.0.0 release summary

The normative release notes, status boundaries, and exclusions are in
[`RELEASE-NOTES-1.0.0.md`](../RELEASE-NOTES-1.0.0.md).

LeanCTX gives an agent a local, evidence-backed context lifecycle while leaving
the model loop and framework under host control.

Stable: `ContextSession`, `ContextSource`, `ContextView`, `ContextPlan`, and
`ContextReceipt`; Select → Shape → Reuse → Recover; exact Engine compatibility;
OpenAI Agents 0.8.4 reference integration; receipts and recovery.

Preview: local Workspace, Checkpoint, Fork, Delta, Handoff, and narrow `.ctxpkg`
lifecycle APIs under `leanctx_sdk.preview`. Preview may change before Stable
promotion.

Not shipped: Cloud, P8 Receipt Board, P9 Governed Optimization, production AutoTune,
organization-wide shared context, or a multi-agent scheduler.

The SDK is source available. Commercial Production Use requires a separate
written Thinkery AG agreement.
