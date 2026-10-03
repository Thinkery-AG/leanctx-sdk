# Decision record: Context Store preview (2026-10-03)

Intake required by `POST-V1-RESEARCH-FREEZE.md` for new public surface.

1. **User and problem.** The product owner's v4 master prompt
   ("LeanCTX V4 Context Store & Context Intelligence", Phase 12) requires the
   Engine's decision lineage and read-strategy evidence to be reachable from
   the SDKs, so hosts that embed the Engine without MCP can audit what reached
   the model and why, and see whether a learned policy is backed by evidence.
2. **Why existing surfaces do not cover it.** Stable SDK v1 exposes
   `context-view` and `recover` only; the gateway preview exposes egress
   admission. Neither reads the execution ledger, Decision Receipts or policy
   evidence.
3. **Outcome and criteria.** Six SDKs read both Engine operations with strict
   parsing; success = the shared fixture set (2 + 2 valid, 21 + 15 invalid
   documents) passes in every language and the live journey passes against a
   real Engine. Failure = any SDK accepts an invalid document or reports
   unmeasured evidence as measured.
4. **Classification.** Public, preview. Read-only, local process boundary, no
   network, content-free (counts, digests, identifiers). No licensing or
   pricing logic; the licensed learner stays outside the SDK.
5. **Budget and stop condition.** One preview iteration, additive only; the
   stable root surfaces stay unchanged. Stop if the Engine contract changes
   before promotion; the preview then follows it or is withdrawn.
6. **Owner.** Product owner (Yves Gugger). Promotion to Stable only together
   with the Engine release that promotes `engine-context-store-v1` from
   Experimental.
