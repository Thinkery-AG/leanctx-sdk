# Context Store preview — task lineage and policy evidence

> **Preview.** The contract `leanctx-context-store-preview` 0.1 may change in
> minor releases. It requires an Engine that implements `engine
> context-lineage` and `engine context-policy-evidence` (v4 Engine,
> Experimental contract `engine-context-store-v1`).

The local LeanCTX Engine keeps what it decided and why: which context it
planned for a task, what actually reached the model under which policy, and
which outcome followed. Two read-only calls expose it, always within one
tenant/project scope:

- **Task lineage** — one task's ledger steps (plan, delivery, model call,
  outcome) joined with its Decision Receipts. Every missing link is named in
  `gaps`; a lineage without gaps is complete. Nothing is inferred to fill a
  gap.
- **Policy evidence** — per workload, read strategy and UTC day: how many
  tasks were accepted or rejected, how many tokens were saved, whether
  quality, security and runtime friction (re-reads, expansions, failed edits)
  were measured, and which strategy evaluations exist.

Neither carries source text, prompts or credentials. **Unmeasured is never
zero**: an unmeasured quality or security section has no counts at all.

```python
from leanctx_sdk import SubprocessEngineClient
from leanctx_sdk.preview import read_policy_evidence, read_task_lineage

engine = SubprocessEngineClient("/path/to/lean-ctx")
lineage = read_task_lineage(engine, "/work/project", "task-42", project_id="acme")
if not lineage.is_complete:
    print("missing:", lineage.gaps)

evidence = read_policy_evidence(engine, "/work/project", project_id="acme")
for record in evidence.records:
    if record.security.measured:
        print(record.strategy, record.workload.size, record.security.regressions)
```

Without `project_id`, the Engine uses the project root, the same default as
`lean-ctx autopilot`. Strategy evaluations are suite results of the machine,
attached to every scope's evidence.

| Language | Calls |
|---|---|
| Python | `leanctx_sdk.preview.read_policy_evidence`, `read_task_lineage` |
| TypeScript | `@thinkery/leanctx-sdk/preview`: `readPolicyEvidence`, `readTaskLineage` |
| Go | `SubprocessEngineClient.ReadPolicyEvidence`, `ReadTaskLineage` |
| Rust | `SubprocessEngineClient::read_policy_evidence`, `read_task_lineage` |
| Java/Kotlin | `ContextStorePreview.readPolicyEvidence`, `readTaskLineage` |
| .NET | `ContextStorePreview.ReadPolicyEvidenceAsync`, `ReadTaskLineageAsync` |

Parsing is strict in every language and mirrors the Engine's validation;
`contracts/context-store-preview-v1.json` lists the invariants, and
`fixtures/context-store-preview-v1` is the shared conformance set.
