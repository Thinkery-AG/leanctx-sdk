# LeanCTX Go SDK 1.3.0

Instance names; Engine names deprecated until 2.0; engine interface contract unchanged.

- Public types, constructors, constants and options use `Instance` where they
  said `Engine` (for example `SubprocessInstanceClient`, `InstanceUnavailable`,
  `NewInstanceProtocolError`, `InstanceBinary`).
- Every Engine name remains as a deprecated Go type alias, constant or wrapper
  function; `errors.As` and type switches on old names keep matching.
- `EngineBinary` and `SessionOptions.Engine` still work when the new field is empty.
- Test harness variables: `LEANCTX_INSTANCE_BIN`, `LEANCTX_INSTANCE_BINARY`,
  `LEANCTX_TEST_INSTANCE_BINARY` first; the old `LEANCTX_*ENGINE*` names are a
  fallback with a one-time stderr deprecation warning.
- Error messages say "LeanCTX instance"/"instance".
- Wire fields (`engine_interface_version`, `engine_id`, `engine_version`, ...)
  and `EngineInterfaceVersion` are unchanged.
