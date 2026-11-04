// Deadlines, cancellation and terminal conditions against a real child process tree.
// The Engine is a Node stand-in; nothing here starts a LeanCTX instance.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import {
  AgentContext,
  ConfigurationError,
  InstanceCrashed,
  InstanceExecutionError,
  InstanceProtocolError,
  InstanceTimeout,
} from "../dist/index.js";

const READ_CAPABILITIES = ["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"];
const posixOnly = { skip: process.platform === "win32" ? "POSIX process groups; Windows runs in the CI matrix" : false };
const temporaryRoots = new Set();

afterEach(() => {
  for (const root of temporaryRoots) rmSync(root, { recursive: true, force: true });
  temporaryRoots.clear();
});

function project() {
  const root = mkdtempSync(join(tmpdir(), "leanctx-agent-tree-"));
  temporaryRoots.add(root);
  return { root, dirs: () => readdirSync(root).filter((entry) => entry.startsWith(".leanctx-agent-")) };
}

/** Stand-in: answers the hello, keeps one descendant in its process group, then follows `behaviour`. */
function treeEngine(root, behaviour = {}) {
  const path = join(root, "stand-in-engine.mjs");
  writeFileSync(path, `#!${process.execPath}
import { spawn } from "node:child_process";
import { closeSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import readline from "node:readline";
const root = ${JSON.stringify(root)};
const publish = (name, value) => { writeFileSync(join(root, name + ".tmp"), String(value)); renameSync(join(root, name + ".tmp"), join(root, name)); };
const rl = readline.createInterface({ input: process.stdin });
const send = (value) => process.stdout.write(JSON.stringify(value) + "\\n");
let greeted = false;
rl.on("line", (line) => {
  const request = JSON.parse(line);
  if (!greeted) {
    greeted = true;
    const descendant = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
    publish("descendant.pid", descendant.pid);
    publish("leader.pid", process.pid);
    send({ id: request.id, ok: true, result: { agent_tools_interface_version: "1.0.0", allow_exec: false, allow_write: false, capabilities: ${JSON.stringify(READ_CAPABILITIES)}, engine_version: ${JSON.stringify(behaviour.engineVersion ?? "3.11.0")}, schema_version: 1, transport_version: 1 } });
    ${behaviour.afterGreeting ?? ""}
    return;
  }
  ${behaviour.onCall ?? ""}
});
setInterval(() => {}, 1000);
`, { mode: 0o700 });
  chmodSync(path, 0o700);
  return path;
}

async function waitForProcessGone(pid, timeoutMs = 3_000) {
  const deadline = Date.now() + timeoutMs;
  do {
    try { process.kill(pid, 0); } catch (error) { if (error.code === "ESRCH") return true; throw error; }
    await new Promise((resolve) => setTimeout(resolve, 10));
  } while (Date.now() < deadline);
  return false;
}

/** Members of a process group, read from the process table. */
function groupMembers(group) {
  return execFileSync("ps", ["-A", "-o", "pid=", "-o", "pgid="], { encoding: "utf8" })
    .split("\n")
    .map((line) => line.trim().split(/\s+/))
    .filter((fields) => fields.length === 2 && Number(fields[1]) === group)
    .map((fields) => Number(fields[0]));
}

function publishedPids(root) {
  return [Number(readFileSync(join(root, "leader.pid"), "utf8")), Number(readFileSync(join(root, "descendant.pid"), "utf8"))];
}

async function assertTreeGone(root) {
  const [leader, descendant] = publishedPids(root);
  try {
    assert.equal(await waitForProcessGone(leader), true, "the Engine leader should be gone");
    assert.equal(await waitForProcessGone(descendant), true, "the Engine descendant should be gone");
    assert.deepEqual(groupMembers(leader), []);
  } finally {
    for (const pid of [leader, descendant]) { try { process.kill(pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; } }
  }
}

async function assertTerminal(root, dirs, context) {
  await assertTreeGone(root);
  assert.deepEqual(dirs(), [], "the temporary policy directory should be gone");
  await assert.rejects(context.read("README.md"), InstanceCrashed);
  await context.close();
  await context.close();
}

const STOP_READING = "rl.pause(); process.stdin.pause();";
const OVERSIZED = { padding: "x".repeat(600_000) };

test("a request the Engine never reads is bounded by the deadline and typed", posixOnly, async () => {
  const { root, dirs } = project();
  // Await the greeting with its own budget; only the call below measures the 0.7 s deadline.
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { afterGreeting: STOP_READING }), timeout: 0.7, handshakeTimeout: 5 });
  const started = performance.now();
  await assert.rejects(context.call("ctx_read", OVERSIZED), InstanceTimeout);
  assert.ok(performance.now() - started < 3_000);
  await assertTerminal(root, dirs, context);
});

