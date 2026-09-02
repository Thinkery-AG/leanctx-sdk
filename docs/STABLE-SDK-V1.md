# Stable SDK v1

Stable v1 contains exactly five Product primitives:

- `ContextSession`: lifecycle owner and terminal state machine.
- `ContextSource`: admitted local source identity.
- `ContextPlan`: deterministic preparation intent.
- `ContextView`: shaped context plus recovery binding.
- `ContextReceipt`: factual completion and evidence projection.

The lifecycle is **Select → Shape → Reuse → Recover**. Public serialization is
versioned, unknown protocol fields fail closed, projections are detached JSON
dictionaries, and exact recovery remains bound to the admitted source digest.

Stable supporting symbols—errors, Engine clients, protocol records, and
constants—are enumerated in `PUBLIC-SURFACE-MANIFEST.md` and follow the same
SemVer policy unless explicitly marked INTERNAL.

SDK 1.1 adds a separate Stable Agent Tools contract: `AgentContext` and
`AsyncAgentContext` expose negotiated local read/search/edit/execute tools for
host-owned agent loops. This additive contract does not change the five
Product primitives or make the SDK responsible for models, planning, retries,
or autonomous orchestration.
