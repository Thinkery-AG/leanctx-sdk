# Compatibility

## `main` (unreleased)

The current `main` source is SDK **1.2.0**, prepared against LeanCTX Engine
**3.11.2**, an updater hotfix for 3.11.1 that is not yet published. The public
SDK 1.1.1 release below remains bound to Engine 3.10.1. Package version fields
in this source checkout do not establish that an artifact has been published;
verify the specific registry artifact before installation. A source checkout of
`main` needs Engine 3.11.2.

| Component | Declared scope | Status |
| --- | --- | --- |
| Agent Tools Engine | `v3.11.2` | unreleased hotfix; required by `main` in every language package |
| Agent Tools protocol | interface `1.0.0`, schema `1`, transport `1` | unchanged from 1.1.0 |
| Companion Engine wheels | `thinkery-leanctx-engine`, `-cuda`, `-windows-gnu` `==3.11.2` | published with Engine 3.11.2 |
| Gateway preview | `leanctx-gateway-preview` 0.1 | preview; see `docs/gateway-preview.md` |
| Rust package | Rust 1.77+ and stable | raised from 1.76 (see CHANGELOG) |

Until v3.11.2 is tagged, the release-candidate pipeline keeps the last
published pairing: the signed Engine release
[`v3.11.1`](https://github.com/yvgude/lean-ctx/releases/tag/v3.11.1).
The tag resolves to commit
`b4fd337b9ae0ca98b7bbc28598082caf1b820828`.
Its signed `SHA256SUMS` has SHA-256
`f3b2b650516d9d485b7b051f8fa8eedc92b834bf99b791be6c2ee447dcee33d0`.
Release CI verifies the Sigstore identity
`https://github.com/yvgude/lean-ctx/.github/workflows/release.yml@refs/tags/v3.11.1`.

| Platform | Release archive SHA-256 | Extracted binary SHA-256 |
| --- | --- | --- |
| Linux x86_64 GNU | `08d9805c0405fdfebcd79c2deaeb6932f4d2b893b19241c3fc92fa64150ccbc0` | `eb6a4ad35d8dd0884f68dbebf8d2469ea8d2f9d5593f359d8a7f01a46c1a3458` |
| macOS arm64 | `149c4962c98e5de2d9bec8cc7bffa327d1b6784be28af497e542bb8789f63b63` | `cfd2d1da47385a3f2a0425d299df0517171a639282422b49ac16a46acf317e33` |

`scripts/verify_agent_context_e2e.py` passes against the released macOS arm64
binary (Engine 3.11.1, SDK 1.2.0, status PASS). The Agent Tools protocol
(interface 1.0.0, schema 1, transport 1) is unchanged from 3.10.5.

## Stable SDK 1.1.1

[SDK 1.1.1](https://github.com/Thinkery-AG/leanctx-sdk/releases/tag/v1.1.1)
is a maintenance release from the published 1.1.0 compatibility line. It updates
product documentation and audited optional dependencies while retaining Engine
3.10.1, Rust 1.76, the public APIs, protocol versions and licenses. Its source
is `392946aaf689e328d05a9c6fbac35f5316ec1649`; the Python wheel SHA-256 is
`23cfa48772ad509404ad41a5cebbfa3610321b877cdc8a26ddb2f381cdeae172`.

<a id="stable-sdk-110"></a>
The compatibility matrix below also applies to the historical SDK 1.1.0 release.

SDK 1.1 adds the Agent Tools Interface without changing the SDK 1.0 lifecycle
contract. `AgentContext` requires LeanCTX Engine 3.10.1 and negotiates interface
`1.0.0`, schema `1`, and transport `1` before exposing any tool.

| Component | Declared scope | Release status |
| --- | --- | --- |
| Python | CPython 3.9–3.14 | supported |
| SDK wheel | pure Python, `py3-none-any` | release artifact |
| TypeScript | Node.js 22+ | supported |
| Go | Go 1.24+ | supported monorepo module |
| Rust | Rust 1.76+ and stable | supported |
| JVM | Java 21 / Kotlin 2.1 | supported |
| .NET | .NET 8+ | supported |
| Agent Tools Engine | `v3.10.1` | published; required for `AgentContext` |
| Agent Tools protocol | interface `1.0.0`, schema `1`, transport `1` | exact matching required |
| OpenAI Agents | `openai-agents==0.8.4`, CPython 3.10+ | optional exact-version integration |

The `[agent]`, `[agent-cuda]`, and `[agent-windows-gnu]` extras install
their exact 3.10.1 companion Engine packages. Source checkouts may instead pass
`engine_binary=` explicitly. No compatibility is inferred from a newer Engine
or an executable found on `PATH`.

The following historical release evidence is retained for
[`v3.10.1`](https://github.com/yvgude/lean-ctx/releases/tag/v3.10.1).
The tag resolves to commit
`4a76710a6c792229f170a66fdda1f4a0a64f47ee`.
Its signed `SHA256SUMS` has SHA-256
`86fd1d4e4b27541e15664c8a2c93d9b6bcd8b1b2fd7e8914943496ba213bc170`.
It does not certify the current source candidate. That release's CI verifies the Sigstore identity
`https://github.com/yvgude/lean-ctx/.github/workflows/release.yml@refs/tags/v3.10.1`.

| Platform | Release archive SHA-256 | Extracted binary SHA-256 |
| --- | --- | --- |
| Linux x86_64 GNU | `dae5bde18c58b7976b98f967f261bbdece8d3072dda23e6509f3da7d581b5c58` | `0385c8169a4b20df84dc0f1e8b32788c3b7a402cb0b5ff484d391f8cd67a5bbc` |
| macOS arm64 | `25a14a6bc597739c8f5e8a8d18e85e38f89711e4fcb82bdd648b060d201360fd` | `00e08272eb443ab9c9539c58ec24cd0d9ae02d789ec0944ab56919e24a39fa23` |

## Stable SDK 1.0.0

| Component | Declared scope | Release status |
| --- | --- | --- |
| Python | CPython 3.9–3.14 | supported |
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

Engine binary availability remains limited to the certified platforms listed
above. Other Engine architectures, alternate frameworks, Cloud, and alternate
Engine majors require separate evidence. Provider credentials and live model
calls remain host-owned.
