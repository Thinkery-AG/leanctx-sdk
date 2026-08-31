import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import {
  AgentContext,
  AgentPermissionError,
  EngineCrashed,
  EngineProtocolError,
  ExecutionPolicy,
  ReadMode,
  ValidationError,
} from "../dist/index.js";

const READ_CAPABILITIES = ["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"];
const ALL_CAPABILITIES = [...READ_CAPABILITIES, "ctx_edit", "ctx_fill", "ctx_patch", "ctx_shell"].sort();
const EXEC_CAPABILITIES = [...READ_CAPABILITIES, "ctx_shell"].sort();
const temporaryRoots = new Set();

afterEach(() => {
  for (const root of temporaryRoots) rmSync(root, { recursive: true, force: true });
  temporaryRoots.clear();
});

function project() {
  const root = mkdtempSync(join(tmpdir(), "leanctx-agent-test-"));
  temporaryRoots.add(root);
  return { root, dirs: () => readdirSync(root).filter((entry) => entry.startsWith(".leanctx-agent-")) };
}

function engine(root, source) {
  const path = join(root, "fake-engine.mjs");
  writeFileSync(path, `#!${process.execPath}\n${source}\n`, { mode: 0o700 });
  chmodSync(path, 0o700);
  return path;
}

function helloAndLoop({ capabilities = READ_CAPABILITIES, allowExec = false, body = "" } = {}) {
  return `
import readline from "node:readline";
const capabilities = ${JSON.stringify(capabilities)};
const allowExec = ${JSON.stringify(allowExec)};
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const request = JSON.parse(line);
  if (request.op === "hello") {
    process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { agent_tools_interface_version: "1.0.0", allow_exec: allowExec, allow_write: false, capabilities, engine_version: "3.10.1", schema_version: 1, transport_version: 1 } }) + "\\n");
    return;
  }
  ${body}
});`;
}

test("missing binary cleans policy directory transactionally", () => {
  const { root, dirs } = project();
  assert.throws(() => new AgentContext(root, { engineBinary: join(root, "missing-engine") }));
  assert.deepEqual(dirs(), []);
});

for (const [name, source] of [
  ["malformed hello", "process.stdout.write('{\"bad\":true}\\n');"],
  ["incompatible hello", "process.stdout.write(JSON.stringify({ agent_tools_interface_version: '9.0.0', allow_exec: false, allow_write: false, capabilities: [], engine_version: '3.10.1', schema_version: 1, transport_version: 1 }) + '\\n');"],
]) {
  test(`${name} tears down process and exact temp policy`, async () => {
    const { root, dirs } = project();
    const context = new AgentContext(root, { engineBinary: engine(root, source), timeout: 1 });
    await assert.rejects(context.ready(), EngineProtocolError);
    await context.close();
    assert.deepEqual(dirs(), []);
  });
}

test("unexpected response id is terminal and reaped", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "process.stdout.write(JSON.stringify({ id: 'unexpected', ok: true, result: {} }) + '\\n');" });
  const context = await AgentContext.open(root, { engineBinary: engine(root, source), timeout: 1 });
  await assert.rejects(context.read("README.md", ReadMode.AUTO), EngineProtocolError);
  await assert.rejects(context.read("README.md"), EngineCrashed);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("malformed tool result envelope terminates the session", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { extra: true } }) + '\\n');" });
  const context = await AgentContext.open(root, { engineBinary: engine(root, source), timeout: 1 });
  await assert.rejects(context.read("README.md"), EngineProtocolError);
  await assert.rejects(context.read("README.md"), EngineCrashed);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("malformed response envelope for the current id cannot hang the caller", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "process.stdout.write(JSON.stringify({ id: request.id, ok: true, error: {} }) + '\\n');" });
  const context = await AgentContext.open(root, { engineBinary: engine(root, source), timeout: 1 });
  await assert.rejects(context.read("README.md"), EngineProtocolError);
  await assert.rejects(context.read("README.md"), EngineCrashed);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("call arguments must be deterministic JSON", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { text: '', content_blocks: [], original_tokens: 0, output_tokens: 0, saved_tokens: 0, mode: null, changed: false, shell: null } }) + '\\n');" });
  const context = await AgentContext.open(root, { engineBinary: engine(root, source), timeout: 1 });
  await assert.rejects(context.call("ctx_read", { invalid: undefined }), ValidationError);
  await assert.rejects(context.call("ctx_read", { invalid: Number.NaN }), ValidationError);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("run exchange deadline includes selected command timeout", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ capabilities: EXEC_CAPABILITIES, allowExec: true, body: "process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { text: '', content_blocks: [], original_tokens: 0, output_tokens: 0, saved_tokens: 0, mode: null, changed: false, shell: { exitCode: 0 } } }) + '\\n');" });
  const context = await AgentContext.open(root, { engineBinary: engine(root, source), timeout: 1, permissions: { execute: true }, executionPolicy: new ExecutionPolicy({ maxTimeout: 0.2, allowedExecutables: ["git"] }) });
  const started = performance.now();
  const result = await context.run(["git", "status"], { timeout: 0.2 });
  assert.equal(result.shell?.exitCode, 0);
  assert.ok(performance.now() - started < 500);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("empty PATH entries are ignored while resolving a bare engine", async () => {
  const { root, dirs } = project();
  const binary = engine(root, helloAndLoop());
  const oldPath = process.env.PATH;
  process.env.PATH = `:${root}`;
  try {
    const context = await AgentContext.open(root, { engineBinary: "fake-engine.mjs", timeout: 1 });
    assert.deepEqual(context.capabilities, READ_CAPABILITIES);
    await context.close();
  } finally {
    if (oldPath === undefined) delete process.env.PATH;
    else process.env.PATH = oldPath;
  }
  assert.deepEqual(dirs(), []);
});

test("AbortSignal cancellation terminates a pending request", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "if (request.op === 'call') { /* intentionally no response */ }" });
  const context = await AgentContext.open(root, { engineBinary: engine(root, source), timeout: 2 });
  const controller = new AbortController();
  const pending = context.read("README.md", ReadMode.AUTO, false, controller.signal);
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();
  await assert.rejects(pending, /aborted|terminated/i);
  await context.close();
  assert.deepEqual(dirs(), []);
});
