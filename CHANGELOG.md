# Changelog

## 1.0.0

### Stable

- Five Product primitives: `ContextSession`, `ContextSource`, `ContextView`,
  `ContextPlan`, and `ContextReceipt`.
- Select → Shape → Reuse → Recover lifecycle.
- Exact Engine protocol compatibility, typed errors, receipts, recovery, and
  provider-free OpenAI Agents 0.8.4 reference integration.

### Preview

- Explicit `leanctx_sdk.preview` namespace.
- Local Workspace, Checkpoint, Delta, Handoff, fork, and policy-inheritance
  evaluation APIs. Engine-dependent package lifecycle helpers remain Internal
  until a compatible public Engine release is bound.

### Distribution

- Distribution `leanctx-sdk`, import `leanctx_sdk`, version `1.0.0`.
- Perpetual source-available SDK license with no automatic open-source date;
  commercial Production Use requires a Thinkery AG agreement.
- Cloud Receipt Board, Governed Optimization, and private research evidence
  are excluded.
