# Quickstart

Install `leanctx-sdk==1.0.0` and the exact LeanCTX Engine `v3.10.0` artifact
listed in the compatibility matrix. Verify its signed checksum before putting
`lean-ctx` on `PATH`. Provider credentials are not required for deterministic
local use.

```python
from pathlib import Path

from leanctx_sdk import ContextSession, ContextSource, SubprocessEngineClient

root = Path.cwd()
session = ContextSession(
    "inspect configuration",
    project_root=str(root),
    engine=SubprocessEngineClient(),  # uses the compatible `lean-ctx` on PATH
)
source = ContextSource("README.md", project_root=str(root))
view = session.prepare(source)
plan = session.current_plan
assert plan is not None

try:
    result = {"selected_context": view.require_text()}
except BaseException as error:
    session.abort(error)
    raise

receipt = session.complete(result, outcome="completed")
recovered = session.recover(view)
assert recovered.source_digest == view.source_digest
print(plan.plan_id, receipt.receipt_link.receipt_id)
```

Replace the deterministic host step with your framework or model call. LeanCTX
never owns that loop, its tools, retries, scheduling, or returned object.

Run `lean-ctx --version` first and compare it with `COMPATIBILITY.md`. The
example expects a project `README.md`; replace that path with any project file.
