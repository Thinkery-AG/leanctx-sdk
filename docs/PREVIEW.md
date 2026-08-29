# Preview local-context APIs

Preview APIs are usable for evaluation and pilots but may change in minor
releases. They are not covered by the Stable v1 compatibility guarantee.

```python
from leanctx_sdk.preview import (
    ContextCheckpoint,
    ContextDelta,
    ContextHandoff,
    ContextWorkspace,
)
```

The aliases bind to the current versioned schemas:

- `ContextCheckpoint` → `ContextCheckpointV2`
- `ContextDelta` → `ContextDeltaV1`
- `ContextHandoff` → `ContextHandoffV1`

The Preview surface supports durable local Workspace state, checkpoint/restore,
fork lineage through `ContextWorkspace.fork`, bounded delta and handoff
admission, and policy inheritance. It does not persist raw transcripts,
provider credentials, or model state, and it does not require Cloud.

The Preview namespace also exposes the narrow `.ctxpkg` path:
`seal_checkpoint_package`, `seed_workspace_from_package`, and
`migrate_snapshot_v1`. These operations require the supported public Engine
release declared in `COMPATIBILITY.md`; signature, compatibility, and policy
checks fail closed.

Run the provider-free lifecycle smoke from a repository checkout:

```bash
PYTHONPATH=src python examples/preview_workspace.py
```

Success produces no output. The smoke creates and reopens a workspace, restores
a checkpoint, forks it, computes a bounded delta, and validates handoff
admission without Cloud, credentials, or a model provider.
