# Migration to LeanCTX SDK 1.0

The historical private distribution `leanctx-product-sdk-local` and import
`leanctx_product_sdk` are replaced by `leanctx-sdk` and `leanctx_sdk`.

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

P5–P7 local Research imports move to the explicit Preview namespace:

```python
from leanctx_sdk.preview import ContextWorkspace, ContextCheckpoint
```

Preview persisted state is not rewritten during import migration. Unknown
schemas fail closed. Preserve state directories for a compatible reader; do
not reinterpret Workspace events as transcripts or another package format.

The Rust `lean-ctx-sdk` facade remains an Apache Engine embedding mechanism and
is not the Python `leanctx-sdk` distribution.