test("the handshake budget is the timeout unless set, and is validated like it", posixOnly, async () => {
  const { root, dirs } = project();
  // Refused before anything is started: nothing is left to clean up.
  for (const handshakeTimeout of [0, 0.05, 120.5, Number.NaN, Number.POSITIVE_INFINITY, "5"]) {
    await assert.rejects(AgentContext.open(root, { instanceBinary: treeEngine(root, {}), timeout: 1, handshakeTimeout }), ConfigurationError);
  }
  // The stand-in acknowledges a close; otherwise each close below would wait
  // out the whole timeout for an answer that never comes.
  const instanceBinary = treeEngine(root, { onCall: 'if (request.op === "close") send({ id: request.id, ok: true, result: {} });' });
  const unset = await AgentContext.open(root, { instanceBinary, timeout: 20 });
  assert.equal(unset.handshakeTimeout, 20);
  await unset.close();
  const set = await AgentContext.open(root, { instanceBinary, timeout: 20, handshakeTimeout: 5 });
  assert.deepEqual([set.timeout, set.handshakeTimeout], [20, 5]);
  // A reconnect keeps both budgets.
  const again = await set.reconnect();
  assert.deepEqual([again.timeout, again.handshakeTimeout], [20, 5]);
  await again.close();
  assert.deepEqual(dirs(), [], "the temporary policy directory should be gone");
});

for (const [name, argumentsValue] of [["a stalled write", OVERSIZED], ["a pending response", {}]]) {
  test(`cancel interrupts ${name} within a bound`, posixOnly, async () => {
    const { root, dirs } = project();
    // The deadline is far away: only cancel() may end this call.
    const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { afterGreeting: STOP_READING }), timeout: 30 });
    const outcome = context.call("ctx_read", argumentsValue).then(() => null, (error) => error);
    await new Promise((resolve) => setTimeout(resolve, 300));
    const started = performance.now();
    await context.cancel();
    assert.ok(performance.now() - started < 2_000);
    assert.ok((await outcome) instanceof InstanceCrashed);
    await assertTerminal(root, dirs, context);
  });
}

test("closed stdout with a live Engine and descendant is terminal at once", posixOnly, async () => {
  const { root, dirs } = project();
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { onCall: "closeSync(1);" }), timeout: 20 });
  const started = performance.now();
  await assert.rejects(context.read("README.md"), InstanceCrashed);
  assert.ok(performance.now() - started < 3_000, "must not wait for the deadline");
  await assertTerminal(root, dirs, context);
});

test("Engine exit with a surviving descendant is terminal at once", posixOnly, async () => {
  const { root, dirs } = project();
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { onCall: "process.exit(7);" }), timeout: 20 });
  const started = performance.now();
  await assert.rejects(context.read("README.md"), InstanceCrashed);
  assert.ok(performance.now() - started < 3_000, "must not wait for the deadline");
  await assertTerminal(root, dirs, context);
});

test("invalid ToolResult in a valid envelope terminates the whole tree", posixOnly, async () => {
  const { root, dirs } = project();
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { onCall: "send({ id: request.id, ok: true, result: { extra: true } });" }), timeout: 5 });
  await assert.rejects(context.read("README.md"), InstanceProtocolError);
  await assertTerminal(root, dirs, context);
});

test("an Engine that reports another version is refused and its tree ended", posixOnly, async () => {
  const { root, dirs } = project();
  await assert.rejects(AgentContext.open(root, { instanceBinary: treeEngine(root, { engineVersion: "3.10.5" }), timeout: 5 }), InstanceProtocolError);
  await assertTreeGone(root);
  assert.deepEqual(dirs(), []);
});

test("reconnect is explicit, starts a new process and replays nothing", posixOnly, async () => {
  const { root, dirs } = project();
  const reply = "send({ id: request.id, ok: true, result: request.op === 'close' ? {} : { text: 'ok', content_blocks: [], original_tokens: 4, output_tokens: 1, saved_tokens: 3, mode: 'full', changed: false, shell: null } }); if (request.op === 'close') process.exit(0);";
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { onCall: reply }), timeout: 5 });
  const [firstLeader] = publishedPids(root);
  await context.cancel();
  await assert.rejects(context.read("README.md"), InstanceCrashed);
  await assertTreeGone(root);
  const replacement = await context.reconnect();
  const [secondLeader] = publishedPids(root);
  assert.notEqual(secondLeader, firstLeader);
  assert.equal(replacement.metrics.toolCalls, 0);
  assert.equal((await replacement.read("README.md")).text, "ok");
  await replacement.cancel();
  await assertTreeGone(root);
  assert.deepEqual(dirs(), []);
});

test("a policy directory that cannot be removed is reported, not hidden", posixOnly, async () => {
  const { root } = project();
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { afterGreeting: STOP_READING }), timeout: 0.5 });
  const owned = join(root, ".leanctx-agent-undeletable");
  mkdirSync(owned);
  writeFileSync(join(owned, "not-created-by-the-sdk"), "keep");
  context.policyPath = join(owned, "policy.json");
  await assert.rejects(context.cancel(), (error) => error instanceof InstanceExecutionError && /policy could not be removed/.test(error.message));
  assert.equal(readFileSync(join(owned, "not-created-by-the-sdk"), "utf8"), "keep");
  await assert.rejects(context.close(), InstanceExecutionError);
  await assertTreeGone(root);
});

