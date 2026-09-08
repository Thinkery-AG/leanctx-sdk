# Language package releases

Python, TypeScript, Go, Rust, JVM, and .NET live in one repository and consume
the same contracts, fixtures, version, Engine compatibility declaration, and
cross-SDK acceptance gate.

## Package identities

| Language | Registry identity | Release tag |
| --- | --- | --- |
| Python | `thinkery-leanctx-sdk` | `vX.Y.Z` |
| TypeScript | `@thinkery/leanctx-sdk` | `packages/typescript/vX.Y.Z` |
| Go | `github.com/Thinkery-AG/leanctx-sdk/packages/go` | `packages/go/vX.Y.Z` |
| Rust | `thinkery-leanctx-sdk` | `packages/rust/vX.Y.Z` |
| JVM | `com.thinkery.leanctx:leanctx-sdk` | `packages/jvm/vX.Y.Z` |
| .NET | `Thinkery.LeanCtx` | `packages/dotnet/vX.Y.Z` |

Go requires no upload: the scoped Git tag is the module release. The other
language tags publish only after all five non-Python SDK build, test, package,
and clean-install jobs pass.

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
