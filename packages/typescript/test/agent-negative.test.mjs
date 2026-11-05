// Paired with LeanCTX 3.12.2 (the matrix also admits 3.11.0, 3.11.3, 3.11.4, 3.11.5, 3.12.0 and 3.12.1).
import assert from "node:assert/strict";
import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import { INSTANCE_BIN } from "./instance-env.mjs";
import {
  AgentContext,
  AsyncAgentContext,
  AgentPermissionError,
  InstanceCrashed,
  InstanceExecutionError,
  InstanceProtocolError,
  ExecutionPolicy,
  ReadMode,
  ValidationError,
} from "../dist/index.js";

const READ_CAPABILITIES = ["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"];
const ALL_CAPABILITIES = [...READ_CAPABILITIES, "ctx_edit", "ctx_fill", "ctx_patch", "ctx_shell"].sort();
const EXEC_CAPABILITIES = [...READ_CAPABILITIES, "ctx_shell"].sort();
const temporaryRoots = new Set();

test("ExecutionPolicy rejects dynamic-loader search paths but permits ordinary explicit env", () => {
  for (const name of ["LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "ld_library_path", "dYlD_LiBrArY_PaTh"]) {
    assert.throws(() => new ExecutionPolicy({ allowedEnv: [name] }), ValidationError);
  }
  assert.deepEqual(new ExecutionPolicy({ allowedEnv: ["LANG"] }).allowedEnv, ["LANG"]);
});

afterEach(() => {
  for (const root of temporaryRoots) rmSync(root, { recursive: true, force: true });
  temporaryRoots.clear();
});

function project() {
  const root = mkdtempSync(join(tmpdir(), "leanctx-agent-test-"));
  temporaryRoots.add(root);
  return { root, dirs: () => readdirSync(root).filter((entry) => entry.startsWith(".leanctx-agent-")) };
}

async function waitForProcessGone(pid, timeoutMs = 1_000) {
  const deadline = Date.now() + timeoutMs;
  do {
    try { process.kill(pid, 0); } catch (error) { if (error.code === "ESRCH") return true; throw error; }
    await new Promise((resolve) => setTimeout(resolve, 10));
  } while (Date.now() < deadline);
  return false;
}

function engine(root, source) {
  const path = join(root, "fake-engine.mjs");
  writeFileSync(path, `#!${process.execPath}\n${source}\n`, { mode: 0o700 });
  chmodSync(path, 0o700);
  return path;
}

// Q13. A stand-in Engine has no session starter. With a real Engine binary
// (LEANCTX_INSTANCE_BIN, or the deprecated LEANCTX_ENGINE_BIN) the stand-in is started through that binary's own
// session starter — the process an SDK gets from a real Engine — so the
// containment under test is the Engine's, not a copy of it. Without that
// binary the stand-in runs bare, exactly as before.
function shellQuote(value) { return `'${value.replaceAll("'", `'\\''`)}'`; }
function throughSessionStarter(root, standIn) {
  const instanceBinary = INSTANCE_BIN;
  if (!instanceBinary) return standIn;
  const path = join(root, "contained-engine");
  writeFileSync(path, `#!/bin/sh\nexec ${shellQuote(realpathSync(instanceBinary))} __session-starter --ack --watch-parent -- ${shellQuote(standIn)} "$@"\n`, { mode: 0o700 });
  chmodSync(path, 0o700);
  return path;
}

function helloAndLoop({ capabilities = READ_CAPABILITIES, allowExec = false, body = "", capturePolicyPath } = {}) {
  return `
import { copyFileSync } from "node:fs";
import readline from "node:readline";
${capturePolicyPath ? `copyFileSync(process.argv[7], ${JSON.stringify(capturePolicyPath)});` : ""}
const capabilities = ${JSON.stringify(capabilities)};
const allowExec = ${JSON.stringify(allowExec)};
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const request = JSON.parse(line);
  if (request.op === "hello") {
    process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { agent_tools_interface_version: "1.0.0", allow_exec: allowExec, allow_write: false, capabilities, engine_version: "3.12.2", schema_version: 1, transport_version: 1 } }) + "\\n");
    return;
  }
  ${body}
});`;
}

test("missing binary cleans policy directory transactionally", () => {
  const { root, dirs } = project();
  assert.throws(() => new AgentContext(root, { instanceBinary: join(root, "missing-engine") }));
  assert.deepEqual(dirs(), []);
});

test("project root is canonicalized through symlinks", async () => {
  const { root } = project();
  const target = join(root, "target");
  const alias = join(root, "alias");
  mkdirSync(target);
  symlinkSync(target, alias, "dir");
  const source = helloAndLoop({ body: `
    if (request.op === "close") {
      process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { closed: true } }) + "\\n");
    }
  ` });
  const context = await AgentContext.open(alias, { instanceBinary: engine(target, source), timeout: 1, handshakeTimeout: 5 });
  assert.equal(context.projectRoot, realpathSync(target));
  await context.close();
  assert.deepEqual(readdirSync(target).filter((entry) => entry.startsWith(".leanctx-agent-")), []);
});

// A hello answered to the request's own id, written out as text so that the
// number lexemes are exactly these: everything but the named field is the
// hello this package accepts.
function rawHello({ schema = "1", transport = "1", engineVersion = "3.12.2" }) {
  return `
import readline from "node:readline";
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  const request = JSON.parse(line);
  process.stdout.write('{"id":' + JSON.stringify(request.id) + ',"ok":true,"result":{"agent_tools_interface_version":"1.0.0","allow_exec":false,"allow_write":false,"capabilities":${JSON.stringify(READ_CAPABILITIES)},"engine_version":"${engineVersion}","schema_version":${schema},"transport_version":${transport}}}\\n');
});`;
}

for (const [name, source] of [
  ["malformed hello", "process.stdout.write('{\"bad\":true}\\n');"],
  ["incompatible hello", "process.stdout.write(JSON.stringify({ agent_tools_interface_version: '9.0.0', allow_exec: false, allow_write: false, capabilities: [], engine_version: '3.12.2', schema_version: 1, transport_version: 1 }) + '\\n');"],
  ["hello with a decimal schema version", rawHello({ schema: "1.0" })],
  ["hello with an exponent transport version", rawHello({ transport: "1e0" })],
  ["hello of an Engine pair outside the matrix", rawHello({ engineVersion: "3.11.1" })],
]) {
  test(`${name} tears down process and exact temp policy`, async () => {
    const { root, dirs } = project();
    // These are protocol cases, not deadline cases: hello has its own bounded 5 s.
    // The malformed case retains its ordinary 1 s timeout; the other cases retain 5 s.
    const context = new AgentContext(root, { instanceBinary: engine(root, source), timeout: name === "malformed hello" ? 1 : 5, handshakeTimeout: 5 });
    await assert.rejects(context.ready(), InstanceProtocolError);
    await context.close();
    assert.deepEqual(dirs(), []);
  });
}

test("cancellation respects containment after Engine leader exits with inherited pipes open", { skip: process.platform === "win32" }, async () => {
  const { root, dirs } = project();
  const source = `
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import readline from "node:readline";
const projectRoot = ${JSON.stringify(root)};
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const request = JSON.parse(line);
  if (request.op !== "hello") return;
  const descendant = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: ["ignore", "inherit", "inherit"] });
  writeFileSync(join(projectRoot, "descendant.pid"), String(descendant.pid));
  process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { agent_tools_interface_version: "1.0.0", allow_exec: false, allow_write: false, capabilities: ${JSON.stringify(READ_CAPABILITIES)}, engine_version: "3.12.2", schema_version: 1, transport_version: 1 } }) + "\\n");
  process.exit(0);
});
`;
  let context = null;
  let descendantPid = null;
  let descendantGone = false;
  try {
    // A process-cleanup case, not a deadline case: bounded 5 s for the open (behind a real
    // session starter two freshly written scripts start before the stand-in answers).
    context = await AgentContext.open(root, { instanceBinary: throughSessionStarter(root, engine(root, source)), timeout: 5 });
    const child = context.process;
    assert.ok(child);
    const deadline = Date.now() + 2_000;
    while (child.exitCode === null && child.signalCode === null && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
    if (INSTANCE_BIN) {
      // Behind a real session starter the process the SDK holds is the starter, not the
      // stand-in. When the stand-in exits, the starter closes the session's pipes and ends the
      // group; the SDK reads the end of the stream and ends its leader, which may reach the
      // starter first (observed: SIGKILL). Either way the leader is gone; what is required —
      // no descendant, no policy directory — is asserted below.
      assert.ok(child.exitCode === 0 || child.signalCode !== null, `leader still running: pid ${child.pid}`);
    } else {
      assert.equal(child.exitCode, 0, `leader: signal ${child.signalCode}, pid ${child.pid}`);
    }
    descendantPid = Number(readFileSync(join(root, "descendant.pid"), "utf8"));
    assert.ok(Number.isSafeInteger(descendantPid) && descendantPid > 0);
    if (INSTANCE_BIN) {
      await context.cancel();
      descendantGone = await waitForProcessGone(descendantPid);
      assert.equal(descendantGone, true, "the process-group descendant should be gone after close");
    } else {
      // Node has reaped the bare leader: its former group is no longer owned by the SDK.
      // The fixture owns this recorded descendant and removes it only in finally.
      const started = performance.now();
      await assert.rejects(context.cancel(), (error) => error instanceof InstanceExecutionError && /process tree did not close within 5000 ms/.test(error.message));
      const elapsed = performance.now() - started;
      assert.ok(elapsed >= 4_000 && elapsed < 8_000, `cleanup took ${elapsed} ms`);
      assert.doesNotThrow(() => process.kill(descendantPid, 0), "the unowned group descendant must not be killed by the SDK");
    }
    assert.deepEqual(dirs(), []);
  } finally {
    if (descendantPid === null) {
      try { descendantPid = Number(readFileSync(join(root, "descendant.pid"), "utf8")); }
      catch (error) { if (error.code !== "ENOENT") throw error; }
    }
    if (descendantPid !== null && Number.isSafeInteger(descendantPid) && descendantPid > 0 && !descendantGone) {
      try { process.kill(descendantPid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
      await waitForProcessGone(descendantPid);
    }
    if (context) await context.close().catch(() => {});
  }
});

test("session containment: an Engine without the starter is reported as unavailable", async () => {
  const { root, dirs } = project();
  const context = await AgentContext.open(root, { instanceBinary: engine(root, helloAndLoop()), timeout: 1 });
  assert.equal(context.sessionContainment, "unavailable");
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("session containment: an acknowledging stand-in is reported and its line is not a response", async () => {
  // Hermetic: the stand-in writes the starter's line itself. The hello that follows is
  // still accepted, so the line was not taken for a response.
  const { root, dirs } = project();
  const source = `process.stdout.write('{"leanctx_session_containment":1}\\n');\n${helloAndLoop()}`;
  const context = await AgentContext.open(root, { instanceBinary: engine(root, source), timeout: 1 });
  assert.equal(context.sessionContainment, "engine-starter");
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("session containment: the Engine's starter acknowledges, and ending it ends the Engine's tree", { skip: process.platform === "win32" || !INSTANCE_BIN }, async () => {
  const { root, dirs } = project();
  const source = `
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import readline from "node:readline";
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const request = JSON.parse(line);
  if (request.op !== "hello") return;
  const descendant = spawn(process.execPath, ["-e", "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000)"], { stdio: "ignore" });
  writeFileSync(${JSON.stringify(join(root, "descendant.pid"))}, String(descendant.pid));
  process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { agent_tools_interface_version: "1.0.0", allow_exec: false, allow_write: false, capabilities: ${JSON.stringify(READ_CAPABILITIES)}, engine_version: "3.12.2", schema_version: 1, transport_version: 1 } }) + "\\n");
});
`;
  let context = null;
  let descendantPid = null;
  let descendantGone = false;
  try {
    context = await AgentContext.open(root, { instanceBinary: throughSessionStarter(root, engine(root, source)), timeout: 2 });
    assert.equal(context.sessionContainment, "engine-starter");
    descendantPid = Number(readFileSync(join(root, "descendant.pid"), "utf8"));
    assert.ok(Number.isSafeInteger(descendantPid) && descendantPid > 0);
    // The SDK ends the starter; the guard the starter attached to the Engine's
    // group ends the Engine and the SIGTERM-ignoring descendant.
    await context.cancel();
    descendantGone = await waitForProcessGone(descendantPid, 3_000);
    assert.equal(descendantGone, true, "the Engine's descendant should be gone after cancel");
    assert.deepEqual(dirs(), []);
  } finally {
    if (descendantPid !== null && Number.isSafeInteger(descendantPid) && descendantPid > 0 && !descendantGone) {
      try { process.kill(descendantPid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
      await waitForProcessGone(descendantPid);
    }
    if (context) await context.close().catch(() => {});
  }
});

test("process-group failure falls back to the live Engine and remains an error", { skip: process.platform === "win32" }, async () => {
  const { root, dirs } = project();
  const context = await AgentContext.open(root, { instanceBinary: engine(root, helloAndLoop()), timeout: 1 });
  const child = context.process;
  assert.ok(child?.pid);
  const originalKill = process.kill;
  process.kill = function (pid, signal) {
    if (pid === -child.pid) {
      const error = new Error("simulated process-group permission failure");
      error.code = "EPERM";
      throw error;
    }
    return originalKill.call(process, pid, signal);
  };
  try {
    await assert.rejects(context.cancel(), InstanceExecutionError);
    assert.equal(child.signalCode, "SIGKILL");
    assert.deepEqual(dirs(), []);
  } finally {
    process.kill = originalKill;
    if (child.exitCode === null && child.signalCode === null) {
      try { child.kill("SIGKILL"); } catch {}
    }
    await context.close().catch(() => {});
  }
});

test("unexpected response id is terminal and reaped", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "process.stdout.write(JSON.stringify({ id: 'unexpected', ok: true, result: {} }) + '\\n');" });
  const context = await AgentContext.open(root, { instanceBinary: engine(root, source), timeout: 1 });
  await assert.rejects(context.read("README.md", ReadMode.AUTO), InstanceProtocolError);
  await assert.rejects(context.read("README.md"), InstanceCrashed);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("malformed tool result envelope terminates the session", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { extra: true } }) + '\\n');" });
  const context = await AgentContext.open(root, { instanceBinary: engine(root, source), timeout: 1 });
  await assert.rejects(context.read("README.md"), InstanceProtocolError);
  await assert.rejects(context.read("README.md"), InstanceCrashed);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("malformed response envelope for the current id cannot hang the caller", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "process.stdout.write(JSON.stringify({ id: request.id, ok: true, error: {} }) + '\\n');" });
  const context = await AgentContext.open(root, { instanceBinary: engine(root, source), timeout: 1 });
  await assert.rejects(context.read("README.md"), InstanceProtocolError);
  await assert.rejects(context.read("README.md"), InstanceCrashed);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("call arguments must be deterministic JSON", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { text: '', content_blocks: [], original_tokens: 0, output_tokens: 0, saved_tokens: 0, mode: null, changed: false, shell: null } }) + '\\n');" });
  const context = await AgentContext.open(root, { instanceBinary: engine(root, source), timeout: 1 });
  await assert.rejects(context.call("ctx_read", { invalid: undefined }), ValidationError);
  await assert.rejects(context.call("ctx_read", { invalid: Number.NaN }), ValidationError);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("run exchange deadline includes selected command timeout", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ capabilities: EXEC_CAPABILITIES, allowExec: true, body: "process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { text: '', content_blocks: [], original_tokens: 0, output_tokens: 0, saved_tokens: 0, mode: null, changed: false, shell: { exitCode: 0 } } }) + '\\n');" });
  const context = await AgentContext.open(root, { instanceBinary: engine(root, source), timeout: 1, permissions: { execute: true }, executionPolicy: new ExecutionPolicy({ maxTimeout: 0.2, allowedExecutables: ["git"] }) });
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
    const context = await AgentContext.open(root, { instanceBinary: "fake-engine.mjs", timeout: 1 });
    assert.deepEqual(context.capabilities, READ_CAPABILITIES);
    await context.close();
  } finally {
    if (oldPath === undefined) delete process.env.PATH;
    else process.env.PATH = oldPath;
  }
  assert.deepEqual(dirs(), []);
});

