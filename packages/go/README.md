# LeanCTX Go SDK

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

The Go module `github.com/Thinkery-AG/leanctx-sdk/packages/go` connects your
host-owned model and workflow to a local LeanCTX Engine. Your application keeps
its model, agent loop, and UI. The module provides the five Stable lifecycle
primitives plus the separate Stable Agent Tools API.

The module uses the Go-standard `packages/go/vX.Y.Z` tag. The published SDK
1.1.1 release requires Engine 3.10.1; current `main` sources target Engine
3.11.2. Check the release-specific compatibility record before choosing an
Engine binary.

```go
import leanctx "github.com/Thinkery-AG/leanctx-sdk/packages/go"
```

The SDK source license permits non-production use; production use, OEM
embedding, and commercial redistribution require a separate written agreement
signed by Thinkery AG. Runtime dependencies are limited to the Go standard
library.

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
