# LeanCTX Go SDK

The Go module `github.com/Thinkery-AG/leanctx-sdk/packages/go` implements LeanCTX SDK
1.1.0. It provides the five stable Product primitives, a strict Engine
Interface v1 subprocess adapter, and the persistent Agent Tools 1.1 client.

The module is released from this monorepo with the Go-standard
`packages/go/vX.Y.Z` tag. This source candidate requires exactly LeanCTX Engine 4.0.0 for Agent Tools.
The local pairing does not certify a published Engine or SDK release.

```go
import leanctx "github.com/Thinkery-AG/leanctx-sdk/packages/go"
```

The package is source-available under the accompanying `LICENSE`. Runtime
dependencies are limited to the Go standard library.

## Unreleased v4 integration

`NewEnterpriseEngineClient` adds authenticated standalone Engine source planning
through `ContextPlanSources` and its cancellable context variant. Source
materialization uses `ContextMaterializeSources` and its context variant with an
`EngineSourceMaterializationRequest` bound to the planning result's governance
revision, binding digest and optional evaluation time. The configured
tenant is an expected response binding, never an authorization override. Requests
contain source IDs, not source bodies; the server remains the policy and planning
authority. HTTPS is the default; literal loopback HTTP requires the explicit
test option. Redirects, environment proxies and automatic POST retries are off.

This supporting adapter is not part of published 1.1.0 and does not change the
existing `EngineClient` interface. Planning is not provider dispatch, a signed
receipt or task acceptance; materialization returns digest-checked context only. See
[Engine planning](../../docs/ENGINE-PLANNING.md) for the canonical boundary;
other-language and installed-release parity remain separate acceptance work.

`ContextExecuteV2` and its context variant consume a declared local-native source
execution through `EngineSourceExecutionV2Request`. They return the exact v2
receipt-document string with checked byte-digest and lineage bindings. The SDK
does not verify signer trust, declare task acceptance, execute a model provider,
or retry an execution request automatically.

`ProviderExecute`/`ProviderExecuteContext` send a concrete non-local plan through
the separately governed provider boundary. Results retain nullable usage and
cost provenance; unavailable values are never synthesized as zero. The host
owns dispatch/admission, acceptance stays `unknown`, and the SDK does not retry.

`ContextOutcome`/`ContextOutcomeContext` carry restricted operator signals and
task/receipt/decision digests to the authenticated outcome boundary. They check
the host's accepted/rejected receipt projection, canonical document identity
and exact bytes without establishing signer trust or evaluating acceptance.
The host alone grants signing authority and changes its ledger; the adapter
does not add learning, billing or automatic retries.
