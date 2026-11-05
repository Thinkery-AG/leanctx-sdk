# Compatibility

## `main` (unreleased)

The current `main` source is SDK **1.3.0**, paired with LeanCTX | OSS
**3.12.2** ([published](https://github.com/yvgude/lean-ctx/releases/tag/v3.12.2)). The public SDK 1.1.1
release below remains bound to Engine 3.10.1. Package version fields in this
source checkout do not establish that an artifact has been published; verify the
specific registry artifact before installation. A source checkout of `main`
needs Engine 3.12.2; the matrix below also admits the earlier published
[3.12.1](https://github.com/yvgude/lean-ctx/releases/tag/v3.12.1).

| Component | Declared scope | Status |
| --- | --- | --- |
| Agent Tools Engine | `v3.12.2` | published; required by `main` in every language package |
| Agent Tools protocol | interface `1.0.0`, schema `1`, transport `1` | unchanged from 1.1.0 |
| Companion Engine wheels | `thinkery-leanctx-engine`, `-cuda`, `-windows-gnu` `==3.12.2` | published with Engine 3.12.2 |
| Gateway preview | `leanctx-gateway-preview` 0.1 | preview; see `docs/gateway-preview.md` |
| Rust package | Rust 1.77+ and stable | raised from 1.76 (see CHANGELOG) |

### Which Engine a hello is accepted from

One table decides it: `contracts/agent-tools-compatibility-v1.json`. Every
language package carries the same rows and accepts an Engine's hello only when
the interface, schema and transport are the ones the package speaks **and** the
(interface, schema, transport, Engine version) pair is a row of that table.
Any other pair is refused with `InstanceProtocolError` ("Agent Tools Engine pair
is unsupported") — also when it would otherwise complete the handshake. There
is no fallback to another schema and no "try anyway". The remaining hello
checks are unchanged: the exact field set, the two versions written as JSON
integers, the permission mirror and the required capabilities.

| Interface | Schema | Transport | Engine | Basis |
| --- | --- | --- | --- | --- |
| `1.0.0` | `1` | `1` | `3.11.0` | `exact_legacy` |
| `1.0.0` | `1` | `1` | `3.11.3` | `tested_pair` |
| `1.0.0` | `1` | `1` | `3.11.4` | `tested_pair` |
| `1.0.0` | `1` | `1` | `3.11.5` | `tested_pair` |
| `1.0.0` | `1` | `1` | `3.12.0` | `tested_pair` |
| `1.0.0` | `1` | `1` | `3.12.1` | `tested_pair` |
| `1.0.0` | `1` | `1` | `3.12.2` | `tested_pair` |

`exact_legacy` is the pair the earlier Engine-version equality admitted,
carried over as it was. No compatibility run of the table is claimed for it.
A row with basis `tested_pair` names a retained report and its SHA-256. The
reports are held by the release owner and are not published with the SDK; the
contract records each report's name and SHA-256 so a disclosed copy can be
matched. The 3.11.3 (N−1) and 3.11.4 (N) rows rest on the report of
2026-10-13: all six packages (Python,
TypeScript, Rust, Go, .NET, JVM) each ran the same eight Agent Tools operations
against the released macOS arm64 Engine binaries of both versions (archive
digests match the releases' `SHA256SUMS`); the Python runs carry byte-level
wire evidence. No Linux or Windows Engine was run. The 3.11.5 row rests on
the report of 2026-10-17: only the Python package
ran the eight operations, against a macOS arm64 debug build of the 3.11.5
release candidate (not a published binary), with byte-level wire evidence; the
other five packages mirror the row without a run of their own. The 3.12.0 row
rests on the report of 2026-10-21, recorded the same way against a Linux
x86_64 debug build of the 3.12.0 release candidate; it is to be replaced by a
run of all six packages against the 3.12.0 release archives. The 3.12.1 row
rests on the report of 2026-11-04: all six packages
ran the eight operations against the released macOS arm64 Engine of
[`v3.12.1`](https://github.com/yvgude/lean-ctx/releases/tag/v3.12.1)
(archive SHA-256
`212e57ad32983e43d0c4ac5e2943b82a6caf699fe40da45745b6872d72f40b08`, listed in
the release `SHA256SUMS`, whose SHA-256 is
`3ba7f999327a2d552fb46f7792aff63275bbf6656acfc24ea4934c545d498113`); the
Python run carries byte-level wire evidence. The Sigstore signature of
`SHA256SUMS` was not verified in that run, and no Linux or Windows Engine was
run. It replaces the earlier release-candidate report of 2026-11-03. The
3.12.2 row rests on the report of 2026-11-05: all six packages ran the eight
operations against the released macOS arm64 Engine of
[`v3.12.2`](https://github.com/yvgude/lean-ctx/releases/tag/v3.12.2)
(archive SHA-256
`74fdf707eca87761c818c2caae2805b990c83a5fb459d7e7de541e525b2afadc`, listed in
the release `SHA256SUMS`, whose SHA-256 is
`28df17cced3d34afccc55d73bd9f3eb77646c2a942ee6d7361af1f596a245d0f`); the
Python run carries byte-level wire evidence, and no Linux or Windows Engine
was run. Any other Engine release is a new pair and is
unsupported until a row for it exists in the table and in all six packages.
`scripts/check_language_versions.py` holds the table, the six packages and the
Agent Tools contract together.

The release-candidate pipeline is bound to the signed Engine release
[`v3.12.2`](https://github.com/yvgude/lean-ctx/releases/tag/v3.12.2), with the
digests taken from its release download.
The tag resolves to commit
`ffe6e95c34babc2d42a6a1cd60833c352e928b52`.
Its signed `SHA256SUMS` has SHA-256
`28df17cced3d34afccc55d73bd9f3eb77646c2a942ee6d7361af1f596a245d0f`.
The Sigstore identity
`https://github.com/yvgude/lean-ctx/.github/workflows/release.yml@refs/tags/v3.12.2`
was verified with `cosign verify-blob`, and release CI verifies it again.

| Platform | Release archive SHA-256 | Extracted binary SHA-256 |
| --- | --- | --- |
| Linux x86_64 GNU | `501a4c209e92e6e2345ef875440944aa046d86b16a8c2f2df8099f234a75a078` | `ddcad2c7cd7c8ee595fb2cfd899974cb94490257d8d1e8388ddabb905a475a92` |
| macOS arm64 | `74fdf707eca87761c818c2caae2805b990c83a5fb459d7e7de541e525b2afadc` | `5770e479816db8fc70136a8ef0aa53546420f7b0492b7a926e3816bc17e76824` |

The previous binding was Engine v3.12.1 (commit
`0f0297e396ecf15f578d6d0fd07dba6353b1497c`).


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
contract. `AgentContext` requires LeanCTX | OSS 3.10.1 and negotiates interface
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
`instance_binary=` explicitly. No compatibility is inferred from a newer Engine
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

## Context Store preview source qualification

The source proposal adds the read-only `leanctx-context-store-preview` 0.1
contract; see [the guide](docs/context-store-preview.md). It requires an Engine
implementing the Experimental `engine-context-store-v1` operations. This does
not change the 3.12.2 pairing declaration or certify a release, signed
artifact, fixture result or live Engine journey.