test("source-free policy bytes and hello capabilities stay unchanged", async () => {
  const { root, dirs } = project();
  const capturePolicyPath = join(root, "captured-policy.json");
  const context = await AgentContext.open(root, {
    instanceBinary: engine(root, helloAndLoop({ capturePolicyPath })),
    timeout: 1,
  });
  assert.deepEqual(context.capabilities, READ_CAPABILITIES);
  assert.equal(
    readFileSync(capturePolicyPath, "utf8"),
    JSON.stringify({ allow_exec: false, allow_write: false, allowed_env: [], allowed_executables: [], max_timeout_ms: 30000, schema_version: 1 }),
  );
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("selected GitLab source is snapshotted, serialized, and negotiated", async () => {
  const { root, dirs } = project();
  const capturePolicyPath = join(root, "captured-policy.json");
  const source = { host: "gitlab.example.test", project: 42, namespace: "group/project", glab: "/usr/bin/glab" };
  const context = new AsyncAgentContext(root, {
    gitlabSource: source,
    instanceBinary: engine(root, helloAndLoop({ capabilities: [...READ_CAPABILITIES, "ctx_provider"].sort(), capturePolicyPath })),
    timeout: 1,
  });
  source.project = 77;
  await context.open();
  assert.deepEqual(context.capabilities, [...READ_CAPABILITIES, "ctx_provider"].sort());
  assert.deepEqual(JSON.parse(readFileSync(capturePolicyPath, "utf8")).selected_gitlab, {
    host: "gitlab.example.test",
    project: 42,
    namespace: "group/project",
    glab: "/usr/bin/glab",
  });
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("GitLab source validates required fields, safe project range, and absolute paths", () => {
  const { root, dirs } = project();
  const source = { host: "gitlab.example.test", project: 1, namespace: "group/project", glab: "/usr/bin/glab" };
  for (const invalid of [
    { ...source, host: "bad\nhost" },
    { ...source, namespace: 1 },
    { ...source, project: true },
    { ...source, project: 0 },
    { ...source, project: Number.MAX_SAFE_INTEGER + 1 },
    { ...source, glab: "glab" },
    { ...source, configDir: "relative" },
    { ...source, token: "must not be accepted" },
  ]) {
    assert.throws(() => new AgentContext(root, { gitlabSource: invalid }), ValidationError);
  }
  assert.deepEqual(dirs(), []);
});

test("AbortSignal cancellation terminates a pending request", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ body: "if (request.op === 'call') { /* intentionally no response */ }" });
  const context = await AgentContext.open(root, { instanceBinary: engine(root, source), timeout: 2 });
  const controller = new AbortController();
  const pending = context.read("README.md", ReadMode.AUTO, false, controller.signal);
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();
  await assert.rejects(pending, /aborted|terminated/i);
  await context.close();
  assert.deepEqual(dirs(), []);
});

test("AsyncAgentContext forwards AbortSignal cancellation", async () => {
  const { root, dirs } = project();
  const source = helloAndLoop({ capabilities: EXEC_CAPABILITIES, allowExec: true, body: "if (request.op === 'call') { /* intentionally no response */ }" });
  const context = new AsyncAgentContext(root, {
    instanceBinary: engine(root, source),
    timeout: 2,
    permissions: { execute: true },
    executionPolicy: new ExecutionPolicy({ maxTimeout: 1, allowedExecutables: ["git"] }),
  });
  await context.open();
  const controller = new AbortController();
  const pending = context.run(["git", "status"], { timeout: 1, signal: controller.signal });
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();
  await assert.rejects(pending, /aborted|terminated/i);
  await context.close();
  assert.deepEqual(dirs(), []);
});
