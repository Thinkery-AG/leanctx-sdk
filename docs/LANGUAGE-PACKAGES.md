# Language package releases

Python, TypeScript, Go, Rust, JVM, and .NET live in one repository and consume
the same contracts, fixtures, version, Engine compatibility declaration, and
cross-SDK acceptance gate.

## Engine configuration

All six `AgentContext` launchers preserve the host application's explicit
`LEAN_CTX_CONFIG_DIR`, `LEAN_CTX_DATA_DIR`, `LEAN_CTX_STATE_DIR`,
`LEAN_CTX_CACHE_DIR` and `DO_NOT_TRACK` settings. Configure these before opening
a context when using a custom Engine installation or data directory. The saved
Engine configuration supplies the verified private runtime and license binding;
the SDK does not receive a license secret directly.

This exact allowlist applies only to the Engine process. Arbitrary environment
variables, provider keys and loader overrides remain excluded. Model-requested
shell commands retain their separate execution permission and environment policy.

### Selected GitLab source

An optional `GitLabSource` binds one Agent Tools session to a canonical HTTPS
host, positive project ID and namespace. The host application also selects the
absolute `glab` executable and, optionally, its absolute configuration directory.
These are trusted operator settings, like `engine_binary`: never take them from
model-generated tool arguments or project documents. The Engine reads the
existing glab credential through a bounded in-memory channel. Tokens are not
SDK arguments, policy fields or forwarded environment variables.

The policy-file extension is `selected_gitlab` with `host`, `project`,
`namespace`, `glab` and optional `config_dir`. It is omitted entirely when no
source is selected. The existing schema and local capability set remain
unchanged for those sessions; selected sessions additionally negotiate
`ctx_provider`. Older Engines reject the unsupported policy extension instead
of opening an unbound source session.

```python
from leanctx_sdk import AgentContext, GitLabSource

source = GitLabSource(
    host="gitlab.example.com", project=42, namespace="team/project",
    glab="/usr/local/bin/glab",
)
with AgentContext("/workspace/project", gitlab_source=source) as context:
    snapshot = context.call("ctx_provider", {
        "action": "query", "provider": "gitlab", "resource": "merge_requests",
        "project": "42", "mode": "snapshot", "limit": 10,
    })
```

Selected sessions require an active content policy admitting `ctx_provider`.
The Engine binds the reviewed policy and configuration at startup and rechecks
that authority before operations and before releasing output. Changed, missing
or invalid protection does not fall back to an unprotected session; deliberate
policy updates require a new selected-source session. Ordinary local sessions
retain their existing dynamic policy behavior.
Only `query` snapshots of `issues`, `merge_requests` and `pipelines` are exposed,
with an explicit matching project and limit from 1 to 100. Optional filters are
`state` and `query`. Other actions, providers and parameters are refused. The
existing provider rechecks remote project identity and authorization, and the
snapshot digest is verified after output protection. A digest is not an
upstream signature or proof that application code later sent data to a model.

Source credentials and executables must be outside the selected project.
The credential transport currently supports Unix; Windows source startup fails
closed. This does not change platform availability of ordinary local SDK tools.
Language-package execution and platform qualification are separate release
gates. Reconnection preserves source selection and repeats credential startup.

## Package identities

| Language | Registry identity | Release tag |
| --- | --- | --- |
| Python | `thinkery-leanctx-sdk` | `vX.Y.Z` |
| TypeScript | `@thinkery/leanctx-sdk` | `packages/typescript/vX.Y.Z` |
| Go | `github.com/Thinkery-AG/leanctx-sdk/packages/go` | `packages/go/vX.Y.Z` |
| Rust | `thinkery-leanctx-sdk` | `packages/rust/vX.Y.Z` |
| JVM | `com.leanctx:leanctx-sdk` | `packages/jvm/vX.Y.Z` |
| .NET | `Thinkery.LeanCtx` | `packages/dotnet/vX.Y.Z` |

Go requires no upload: the scoped Git tag is the module release. The other
language tags publish only after all five non-Python SDK build, test, package,
and clean-install jobs pass.

The public SDK 1.1.0 release requires Engine 3.10.1; current `main` sources
target Engine 3.11.1. Verify each registry artifact before describing it as
published; source package version metadata alone is not publication evidence.

## Registry trust

Configure protected GitHub environments named `npm`, `crates-io`,
`maven-central`, and `nuget`. Require reviewer approval for first releases.

- npm: configure `Thinkery-AG/leanctx-sdk` and
  `.github/workflows/language-packages.yml` as the package's trusted
  publisher. No long-lived npm token is used.
- crates.io: add `CARGO_REGISTRY_TOKEN` to the `crates-io` environment.
- Maven Central: add `MAVEN_CENTRAL_USERNAME`, `MAVEN_CENTRAL_TOKEN`,
  `MAVEN_GPG_PRIVATE_KEY`, and `MAVEN_GPG_PASSPHRASE`.
- NuGet: add `NUGET_API_KEY` scoped to `Thinkery.LeanCtx`.

Never publish from a developer machine. Tags must point at an accepted main
commit, and the version-contract job rejects a tag whose version differs from
any package.

## First publication

Create all five language tags on the same accepted commit. Observe the
`Language package release` workflow to completion, then verify installation
from each public registry. Do not describe a package as published until that
registry lookup and clean consumer install succeed.
