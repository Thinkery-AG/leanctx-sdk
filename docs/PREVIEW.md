# Preview local-context APIs

Preview APIs are usable for evaluation and pilots but may change in minor
releases. They are not covered by the Stable v1 compatibility guarantee.

```python
from leanctx_sdk.preview import (
    ContextCheckpoint,
    ContextDelta,
    ContextFork,
    ContextHandoff,
    ContextWorkspace,
)
```

The aliases bind to the current versioned schemas:

- `ContextCheckpoint` → `ContextCheckpointV2`
- `ContextDelta` → `ContextDeltaV1`
- `ContextFork` → `WorkspaceForkV1`
- `ContextHandoff` → `ContextHandoffV1`

The Preview surface supports durable local Workspace state, checkpoint/restore,
fork lineage, bounded delta and handoff admission, and policy inheritance. It
does not persist raw transcripts, provider credentials, or model state, and it
does not require Cloud.

Checkpoint package pin/install/seed/seal helpers remain INTERNAL until a
supported public Engine release contains the required protocol extension.
