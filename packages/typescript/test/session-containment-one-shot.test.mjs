// Session containment (Q13) for the one-shot client: what it asks of the Engine
// binary and what it reports. The stand-in Engines are Node scripts written into
// a temp root (same convention as agent-negative.test.mjs); nothing here starts
// a LeanCTX instance.
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import { InstanceExecutionError, SubprocessInstanceClient } from "../dist/index.js";

const ACK = '{"leanctx_session_containment":1}\n';
const PAYLOAD = '{"payload":"é","n":1}\n';
const roots = new Set();

afterEach(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
  roots.clear();
});

// A stand-in that prints, optionally, the session starter's acknowledgement,
// then a fixed payload, and exits with `code`.
function client({ ack, code = 0 }) {
  const root = mkdtempSync(join(tmpdir(), "leanctx-one-shot-"));
  roots.add(root);
  const path = join(root, "one-shot-engine.mjs");
  const source = [
    `#!${process.execPath}`,
    ack ? `process.stdout.write(${JSON.stringify(ACK)});` : "",
    `process.stdout.write(${JSON.stringify(PAYLOAD)});`,
    code ? `process.stderr.write("engine: nonzero_exit\\n");` : "",
    `process.exitCode = ${code};`,
    "",
  ].join("\n");
  writeFileSync(path, source, { mode: 0o700 });
  chmodSync(path, 0o700);
  return { engine: new SubprocessInstanceClient({ instanceBinary: path, timeout: 10 }), root };
}

const runOnce = ({ engine, root }) => engine.run("context-view", root, join(root, "request.json"));

test("one-shot: without an acknowledgement the bytes are unchanged", { skip: process.platform === "win32" }, async () => {
  const standIn = client({ ack: false });
  assert.equal(standIn.engine.lastSessionContainment, "unavailable");
  const output = await runOnce(standIn);
  assert.equal(output.toString("utf8"), PAYLOAD);
  assert.equal(standIn.engine.lastSessionContainment, "unavailable");
});

test("one-shot: an acknowledgement is stripped and the payload is identical", { skip: process.platform === "win32" }, async () => {
  const standIn = client({ ack: true });
  const output = await runOnce(standIn);
  assert.equal(output.toString("utf8"), PAYLOAD);
  assert.equal(standIn.engine.lastSessionContainment, "engine-starter");
});

test("one-shot: a failed run still records the acknowledgement", { skip: process.platform === "win32" }, async () => {
  const standIn = client({ ack: true, code: 3 });
  await assert.rejects(runOnce(standIn), InstanceExecutionError);
  assert.equal(standIn.engine.lastSessionContainment, "engine-starter");
});