// Negative control: the group kill is replaced by a no-op that reports success, so the
// stand-in really keeps running. Termination must give up at its own bound, and the
// failure must be the outcome for every later cancel and close. The survivors are this
// test's own child processes and are ended by the test.
test("an Engine that cannot be terminated is a bounded, stored cleanup failure", posixOnly, async () => {
  const { root } = project();
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { afterGreeting: STOP_READING }), timeout: 30 });
  const [leader, descendant] = publishedPids(root);
  const child = context.process;
  const originalKill = process.kill;
  process.kill = function (pid, signal) {
    if (pid === -child.pid) return true; // signals nothing
    return originalKill.call(process, pid, signal);
  };
  try {
    const started = performance.now();
    const first = await context.cancel().then(() => null, (error) => error);
    const elapsed = performance.now() - started;
    assert.ok(first instanceof InstanceExecutionError, `cancel reported ${first}`);
    assert.match(first.message, /did not close within/);
    assert.ok(elapsed >= 4_000 && elapsed < 8_000, `cancel took ${elapsed} ms`);
    assert.doesNotThrow(() => originalKill.call(process, leader, 0), "the stand-in was expected to survive");
    for (const shutdown of [() => context.cancel(), () => context.close(), () => context.cancel()]) {
      const again = performance.now();
      assert.equal(await shutdown().then(() => null, (error) => error), first, "a later shutdown did not report the stored failure");
      assert.ok(performance.now() - again < 1_000, "a later shutdown waited again");
    }
    await assert.rejects(context.read("README.md"), InstanceCrashed);
  } finally {
    process.kill = originalKill;
    for (const pid of [leader, descendant]) { try { process.kill(pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; } }
  }
  await assertTreeGone(root);
});

test("repeated and parallel shutdowns share one successful cleanup", posixOnly, async () => {
  const { root, dirs } = project();
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { afterGreeting: STOP_READING }), timeout: 30 });
  const outcomes = await Promise.allSettled([0, 1, 2, 3, 4, 5, 6, 7].map((index) => (index % 2 === 0 ? context.cancel() : context.close())));
  assert.deepEqual(outcomes.filter((outcome) => outcome.status === "rejected"), []);
  await context.cancel();
  await assertTerminal(root, dirs, context);
});

// --- Group ownership at the last signal ---------------------------------------
// The Engine's group is addressed by the leader's pid, which is ours only while the
// leader is unreaped. Node reaps before it sets exitCode/signalCode, so "both null in
// the same synchronous block" is the proof. Every group signal is recorded with it.

function recordGroupSignals(child) {
  const records = [];
  const originalKill = process.kill;
  process.kill = function (pid, signal) {
    if (pid === -child.pid) records.push({ unreaped: child.exitCode === null && child.signalCode === null });
    return originalKill.call(process, pid, signal);
  };
  return { records, restore: () => { process.kill = originalKill; } };
}

test("ownership: the group is signalled only while the leader is unreaped, and never after", posixOnly, async () => {
  const { root, dirs } = project();
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, { afterGreeting: STOP_READING }), timeout: 30 });
  const { records, restore } = recordGroupSignals(context.process);
  try {
    await context.cancel();
    assert.ok(records.length > 0, "no group signal was sent");
    assert.ok(records.every((record) => record.unreaped), "a group signal was sent after the leader had been reaped");
    const sent = records.length;
    await context.cancel();
    await context.close();
    assert.equal(records.length, sent, "the group number was signalled after the reap");
  } finally { restore(); }
  await assertTerminal(root, dirs, context);
});

test("ownership: a leader Node has already reaped is not signalled; its leftover is reported, not killed", posixOnly, async () => {
  const { root } = project();
  // On the first call the stand-in leaves a child that inherits its stdout, then exits.
  const context = await AgentContext.open(root, { instanceBinary: treeEngine(root, {
    onCall: "const holder = spawn(process.execPath, ['-e', \"process.on('SIGTERM', () => {}); setInterval(() => {}, 1000)\"], { stdio: ['ignore', 'inherit', 'inherit'] }); publish('holder.pid', holder.pid); process.exit(7);",
  }), timeout: 1 });
  const child = context.process;
  const { records, restore } = recordGroupSignals(child);
  let holder = null;
  try {
    await assert.rejects(context.read("README.md"));
    const deadline = Date.now() + 3_000;
    while (child.exitCode === null && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(child.exitCode, 7, "the stand-in leader should have exited and been reaped by Node");
    holder = Number(readFileSync(join(root, "holder.pid"), "utf8"));
    // Ownership is gone. Cleanup must not signal the old number, and must not claim success.
    const outcome = await context.cancel().then(() => null, (error) => error);
    assert.ok(records.every((record) => record.unreaped), "the group was signalled after the leader had been reaped");
    assert.ok(outcome instanceof InstanceExecutionError, "a cleanup that could not confirm the tree reported success");
    assert.doesNotThrow(() => process.kill(holder, 0), "the leftover child was expected to be left alone");
  } finally {
    restore();
    // The leftover processes are this test's own.
    for (const pid of [holder, ...publishedPids(root)]) { if (pid) { try { process.kill(pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; } } }
  }
});
