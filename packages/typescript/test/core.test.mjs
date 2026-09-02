import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  AgentContext,
  AgentPermissionError,
  AgentPermissions,
  ArtifactIntegrityError,
  CompatibilityError,
  ContextMeasurement,
  ContextPlan,
  ContextReceipt,
  ContextReceiptLink,
  ContextSession,
  ContextSource,
  ContextView,
  EngineProtocolError,
  HostOutcome,
  Integrity,
  RecoveredSource,
  SubprocessEngineClient,
} from "../dist/index.js";
import { parseResponse } from "../dist/engine.js";
import { canonicalBytes, sha256Digest } from "../dist/protocol.js";

test("root exports equal the 1.1 Stable allowlist", async () => {
  const module = await import("../dist/index.js");
  const expected = [
    "AGENT_TOOLS_INTERFACE_VERSION", "AGENT_TOOLS_SCHEMA_VERSION",
    "AGENT_TOOLS_TRANSPORT_VERSION", "SUPPORTED_AGENT_TOOLS_ENGINE_VERSION",
    "ENGINE_INTERFACE_VERSION", "SCHEMA_VERSION", "TRANSPORT_VERSION", "__version__",
    "AgentContext", "AgentMetrics", "AgentPermissions", "AsyncAgentContext",
    "ExecutionPolicy", "ReadMode", "ToolResult", "ContextSession", "ContextSource",
    "ContextView", "ContextPlan", "ContextReceipt", "ContextFailure",
    "ContextMeasurement", "ContextReceiptLink", "EngineStatus", "FailureCode",
    "Freshness", "HostOutcome", "Integrity", "RecoveredSource", "SessionState",
    "SubprocessEngineClient", "SDKError", "ArtifactIntegrityError",
    "CompatibilityError", "ConfigurationError", "EngineError", "EngineExecutionError",
    "EngineProtocolError", "EngineRejected", "EngineTimeout", "EngineUnavailable",
    "FrameworkCompatibilityError", "FrameworkIntegrationError", "PolicyAdmissionError",
    "RecoveryUnavailableError", "SessionStateError", "SourceUnavailableError",
    "UnsupportedEngineError", "ValidationError", "EngineCrashed",
    "AgentPermissionError", "UnsupportedCapabilityError",
  ].sort();
  assert.deepEqual(Object.keys(module).sort(), expected);
});

test("language-neutral Agent Tools contract freezes the 1 MiB request bound", () => {
  const contractPath = fileURLToPath(new URL("../../../contracts/agent-tools-v1.json", import.meta.url));
  const contract = JSON.parse(readFileSync(contractPath, "utf8"));
  assert.equal(contract.limits.request_bytes, 1024 * 1024);
});

function serializationFixtureView(source) {
  const text = "fresh synthetic view\n";
  const sourceText = "fresh synthetic source\n";
  const outputDigest = sha256Digest(text);
  const sourceDigest = sha256Digest(sourceText);
  const sourceRef = `source:synthetic-path-sha256:${"a".repeat(64)}`;
  const inputRef = `input:synthetic-request-sha256:${"b".repeat(64)}`;
  const invocationId = "engine-invocation-synthetic";
  const measurements = [
    new ContextMeasurement("input_tokens", "token", "measured", 1),
    new ContextMeasurement("output_tokens", "token", "measured", 2),
  ];
  const receiptLink = new ContextReceiptLink(
    1,
    "engine-receipt-synthetic",
    `receipt:sha256:${"d".repeat(64)}`,
    `sha256:${"d".repeat(64)}`,
    invocationId,
  );
  const invocation = {
    schema_version: 1,
    invocation_id: invocationId,
    engine: { engine_id: "lean-ctx-local", engine_version: "3.9.20" },
    operation: {
      capability_id: "capability://leanctx/context-optimization",
      capability_version: "1.0.0",
    },
    input_ref: inputRef,
    input_digest: `sha256:${"c".repeat(64)}`,
    source_refs: [inputRef, sourceRef],
    policy_admission: { policy_ref: "policy:synthetic", decision: "admitted" },
  };
  const observation = {
    schema_version: 1,
    invocation_id: invocationId,
    status: "succeeded",
    output_ref: `output:${outputDigest.slice("sha256:".length)}`,
    output_digest: outputDigest,
    source_lineage: [inputRef, sourceRef],
    measurements,
    failure: null,
    receipt_link: receiptLink,
  };
  return new ContextView({
    source,
    text,
    outputRef: observation.output_ref,
    outputDigest,
    sourceRef,
    sourceDigest,
    recoveryRef: inputRef,
    status: "succeeded",
    measurements,
    failure: null,
    receiptLink,
    invocation,
    observation,
  });
}

