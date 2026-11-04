// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
// Test harness: LeanCTX instance binary from the environment. The new
// variable wins; the deprecated Engine variable is a fallback (until 2.0)
// that emits one deprecation warning per variable.
const warned = new Set();

export function instanceBinaryFromEnv(name, deprecatedName, env = process.env) {
  const value = env[name];
  if (value) return value;
  const legacy = env[deprecatedName];
  if (!legacy) return undefined;
  if (!warned.has(deprecatedName)) {
    warned.add(deprecatedName);
    process.emitWarning(`${deprecatedName} is deprecated since 1.3.0; use ${name}`, { type: "DeprecationWarning", code: "LEANCTX_ENGINE_ENV" });
  }
  return legacy;
}

export const INSTANCE_BIN = instanceBinaryFromEnv("LEANCTX_INSTANCE_BIN", "LEANCTX_ENGINE_BIN");
export const INSTANCE_BINARY = instanceBinaryFromEnv("LEANCTX_INSTANCE_BINARY", "LEANCTX_ENGINE_BINARY");
