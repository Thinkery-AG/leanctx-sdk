# LeanCTX Go SDK

**Embed LeanCTX context control into your application.** LeanCTX is the
**Context Gateway for AI Systems**: **Control what your AI can see.**

The Go module `github.com/Thinkery-AG/leanctx-sdk/packages/go` connects your
host-owned model and workflow to a local LeanCTX instance. Your application keeps
its model, agent loop, and UI. The module provides the five Stable lifecycle
primitives plus the separate Stable Agent Tools API.

The module uses the Go-standard `packages/go/vX.Y.Z` tag. The published SDK
1.1.1 release requires LeanCTX | OSS 3.10.1; current `main` sources target LeanCTX | OSS
3.12.2. Check the release-specific compatibility record before choosing an
instance binary.

```go
import leanctx "github.com/Thinkery-AG/leanctx-sdk/packages/go"
```

The SDK source license permits non-production use; production use, OEM
embedding, and commercial redistribution require a separate written agreement
signed by Thinkery AG. Runtime dependencies are limited to the Go standard
library.

## Instance names (SDK 1.3.0)

LeanCTX running on a machine is an *instance*; the Instance names are primary.
Every Engine name stays a deprecated alias until SDK 2.0 (Go type aliases, so
`errors.As`, type assertions and type switches on old names keep matching).
The engine interface contract is unchanged: wire fields such as
`engine_interface_version`, `engine_id` and `engine_version`, and
`EngineInterfaceVersion`/`ContextView.EngineVersion()`, keep their names.

| Deprecated (until 2.0) | Use |
|---|---|
| `EngineClient`, `SubprocessEngineClient`, `EnterpriseEngineClient`, `EngineContextClient` | `InstanceClient`, `SubprocessInstanceClient`, `EnterpriseInstanceClient`, `InstanceContextClient` |
| `NewSubprocessEngineClient`, `NewEnterpriseEngineClient`, `NewEngineContextClient`, `NewEnginePlanningRequest` | `NewSubprocessInstanceClient`, `NewEnterpriseInstanceClient`, `NewInstanceContextClient`, `NewInstancePlanningRequest` |
| `EngineError`, `EngineUnavailable`, `EngineTimeout`, `EngineCrashed`, `EngineProtocolError`, `EngineRejected`, `EngineExecutionError`, `UnsupportedEngineError` (and `NewEngineX`) | `InstanceError`, `InstanceUnavailable`, `InstanceTimeout`, `InstanceCrashed`, `InstanceProtocolError`, `InstanceRejected`, `InstanceExecutionError`, `UnsupportedInstanceError` (and `NewInstanceX`) |
| `EngineStatus*`, `SessionContainmentEngineStarter`, other `Engine*` request/response types and constants | same name with `Instance` |
| Options `EngineBinary`, `SessionOptions.Engine` | `InstanceBinary`, `SessionOptions.Instance` (old field used only when the new one is empty) |
| `LEANCTX_ENGINE_BIN`, `LEANCTX_ENGINE_BINARY`, `LEANCTX_TEST_ENGINE_BINARY` (test harness) | `LEANCTX_INSTANCE_BIN`, `LEANCTX_INSTANCE_BINARY`, `LEANCTX_TEST_INSTANCE_BINARY` (old name read as fallback with a one-time stderr warning) |

## Unreleased v4 integration

`NewEnterpriseInstanceClient` adds authenticated standalone instance source planning
through `ContextPlanSources` and its cancellable context variant. Source
materialization uses `ContextMaterializeSources` and its context variant with an
`InstanceSourceMaterializationRequest` bound to the planning result's governance
revision, binding digest and optional evaluation time. The configured
tenant is an expected response binding, never an authorization override. Requests
contain source IDs, not source bodies; the server remains the policy and planning
authority. HTTPS is the default; literal loopback HTTP requires the explicit
test option. Redirects, environment proxies and automatic POST retries are off.

This supporting adapter is not part of published 1.1.0 and does not change the
existing `InstanceClient` interface. Planning is not provider dispatch, a signed
receipt or task acceptance; materialization returns digest-checked context only. See
[Engine planning](../../docs/ENGINE-PLANNING.md) for the canonical boundary;
other-language and installed-release parity remain separate acceptance work.

`ContextExecuteV2` and its context variant consume a declared local-native source
execution through `InstanceSourceExecutionV2Request`. They return the exact v2
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

## Process containment (session starter)

The SDK starts the LeanCTX instance binary with `LEAN_CTX_SESSION_CONTAINMENT=1`.
A LeanCTX instance binary that has a session starter answers with one line on standard
output before the LeanCTX instance itself starts (`{"leanctx_session_containment":1}`).
The SDK removes that line and reports one of two states (type
`SessionContainment`):

- `SessionContainmentInstanceStarter`: the process the SDK started is the
  instance's session starter. It runs the LeanCTX instance in a process group of its own,
  ends that group when the LeanCTX instance exits, when the SDK ends the starter or when
  the SDK's process is gone, and exits only afterwards.
- `SessionContainmentUnavailable`: no acknowledgement. The SDK's own
  process-group handling is all there is.

Read it from `(*AgentContext).SessionContainment()` (also on
`AsyncAgentContext`) and, for the one-shot client, from
`(*SubprocessInstanceClient).LastSessionContainment()` (the most recent completed
run).

On Windows there is no starter process: a LeanCTX instance that has the mode joins a
kill-on-close job of its own before it starts anything and acknowledges with
the same line (reported as the same state); everything it started ends when
the LeanCTX instance process ends. A host job that forbids nesting leaves it
unacknowledged.

Not covered: descendants that leave their
process group (`setsid`, `setpgid`), and a graceful stop — the SDK ends the
starter, and the LeanCTX instance's group is ended right after, which can be a few
milliseconds after `Cancel`/`Close` returned. Whether a given instance build has
the starter is told only by the reported state.

## Context Store preview (source proposal)

`ReadPolicyEvidence` and `ReadTaskLineage` provide read-only tenant/project-scoped
instance access through `ContextStoreScope`. `ParsePolicyEvidence` and
`ParseTaskLineage` validate the corresponding wire documents; unmeasured
evidence remains unmeasured and incomplete lineage carries explicit gaps.
The preview contract is `leanctx-context-store-preview` 0.1.0.

This source is paired with LeanCTX | OSS 3.12.2 (published); the
compatibility matrix also admits 3.11.0, 3.11.3, 3.11.4, 3.11.5, 3.12.0 and 3.12.1.