function fakeAgentEngine(root) {
  const path = join(root, "fake-agent-engine");
  const source = `#!${process.execPath}
const { readFileSync } = require("node:fs");
const { createInterface } = require("node:readline");
const args = process.argv.slice(2);
if (args[0] !== "engine" || args[1] !== "tool-session") process.exit(2);
const policyPath = args[args.indexOf("--policy-file") + 1];
const policy = JSON.parse(readFileSync(policyPath, "utf8"));
const capabilities = ["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"];
if (policy.allow_write) capabilities.push("ctx_edit", "ctx_fill", "ctx_patch");
if (policy.allow_exec) capabilities.push("ctx_shell");
capabilities.sort();
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
lines.on("line", (line) => {
  const request = JSON.parse(line);
  let result;
  if (request.op === "hello") {
    result = { agent_tools_interface_version: "1.0.0", allow_exec: policy.allow_exec, allow_write: policy.allow_write, capabilities, engine_version: "3.10.1", schema_version: 1, transport_version: 1 };
  } else if (request.op === "call") {
    result = { text: request.tool + ":ok", content_blocks: [], original_tokens: 10, output_tokens: 4, saved_tokens: 6, mode: null, changed: request.tool === "ctx_patch", shell: request.tool === "ctx_shell" ? { exit_code: 0 } : null };
  } else if (request.op === "close") {
    result = {};
  } else {
    process.stdout.write(JSON.stringify({ id: request.id, ok: false, error: { code: "invalid_request", message: "unsupported" } }) + "\\n");
    return;
  }
  process.stdout.write(JSON.stringify({ id: request.id, ok: true, result }) + "\\n");
  if (request.op === "close") setImmediate(() => process.exit(0));
});
`;
  writeFileSync(path, source, { encoding: "utf8", mode: 0o700 });
  chmodSync(path, 0o700);
  return path;
}

function verifiedView(source, sourceText = "original\n") {
  const text = "shaped context\n";
  const digest = sha256Digest(text);
  const sourceDigest = sha256Digest(sourceText);
  const inputRef = `input:fixture-${"b".repeat(64)}`;
  const sourceRef = `source:fixture-${"a".repeat(64)}`;
  const invocationId = "engine-invocation-fixture";
  const invocation = {
    schema_version: 1,
    invocation_id: invocationId,
    engine: { engine_id: "lean-ctx-local", engine_version: "3.10.1" },
    operation: { capability_id: "capability://leanctx/context-optimization", capability_version: "1.0.0" },
    input_ref: inputRef,
    input_digest: `sha256:${"c".repeat(64)}`,
    source_refs: [inputRef, sourceRef],
    policy_admission: { policy_ref: "policy:fixture", decision: "admitted" },
  };
  const observation = {
    schema_version: 1,
    invocation_id: invocationId,
    status: "succeeded",
    output_ref: `output:${digest.slice(7)}`,
    output_digest: digest,
    source_lineage: [inputRef, sourceRef],
    measurements: [new ContextMeasurement("input_tokens", "token", "measured", 1)],
    failure: null,
    receipt_link: new ContextReceiptLink(1, "engine-receipt-fixture", `receipt:sha256:${"d".repeat(64)}`, `sha256:${"d".repeat(64)}`, invocationId),
  };
  return new ContextView({ source, text, outputRef: `output:${digest.slice(7)}`, outputDigest: digest, sourceRef, sourceDigest, recoveryRef: inputRef, status: "succeeded", measurements: observation.measurements, receiptLink: observation.receipt_link, invocation, observation });
}

test("Product plan identity matches the independently authored v1 fixture", () => {
  const source = new ContextSource("fixture/source.txt", { projectRoot: "/PROJECT" });
  const plan = new ContextPlan("fixture-session-r1", "fixture-task-r1", "inspect the synthetic fixture", source);
  assert.equal(plan.planId, "plan:sha256:25f29db61cbb19986896152ecf2c8b1b60a1187c83a8e4ceefb0b7203542296e");
});

test("all five Product primitives match Python v1 serialization fingerprints", () => {
  const fixturePath = fileURLToPath(new URL("../../../fixtures/sdk-v1/serialization-sha256.json", import.meta.url));
  const expected = JSON.parse(readFileSync(fixturePath, "utf8"));
  const source = new ContextSource("fixture/source.txt", { projectRoot: "/PROJECT" });
  const plan = new ContextPlan("session-fixed", "task-fixed", "inspect", source);
  const view = serializationFixtureView(source);
  const receipt = new ContextReceipt({
    sessionId: "session-fixed",
    taskId: "task-fixed",
    planId: plan.planId,
    view,
    outcome: "completed",
    integrityStatus: "sealed",
    usage: { requests: 1 },
  });
  const session = new ContextSession("inspect", {
    projectRoot: "/PROJECT",
    sessionId: "session-fixed",
    taskId: "task-fixed",
    engine: {},
  });
  const values = {
    ContextSource: source.toDict(),
    ContextPlan: plan.toDict(),
    ContextView: view.toDict(),
    ContextReceipt: receipt.toDict(),
    ContextSession: {
      session_id: session.sessionId,
      task_id: session.taskId,
      task: session.task,
      state: session.state,
    },
  };
  const actual = Object.fromEntries(Object.entries(values).map(([name, value]) => [
    name,
    sha256Digest(canonicalBytes(value)).slice("sha256:".length),
  ]));
  assert.deepEqual(actual, expected);
});

