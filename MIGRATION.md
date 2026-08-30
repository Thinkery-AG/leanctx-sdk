# Migration to LeanCTX SDK 1.0

## Add Agent Tools with SDK 1.1

Existing 1.0 lifecycle code remains valid. Use `AgentContext` only when the
host needs reusable coding-agent tools:

```python
from leanctx_sdk import AgentContext

with AgentContext(".", task="inspect the project") as tools:
    result = tools.read("README.md", mode="signatures")
```

Do not translate an existing `ContextSession` into `AgentContext`: they solve
different problems and can coexist. The former owns Select → Shape → Reuse →
Recover evidence; the latter owns a project-jailed read/search/edit/execute
tool session. Model choice, planning, retries, and the agent loop stay with the
host.

The historical private distribution `leanctx-product-sdk-local` and import
`leanctx_product_sdk` are replaced by `thinkery-leanctx-sdk` and `leanctx_sdk`.

```python
# old private preview
from leanctx_product_sdk import ContextSession

# SDK 1.0
from leanctx_sdk import ContextSession
```

The Stable lifecycle maps as follows:

| Historical responsibility | SDK 1.0 |
| --- | --- |
| source selection | `ContextSource` |
| context shaping request | `ContextPlan` through `ContextSession.prepare` |
| shaped context reuse | `ContextView.require_text()` |
| factual completion evidence | `ContextReceipt` |
| exact source recovery | `ContextSession.recover()` |

Historical `ctx_*` Engine/MCP tools remain Engine surfaces; they are not
deleted or silently redirected. Migrate one host entry point at a time, keep
the model/tool/retry loop host-owned, abort and re-raise original exceptions,
and retain a reversible host feature flag until receipt and recovery checks
pass.

Local-context Research imports move to the explicit Preview namespace:

```python
from leanctx_sdk.preview import ContextWorkspace, ContextCheckpoint
```

Preview persisted state is not rewritten during import migration. Unknown
schemas fail closed. Preserve state directories for a compatible reader; do
not reinterpret Workspace events as transcripts or another package format.

The Rust `lean-ctx-sdk` facade remains an Apache Engine embedding mechanism and
is not the Python `thinkery-leanctx-sdk` distribution.
