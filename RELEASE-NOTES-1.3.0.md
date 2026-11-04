# LeanCTX SDK 1.3.0 — Release notes

**Instance names; Engine names deprecated until 2.0; engine interface contract unchanged.**

A running copy of LeanCTX | OSS is now called an *instance*. In all six language
packages (Python, TypeScript, Go, Rust, JVM, .NET) the public names that said
`Engine` now say `Instance` (`InstanceUnavailable`, `SubprocessInstanceClient`,
`EnterpriseInstanceClient`, `InstancePlanningRequest`, ...). This is a minor
release: every old Engine name stays available as a deprecated alias until SDK
2.0, so existing code compiles and behaves the same, including catching errors,
type checks and pattern matching.

Unchanged:

- The engine interface contract: wire/JSON field names and values
  (`engine_interface_version`, `engine_id`, `engine_version`, `engine_identity`,
  `engine_binding`, ...) and the language-level fields that mirror them.
- Interface, schema and transport versions and the stable error codes
  (`engine_unavailable`, `unsupported_engine`, ...).

Environment variables (new name read first; the old one still works as a
fallback with a one-time deprecation warning):

| Deprecated | New |
|---|---|
| `LEANCTX_ENGINE_BIN` | `LEANCTX_INSTANCE_BIN` |
| `LEANCTX_ENGINE_BINARY` | `LEANCTX_INSTANCE_BINARY` |
| `LEANCTX_ENGINE_PROOF_FILE` | `LEANCTX_INSTANCE_PROOF_FILE` |
| `LEANCTX_REQUIRE_ENGINE` | `LEANCTX_REQUIRE_INSTANCE` |
| `LEANCTX_TEST_ENGINE_BINARY` | `LEANCTX_TEST_INSTANCE_BINARY` |

Error and log messages now say "LeanCTX instance" / "instance".

## Python (`thinkery-leanctx-sdk` 1.3.0)

- New primary names in `leanctx_sdk`: `InstanceError`, `InstanceUnavailable`,
  `InstanceTimeout`, `InstanceCrashed`, `InstanceProtocolError`,
  `InstanceExecutionError`, `InstanceRejected`, `UnsupportedInstanceError`,
  `InstanceStatus`, `InstanceClient`, `InstancePlanningClient`,
  `SubprocessInstanceClient`, `EnterpriseInstanceClient`,
  `InstanceContextClient`, `InstanceContextReadResult`,
  `InstancePlanningRequest`, `InstanceSource`; in `leanctx_sdk.preview`:
  `LocalCheckpointPackageInstance`. `__all__` lists only the new names.
- Old names resolve through module-level `__getattr__` to the *same* objects
  and emit `DeprecationWarning`; `from leanctx_sdk import EngineUnavailable`
  and `except EngineUnavailable` keep working. The `...Error`-suffixed aliases
  in `leanctx_sdk.errors` follow the same scheme.
- Modules `leanctx_sdk.engine` and `leanctx_sdk.enterprise_engine` were renamed
  to `leanctx_sdk.instance` and `leanctx_sdk.enterprise_instance`; the old
  modules remain as deprecated re-exports.
- Keywords: `instance_binary=` replaces `engine_binary=` (`AgentContext`,
  `AsyncAgentContext`, `SubprocessInstanceClient`; attribute
  `SubprocessInstanceClient.instance_binary`, old attribute deprecated);
  `instance=` replaces `engine=` (`ContextSession`, `seal_checkpoint_package`,
  `seed_workspace_from_package`, `migrate_snapshot_v1`). Old keywords warn;
  passing both raises `TypeError`.
- Kept: `ENGINE_INTERFACE_VERSION` and `SUPPORTED_AGENT_TOOLS_ENGINE_VERSION`
  (they carry wire values), the `"engine-starter"` containment value and the
  `engine:<code>` degradation markers.

## Other languages

See each package's CHANGELOG for its exact alias idiom (TypeScript `@deprecated`
exports, Go type aliases and `NewEngineX` wrappers, Rust `#[deprecated]` type
aliases, JVM `@Deprecated(forRemoval = true)` classes, .NET `[Obsolete]`).
