# Receipts and evidence

`ContextReceipt` records factual lifecycle identity, plan/view linkage, host
outcome, integrity state, bounded usage, degradations, failures, and evidence
references. It does not claim model quality, guaranteed savings, or provider
truth beyond supplied verified evidence.

Use `receipt.to_dict()` for a detached JSON projection. Use
`receipt.require_verified()` when an unsealed receipt must fail closed at the
host boundary. Never treat a receipt as a transcript or secret store.
