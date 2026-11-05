// Deprecated Engine names (since 1.3.0, removed in 2.0) keep working.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import * as sdk from "../dist/index.js";

const PAIRS = [
  ["EngineError", "InstanceError"],
  ["EngineUnavailable", "InstanceUnavailable"],
  ["EngineTimeout", "InstanceTimeout"],
  ["EngineCrashed", "InstanceCrashed"],
  ["EngineProtocolError", "InstanceProtocolError"],
  ["EngineRejected", "InstanceRejected"],
  ["EngineExecutionError", "InstanceExecutionError"],
  ["UnsupportedEngineError", "UnsupportedInstanceError"],
  ["EngineStatus", "InstanceStatus"],
  ["SubprocessEngineClient", "SubprocessInstanceClient"],
  ["EnterpriseEngineClient", "EnterpriseInstanceClient"],
  ["EngineContextClient", "InstanceContextClient"],
  ["EnginePlanningRequest", "InstancePlanningRequest"],
  ["EngineSource", "InstanceSource"],
  ["EngineSourcePlanningClient", "InstanceSourcePlanningClient"],
];

test("every deprecated Engine export is the same value as its Instance name", () => {
  for (const [oldName, newName] of PAIRS) {
    assert.ok(sdk[newName] !== undefined, newName);
    assert.equal(sdk[oldName], sdk[newName], oldName);
  }
});

test("errors thrown under Instance names are caught by Engine names and vice versa", () => {
  const thrown = new sdk.InstanceUnavailable("gone");
  assert.ok(thrown instanceof sdk.EngineUnavailable);
  assert.ok(thrown instanceof sdk.EngineError);
  assert.equal(thrown.code, "engine_unavailable");
  const legacy = new sdk.EngineProtocolError("bad");
  assert.ok(legacy instanceof sdk.InstanceProtocolError);
  assert.ok(new sdk.UnsupportedInstanceError("x") instanceof sdk.UnsupportedEngineError);
  assert.equal(legacy.name, "InstanceProtocolError");
});

test("old engineBinary option still selects the binary; new option wins over default", () => {
  const legacy = new sdk.SubprocessEngineClient({ engineBinary: "/opt/legacy-lean-ctx" });
  assert.equal(legacy.instanceBinary, "/opt/legacy-lean-ctx");
  assert.equal(legacy.engineBinary, "/opt/legacy-lean-ctx");
  const current = new sdk.SubprocessInstanceClient({ instanceBinary: "/opt/lean-ctx" });
  assert.equal(current.instanceBinary, "/opt/lean-ctx");
  assert.equal(new sdk.SubprocessInstanceClient().instanceBinary, "lean-ctx");
  assert.equal(new sdk.SubprocessInstanceClient({ instanceBinary: "a", engineBinary: "a" }).instanceBinary, "a");
  assert.throws(() => new sdk.SubprocessInstanceClient({ instanceBinary: "a", engineBinary: "b" }), sdk.ConfigurationError);
});

test("AgentContext accepts the deprecated engineBinary option", () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-deprecated-"));
  try {
    assert.throws(() => new sdk.AgentContext(root, { engineBinary: join(root, "missing-instance") }), (error) => {
      assert.ok(error instanceof sdk.EngineUnavailable);
      assert.ok(error instanceof sdk.InstanceUnavailable);
      assert.equal(error.message, "configured instance binary is unavailable");
      return true;
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

const harness = fileURLToPath(new URL("./instance-env.mjs", import.meta.url));
function probe(env) {
  const script = `const m = await import(${JSON.stringify(harness)}); process.stdout.write(JSON.stringify([m.INSTANCE_BIN ?? null, m.INSTANCE_BINARY ?? null]));`;
  const clean = { ...process.env };
  for (const name of ["LEANCTX_INSTANCE_BIN", "LEANCTX_ENGINE_BIN", "LEANCTX_INSTANCE_BINARY", "LEANCTX_ENGINE_BINARY"]) delete clean[name];
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], { env: { ...clean, ...env }, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return { values: JSON.parse(result.stdout), stderr: result.stderr };
}

test("test harness: new env vars win; old ones are a fallback with one warning", () => {
  const both = probe({ LEANCTX_INSTANCE_BIN: "/new", LEANCTX_ENGINE_BIN: "/old" });
  assert.deepEqual(both.values, ["/new", null]);
  assert.doesNotMatch(both.stderr, /DeprecationWarning/);
  const legacy = probe({ LEANCTX_ENGINE_BIN: "/old", LEANCTX_ENGINE_BINARY: "/old2" });
  assert.deepEqual(legacy.values, ["/old", "/old2"]);
  assert.equal(legacy.stderr.match(/LEANCTX_ENGINE_BIN is deprecated/g)?.length, 1);
  assert.equal(legacy.stderr.match(/LEANCTX_ENGINE_BINARY is deprecated/g)?.length, 1);
});
