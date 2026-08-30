# Compatibility

## Stable SDK 1.1.0

SDK 1.1 adds the Agent Tools Interface without changing the SDK 1.0 lifecycle
contract. `AgentContext` requires LeanCTX Engine 3.10.1 and negotiates interface
`1.0.0`, schema `1`, and transport `1` before exposing any tool.

| Component | Declared scope | Release status |
| --- | --- | --- |
| Python | CPython 3.9–3.12 | supported |
| SDK wheel | pure Python, `py3-none-any` | release candidate |
| Agent Tools Engine | `v3.10.1` | required for `AgentContext`; not yet published |
| Agent Tools protocol | interface `1.0.0`, schema `1`, transport `1` | exact matching required |
| OpenAI Agents | `openai-agents==0.8.4`, CPython 3.10+ | optional exact-version integration |

The `[agent]`, `[agent-cuda]`, and `[agent-windows-gnu]` extras become
installable only when their exact 3.10.1 companion Engine packages are
published. Until then, source checkouts must pass `engine_binary=` explicitly.
No compatibility is inferred from a newer Engine or an executable found on
`PATH`.

## Stable SDK 1.0.0

| Component | Declared scope | Release status |
| --- | --- | --- |
| Python | CPython 3.9–3.12 | supported |
| SDK wheel | pure Python, `py3-none-any` | release artifact |
| Local Engine | `v3.10.0`, commit `5b6920216177b01f48694efff1d6be9505665263` | supported public release |
| Engine protocol | interface `1.0.0`, schema `1`, transport `1` | exact matching required |
| OpenAI Agents | `openai-agents==0.8.4`, CPython 3.11, macOS arm64 | optional provider-free reference gate |

The supported Engine release is
[`v3.10.0`](https://github.com/yvgude/lean-ctx/releases/tag/v3.10.0).
Its signed `SHA256SUMS` has SHA-256
`0fab38178ac0cbb4b1f807c602f77bc738082672f627fe02448b8be8e7f5d8e4`.
Release CI verifies the Sigstore identity
`https://github.com/yvgude/lean-ctx/.github/workflows/release.yml@refs/tags/v3.10.0`.

| Platform | Release archive SHA-256 | Extracted binary SHA-256 |
| --- | --- | --- |
| Linux x86_64 GNU | `f5ad20cbf3eba9ff3024348cc0abe71199f47ae0e13d5554bfeb6345154928e0` | `735f60243cf4030ee6bbb292f06fb23742483fd4c857aac91e02914b3a80ac03` |
| macOS arm64 | `ecd773971d118a19a3de723e82d9f0831c8e1543094d350b3861bcaa75dc6035` | `8f7787ccc6376f1d34b8d342fbc916bd082673e6797ea384e6e10edc3641b4eb` |

Compatibility is never inferred from a version string, shared checkout, or
newer commit. The Engine commit, platform artifact digest, interface, schema,
and transport are evidence-bound. Unknown response fields and non-integer
schema or transport values are rejected.

## Preview

`leanctx_sdk.preview` contains local Workspace, Checkpoint, Delta, Handoff, and
fork APIs. Preview APIs may change or be removed outside the stable deprecation
policy. Engine-dependent package installation, seeding, sealing, migration,
and verification helpers are Preview and require the exact Engine release
above.

P8 Cloud Receipt Board, P9 Governed Optimization/AutoTune, streaming, model
routing, and generalized framework orchestration are not shipped.

## Platform limits

Windows, macOS x86_64, other Linux architectures, other Python ABI closures,
alternate frameworks, Cloud, and alternate Engine majors require separate
evidence. Provider credentials and live model calls remain host-owned.
