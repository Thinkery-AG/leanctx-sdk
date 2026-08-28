# LeanCTX SDK

The Context SDK for AI Agents.

Your framework runs the agent. LeanCTX manages its context path.

**Select → Shape → Reuse → Recover.**

## Install

The final distribution is `leanctx-sdk` and the import namespace is
`leanctx_sdk`:

```bash
python -m pip install leanctx-sdk==1.0.0
```

Public installation remains disabled until the exact release candidate,
license, repository, and PyPI identity receive final authorization.

## 5-minute Quickstart

```python
from pathlib import Path

from leanctx_sdk import ContextSession, ContextSource, SubprocessEngineClient

root = Path.cwd()
engine = SubprocessEngineClient("/absolute/path/to/lean-ctx")
session = ContextSession("inspect the configuration", project_root=root, engine=engine)
source = ContextSource("src/config.py", project_root=root)

view = session.prepare(source)
plan = session.current_plan
assert plan is not None

try:
    host_result = {"selected_context": view.require_text()}
except BaseException as error:
    session.abort(error)
    raise

receipt = session.complete(host_result, outcome="completed")
recovered = session.recover(view)

assert recovered.source_digest == view.source_digest
print(plan.plan_id, receipt.receipt_id)
```

The host owns prompts, models, tools, retries, scheduling, and result objects.
The SDK owns the context lifecycle, identity, bounded degradation, receipts,
and exact recovery. Deterministic local use requires no provider credential or
Cloud service.

## Stable v1

The only Stable Product primitives are:

- `ContextSession`
- `ContextSource`
- `ContextView`
- `ContextPlan`
- `ContextReceipt`

Stable supporting errors, Engine clients, protocol records, and constants are
listed in [PUBLIC-SURFACE-MANIFEST.md](PUBLIC-SURFACE-MANIFEST.md). Stable APIs
follow SemVer; see [docs/SEMVER-AND-DEPRECATION.md](docs/SEMVER-AND-DEPRECATION.md).

## Preview

Local Workspace, Checkpoint, Delta, Fork, and Handoff APIs are available from
`leanctx_sdk.preview`. Preview APIs may change in minor releases and are not
covered by the Stable v1 compatibility guarantee.

```python
from leanctx_sdk.preview import ContextCheckpoint, ContextFork, ContextWorkspace
```

See [docs/PREVIEW.md](docs/PREVIEW.md). P8 Receipt Board, P9 Governed
Optimization, Cloud, and production AutoTune are private Research and are not
included.

## Engine

LeanCTX Engine is Apache-2.0, independently useful, and remains a separate
product. The dependency is one-way: SDK → Engine. See
[COMPATIBILITY.md](COMPATIBILITY.md) before selecting an Engine artifact.

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Stable SDK v1](docs/STABLE-SDK-V1.md)
- [Integration modes](docs/INTEGRATION-MODES.md)
- [Preview APIs](docs/PREVIEW.md)
- [Receipts and evidence](docs/RECEIPTS-AND-EVIDENCE.md)
- [Recovery](docs/RECOVERY.md)
- [Compatibility](COMPATIBILITY.md)
- [Errors and troubleshooting](docs/ERRORS.md)
- [Migration](MIGRATION.md)
- [Security](SECURITY.md)
- [Licensing](LICENSING.md)

## License

LeanCTX SDK is source-available, not open source. Development, testing, CI,
staging, evaluation, and proofs of concept are permitted under the included
license. Commercial Production Use, OEM embedding, and commercial
redistribution require a separate written agreement with Thinkery AG.

LeanCTX Engine remains an Apache-2.0 open-source product. There is no automatic
future open-source conversion for the SDK.