test("canonical JSON uses Python code-point ordering and rejects ambiguous JS numbers", () => {
  assert.equal(canonicalBytes({ "\u{10000}": 1, "\ue000": 2 }).toString(), "{\"\":2,\"𐀀\":1}");
  assert.throws(() => canonicalBytes({ value: 1.5 }));
  assert.throws(() => canonicalBytes({ value: -0 }));
});

test("strict Engine parser rejects unknown, duplicate, and incompatible fields", () => {
  const source = new ContextSource("fixture/source.txt", { projectRoot: "/PROJECT" });
  const view = verifiedView(source);
  const response = {
    schema_version: 1,
    transport_version: 1,
    engine_interface_version: "1.0.0",
    view: { text: view.text, output_ref: view.outputRef, output_digest: view.outputDigest },
    invocation: view.invocation,
    observation: { ...view.observation, measurements: view.measurements.map((item) => item.toDict()), receipt_link: view.receiptLink?.toDict() ?? null },
    recovery: view.recoveryBinding(),
  };
  assert.equal(parseResponse(JSON.stringify(response)).records?.observation.status, "succeeded");
  assert.throws(() => parseResponse(JSON.stringify({ ...response, extra: true })), EngineProtocolError);
  assert.throws(() => parseResponse('{"schema_version":1,"schema_version":1}'), EngineProtocolError);
  assert.throws(() => parseResponse(JSON.stringify({ ...response, transport_version: 2 })), CompatibilityError);
});

test("ContextSession seals truthful completion and exact recovery", async () => {
  const root = "/PROJECT";
  const source = new ContextSource("fixture/source.txt", { projectRoot: root });
  const engine = {
    contextView: () => verifiedView(source),
    recover: async (_root, _path, recoveryRef, sourceRef, sourceDigest) => new RecoveredSource("original\n", sourceRef, sourceDigest, recoveryRef),
  };
  const session = new ContextSession("inspect", { projectRoot: root, sessionId: "session-fixed", taskId: "task-fixed", engine });
  const view = await session.prepare(source);
  assert.equal(view?.integrityStatus, Integrity.SEALED);
  const receipt = session.complete({ opaque: true }, { outcome: HostOutcome.COMPLETED });
  assert.equal(receipt.outcome, HostOutcome.COMPLETED);
  assert.equal(receipt.integrityStatus, Integrity.SEALED);
  receipt.requireVerified();
  assert.equal((await session.recover(view ?? undefined)).text, "original\n");
  session.close();
  assert.equal(session.state, "closed");
});

test("path containment and receipt verification fail closed", () => {
  assert.throws(() => new ContextSource("../escape.txt", { projectRoot: "/PROJECT" }));
  const receipt = new ContextReceipt("s", "t", null, null, "failed", "unsealed");
  assert.throws(() => receipt.requireVerified(), ArtifactIntegrityError);
});

test("Agent Tools negotiates policy, records metrics, and gates mutation", async () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-agent-test-"));
  const outside = mkdtempSync(join(tmpdir(), "leanctx-ts-agent-outside-"));
  try {
    const engineBinary = fakeAgentEngine(root);
    const readonly = await AgentContext.open(root, { engineBinary, task: "inspect" });
    assert.deepEqual(readonly.capabilities, ["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"]);
    const read = await readonly.read("README.md");
    assert.equal(read.text, "ctx_read:ok");
    assert.equal(read.savedRatio, 0.6);
    assert.equal(readonly.metrics.toolCalls, 1);
    await assert.rejects(readonly.createFile("x.txt", "x"), AgentPermissionError);
    await readonly.close();

    const executing = await AgentContext.open(root, {
      engineBinary,
      permissions: new AgentPermissions({ execute: true }),
      executionPolicy: { allowedExecutables: ["git"], allowedEnv: ["SAFE"] },
    });
    assert.equal((await executing.run(["git", "status"], { env: { SAFE: "1" } })).text, "ctx_shell:ok");
    await assert.rejects(executing.run(["sh", "-c", "true"]), AgentPermissionError);
    symlinkSync(outside, join(root, "escape-link"), "dir");
    await assert.rejects(executing.run(["git", "status"], { cwd: "escape-link" }), AgentPermissionError);
    await executing.close();
    assert.deepEqual(readdirSync(root).filter((name) => name.startsWith(".leanctx-agent-")), []);
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  }
});

test("real Engine v1 lifecycle when an Engine binary is provided", {
  skip: !process.env.LEANCTX_ENGINE_BIN,
}, async () => {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const engine = new SubprocessEngineClient(process.env.LEANCTX_ENGINE_BIN);
  const source = new ContextSource("README.md", { projectRoot: root });
  const session = new ContextSession("TypeScript real Engine proof", { projectRoot: root, engine });
  const view = await session.prepare(source);
  assert.ok(view);
  const receipt = session.complete({ ok: true }, { outcome: HostOutcome.COMPLETED });
  receipt.requireVerified();
  const recovered = await session.recover(view ?? undefined);
  assert.equal(recovered.sourceDigest, view?.sourceDigest);
  assert.deepEqual(readdirSync(root).filter((name) => name.startsWith(".leanctx-sdk-")), []);
});
