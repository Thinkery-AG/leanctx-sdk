# Quickstart

Install `leanctx-sdk==1.0.0` and a supported LeanCTX Engine artifact from the
compatibility matrix. Provider credentials are not required for deterministic
local use.

```python
from pathlib import Path

from leanctx_sdk import ContextSession, ContextSource, SubprocessEngineClient

root = Path.cwd()
session = ContextSession(
    "inspect configuration",
    project_root=root,
    engine=SubprocessEngineClient(),  # uses the compatible `lean-ctx` on PATH
)
source = ContextSource("README.md", project_root=root)
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
print(plan.plan_id, receipt.receipt_id)
```

Replace the deterministic host step with your framework or model call. LeanCTX
never owns that loop, its tools, retries, scheduling, or returned object.

Run `lean-ctx --version` first and compare it with `COMPATIBILITY.md`. The
example expects a project `README.md`; replace that path with any project file.
