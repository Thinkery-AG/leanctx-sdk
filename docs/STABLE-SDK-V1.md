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
