# LeanCTX Go SDK

The Go module `github.com/Thinkery-AG/leanctx-sdk/packages/go` implements LeanCTX SDK
1.1.0. It provides the five stable Product primitives, a strict Engine
Interface v1 subprocess adapter, and the persistent Agent Tools 1.1 client.

The module is released from this monorepo with the Go-standard
`packages/go/vX.Y.Z` tag. Subprocess operation requires the published LeanCTX
Engine 3.10.1.

```go
import leanctx "github.com/Thinkery-AG/leanctx-sdk/packages/go"
```

The package is source-available under the accompanying `LICENSE`. Runtime
dependencies are limited to the Go standard library.

## Unreleased v4 integration

`NewEnterpriseEngineClient` adds authenticated standalone Engine source planning
through `ContextPlanSources` and its cancellable context variant. The configured
tenant is an expected response binding, never an authorization override. Requests
contain source IDs, not source bodies; the server remains the policy and planning
authority. HTTPS is the default; literal loopback HTTP requires the explicit
test option. Redirects, environment proxies and automatic POST retries are off.

This supporting adapter is not part of published 1.1.0 and does not change the
existing `EngineClient` interface. Planning is not provider dispatch, a signed
receipt, materialization or task acceptance. See
[Engine planning](../../docs/ENGINE-PLANNING.md) for the canonical boundary;
other-language and installed-release parity remain separate acceptance work.
