# Compatibility

## Stable SDK 1.0.0

| Component | Declared scope | Release status |
| --- | --- | --- |
| Python | CPython 3.9–3.12 | supported after final artifact approval |
| SDK wheel | pure Python, `py3-none-any` | release artifact |
| Local Engine | commit `5a90893092a7d31a8dae41ea6710b5a0c5048d15` | tested RC; public Engine release identity pending |
| Engine protocol | interface `1.0.0`, schema `1`, transport `1` | exact matching required |
| OpenAI Agents | `openai-agents==0.8.4`, CPython 3.11, macOS arm64 | optional provider-free reference gate |

The accepted macOS arm64 Engine candidate has SHA-256
`e98b3367feea41298469a27c4e87fea7956117bc5b2c48072e6e7d55e0b08857`.
That commit is not the commit currently named by the Engine `v3.9.20` tag, so
SDK publication must not claim Engine `3.9.20` compatibility. A supported,
immutable Engine release identity representing the tested contract is a
release blocker.

Compatibility is never inferred from a version string, shared checkout, or
newer commit. The Engine commit, platform artifact digest, interface, schema,
and transport are evidence-bound. Unknown response fields and non-integer
schema or transport values are rejected.

## Preview

`leanctx_sdk.preview` contains local Workspace, Checkpoint, Delta, Handoff, and
fork APIs. Preview APIs may change or be removed outside the stable deprecation
policy. Engine-dependent package installation, seeding, sealing, migration,
and verification helpers remain Internal until a matching supported public
Engine release exists.

Cloud Receipt Board, Governed Optimization/AutoTune, streaming, model routing,
and generalized framework orchestration are not shipped.

## Platform limits

Linux, Windows, macOS x86_64, other Python ABI closures, alternate frameworks,
Cloud, and alternate Engine majors require separate evidence. Provider
credentials and live model calls remain host-owned.
