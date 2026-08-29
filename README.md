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

Install the supported public Engine `v3.10.0` artifact described in
[COMPATIBILITY.md](COMPATIBILITY.md) before running the SDK.

## 5-minute Quickstart

```python
from pathlib import Path

from leanctx_sdk import ContextSession, ContextSource, SubprocessEngineClient

root = Path.cwd()
engine = SubprocessEngineClient()  # uses the compatible `lean-ctx` on PATH
session = ContextSession("inspect the configuration", project_root=str(root), engine=engine)
source = ContextSource("README.md", project_root=str(root))

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

Local Workspace, Checkpoint, Delta, and Handoff APIs are available from
`leanctx_sdk.preview`. Preview APIs may change in minor releases and are not
covered by the Stable v1 compatibility guarantee.

The same namespace contains the narrow Engine-backed `.ctxpkg` seal, seed, and
SnapshotV1 migration operations.

```python
from leanctx_sdk.preview import ContextCheckpoint, ContextWorkspace
```

See [docs/PREVIEW.md](docs/PREVIEW.md). P8 Receipt Board, P9 Governed
Optimization, Cloud, and production AutoTune are private Research and are not
included.

Run the provider-free lifecycle example with
`python examples/preview_workspace.py` from a source checkout.

## Engine

LeanCTX Engine is Apache-2.0, independently useful, and remains a separate
product. The dependency is one-way: SDK → Engine. See
[COMPATIBILITY.md](COMPATIBILITY.md) for the exact supported public
[`v3.10.0` release](https://github.com/yvgude/lean-ctx/releases/tag/v3.10.0).

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Stable SDK v1](docs/STABLE-SDK-V1.md)
- [Integration modes](docs/INTEGRATION-MODES.md)
- [Preview APIs](docs/PREVIEW.md)
- [Receipts and evidence](docs/RECEIPTS-AND-EVIDENCE.md)
- [Recovery](docs/RECOVERY.md)
- [Release notes](RELEASE-NOTES-1.0.0.md)
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
