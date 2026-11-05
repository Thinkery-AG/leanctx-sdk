// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  InstancePlanningRequest,
  InstanceProtocolError,
  InstanceSource,
  InstanceSourcePlanningClient,
  parseMaterialization,
  SubprocessInstanceClient,
  ValidationError,
  parseSourcePlan,
} from "../dist/index.js";
import { canonicalBytes, sha256Digest } from "../dist/protocol.js";

const content = "invoice ledger source body";
const sourceId = "source-file";
const objectRef = "file";
const request = new InstancePlanningRequest("source-task", "invoice ledger", 64);
const source = new InstanceSource({
  object_ref: objectRef,
  source_id: sourceId,
  source_type: "filesystem",
  content_digest: sha256Digest(content),
  revision: "revision-one",
  owner: "owner",
  observed_at: null,
  valid_until: null,
  classification: "Internal",
  permission: "permitted",
}, content);

function makeSource(ref, id, body) {
  return new InstanceSource({
    object_ref: ref,
    source_id: id,
    source_type: "filesystem",
    content_digest: sha256Digest(body),
    revision: "revision-one",
    owner: "owner",
    observed_at: null,
    valid_until: null,
    classification: "Internal",
    permission: "permitted",
  }, body);
}

function sourcePlan(sources = [source], planFields = {}) {
  const selections = sources.map((candidate) => ({
    source_ref: candidate.descriptor.object_ref,
    provider: candidate.descriptor.source_id,
    disposition: "selected",
    token_count: 4,
    sha256_digest: candidate.descriptor.content_digest,
    reason_codes: ["relevant"],
  }));
  const unsignedPlan = {
    schema_version: 1,
    context_plan_id: "source-plan-1",
    task_id: request.taskId,
    budget_tokens: request.budgetTokens,
    selections,
    ...planFields,
    context_plan_evaluation_v1: {
      schema_version: 1,
      evaluation_time: "2026-09-20T12:34:56Z",
    },
  };
  const plan = {
    ...unsignedPlan,
    projection_digest: sha256Digest(canonicalBytes(unsignedPlan)),
  };
  const result = {
    schema_version: 1,
    transport_version: 1,
    engine_interface_version: "1.0.0",
    plan,
  };
  const sourceBindings = [...sources]
    .sort((left, right) => Buffer.compare(
      Buffer.from(left.descriptor.object_ref, "utf8"),
      Buffer.from(right.descriptor.object_ref, "utf8"),
    ))
    .map((candidate) => candidate.descriptor);
  return {
    result,
    source_bindings: sourceBindings,
    binding_digest: sha256Digest(canonicalBytes([result, sourceBindings])),
  };
}

function defineOwn(object, key, value) {
  Object.defineProperty(object, key, {
    value,
    enumerable: true,
    writable: true,
    configurable: true,
  });
  return object;
}

function protoPlan(reseal) {
  const plan = sourcePlan();
  defineOwn(plan.result.plan, "__proto__", { marker: "extension-data" });
  const providerStats = {};
  defineOwn(providerStats, "__proto__", {
    candidates_offered: 1,
    candidates_selected: 1,
    tokens_used: 4,
  });
  plan.result.plan.provider_stats = providerStats;
  if (reseal) {
    const unsigned = { ...plan.result.plan };
    delete unsigned.projection_digest;
    plan.result.plan.projection_digest = sha256Digest(canonicalBytes(unsigned));
    plan.binding_digest = sha256Digest(canonicalBytes([plan.result, plan.source_bindings]));
  }
  return plan;
}

function responses() {
  const plan = sourcePlan();
  const materializedContent = "## leanctx-source-v1\nfile\nsource-file\n" + source.descriptor.content_digest + "\n" + content;
  return {
    plan: JSON.stringify(plan),
    materialization: JSON.stringify({
      schema_version: 1,
      transport_version: 1,
      engine_interface_version: "1.0.0",
      plan,
      materialized_digest: sha256Digest(materializedContent),
      materialized_token_count: 4,
      content: materializedContent,
    }),
  };
}

function fakeEngine(root) {
  const output = responses();
  const seen = join(root, "seen-request.json");
  const script = join(root, "engine");
  writeFileSync(script, [
    "#!" + process.execPath,
    "const fs = require('node:fs');",
    "const chunks = [];",
    "process.stdin.on('data', (chunk) => chunks.push(Buffer.from(chunk)));",
    "process.stdin.on('end', () => {",
    "  const args = process.argv.slice(2);",
    "  if (args[0] !== 'engine' || !args.includes('--json-file') || args[args.indexOf('--json-file') + 1] !== '-') process.exit(2);",
    "  fs.writeFileSync(" + JSON.stringify(seen) + ", Buffer.concat(chunks));",
    "  process.stdout.write(args.includes('context-materialize-sources') ? " + JSON.stringify(output.materialization) + " : " + JSON.stringify(output.plan) + ");",
    "});",
    "",
  ].join("\n"), { mode: 0o700 });
  chmodSync(script, 0o700);
  return script;
}

test("source planning and materialization reuse bounded stdin transport", async () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-source-plan-"));
  try {
    const client = new SubprocessInstanceClient({ instanceBinary: fakeEngine(root) });
    const planned = await client.contextPlanSources(root, request, [source]);
    assert.equal(planned.binding_digest, sourcePlan().binding_digest);
    assert.equal(planned.result.plan.context_plan_evaluation_v1.evaluation_time, "2026-09-20T12:34:56Z");

    const materialized = await client.contextMaterializeSources(
      root,
      request,
      [source],
      planned.binding_digest,
      "2026-09-20T12:34:56Z",
    );
    assert.equal(materialized.plan.binding_digest, planned.binding_digest);
    assert.equal(materialized.materialized_digest, sha256Digest(materialized.content));
    assert.equal(materialized.materialized_token_count, 4);

    const captured = JSON.parse(readFileSync(join(root, "seen-request.json"), "utf8"));
    assert.equal(captured.expected_binding_digest, planned.binding_digest);
    assert.equal(captured.planning_evaluation_time, "2026-09-20T12:34:56Z");
    assert.equal(captured.source_plan.sources[0].content, content);
    assert.equal(captured.source_plan.planning.task_id, request.taskId);

    await assert.rejects(
      client.contextMaterializeSources(
        root,
        request,
        [source],
        sha256Digest("wrong-binding"),
        "2026-09-20T12:34:56Z",
      ),
      InstanceProtocolError,
    );
    await assert.rejects(
      client.contextMaterializeSources(
        root,
        request,
        [source],
        planned.binding_digest,
        "2026-09-20T12:34:57Z",
      ),
      InstanceProtocolError,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("v2 materialization keeps the presenter capability out of argv, JSON and the request", async () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-presenter-"));
  try {
    const secret = "lcp1_" + "7e".repeat(32);
    const v1 = JSON.parse(responses().materialization);
    const grant = { presenter_ref: "presenter:" + "ab".repeat(16), expires_at_unix: 1_800_000_000 };
    const issued = JSON.stringify({ schema_version: 2, materialization: v1, presenter: { ...grant, capability: secret } });
    const renewed = JSON.stringify({ schema_version: 2, materialization: v1, presenter: grant });
    const seen = join(root, "seen.json");
    const script = join(root, "engine");
    writeFileSync(script, [
      "#!" + process.execPath,
      "const fs = require('node:fs');",
      "const chunks = [];",
      "process.stdin.on('data', (chunk) => chunks.push(Buffer.from(chunk)));",
      "process.stdin.on('end', () => {",
      "  const args = process.argv.slice(2);",
      "  if (args[1] !== 'context-materialize-sources-v2') process.exit(2);",
      "  const at = args.indexOf('--presenter-capability-file');",
      "  const file = at < 0 ? null : args[at + 1];",
      "  const shown = file === null ? null : { text: fs.readFileSync(file, 'utf8'), mode: fs.statSync(file).mode & 0o777 };",
      "  fs.writeFileSync(" + JSON.stringify(seen) + ", JSON.stringify({ args, env: process.env, shown, body: Buffer.concat(chunks).toString('utf8') }));",
      "  process.stdout.write(file === null ? " + JSON.stringify(issued) + " : " + JSON.stringify(renewed) + ");",
      "});",
      "",
    ].join("\n"), { mode: 0o700 });
    chmodSync(script, 0o700);
    const client = new SubprocessInstanceClient({ instanceBinary: script });
    const digest = sourcePlan().binding_digest;

    const first = await client.contextMaterializeSourcesV2(root, request, [source], digest, { ttlSeconds: 600 }, "2026-09-20T12:34:56Z");
    const capability = first.presenter.capability;
    assert.equal(capability.reveal(), secret);
    assert.equal(first.presenter.presenter_ref, grant.presenter_ref);
    for (const rendered of [JSON.stringify(first), String(capability), JSON.stringify({ capability })]) {
      assert.ok(!rendered.includes("7e".repeat(32)), rendered);
    }
    let captured = JSON.parse(readFileSync(seen, "utf8"));
    assert.equal(captured.shown, null);
    assert.deepEqual(JSON.parse(captured.body).presenter, { mode: "issue", ttl_seconds: 600 });

    const second = await client.contextMaterializeSourcesV2(root, request, [source], digest, capability, "2026-09-20T12:34:56Z");
    assert.equal(second.presenter.capability, null);
    captured = JSON.parse(readFileSync(seen, "utf8"));
    assert.deepEqual(captured.shown, { text: secret, mode: 0o600 });
    assert.deepEqual(JSON.parse(captured.body).presenter, { mode: "renew" });
    for (const outside of [captured.args.join(" "), JSON.stringify(captured.env), captured.body]) {
      assert.ok(!outside.includes(secret), "the capability travels in the file only");
    }
    assert.throws(() => readFileSync(captured.args[captured.args.indexOf("--presenter-capability-file") + 1]), /ENOENT/);

    // A grant that does not match the requested mode is a protocol error.
    writeFileSync(script, readFileSync(script, "utf8").replace("stdout.write(file === null", "stdout.write(file !== null"));
    await assert.rejects(
      client.contextMaterializeSourcesV2(root, request, [source], digest, { ttlSeconds: 600 }, "2026-09-20T12:34:56Z"),
      InstanceProtocolError,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a held capability is used and revoked through the capability file only", async () => {
  // The package root: a host that kept the secret in its own store wraps it
  // again through the public export, not through an internal module path.
  const { PresenterCapability } = await import("../dist/index.js");
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-presented-"));
  try {
    // Assembled at run time: the source holds no capability-shaped text.
    const secret = "lcp1_" + "7e".repeat(32);
    const capability = new PresenterCapability(secret);
    const presenterRef = "presenter:" + "ab".repeat(16);
    const recoveryId = "5e".repeat(32);
    const expanded = { schema_version: 1, presenter_ref: presenterRef, usable: true, source_ids: [sourceId], recovery_id: recoveryId, content: "ledger" };
    const revoked = { schema_version: 1, presenter_ref: presenterRef, revoked: true };
    const seen = join(root, "seen.json");
    const script = join(root, "engine");
    writeFileSync(script, [
      "#!" + process.execPath,
      "const fs = require('node:fs');",
      "const chunks = [];",
      "process.stdin.on('data', (chunk) => chunks.push(Buffer.from(chunk)));",
      "process.stdin.on('end', () => {",
      "  const args = process.argv.slice(2);",
      "  const file = args[args.indexOf('--presenter-capability-file') + 1];",
      "  const shown = { text: fs.readFileSync(file, 'utf8'), mode: fs.statSync(file).mode & 0o777 };",
      "  const body = Buffer.concat(chunks).toString('utf8');",
      "  fs.writeFileSync(" + JSON.stringify(seen) + ", JSON.stringify({ args, env: process.env, shown, body }));",
      "  if (body.includes('refuse-me')) { process.stderr.write('engine: presenter_capability_refused\\n'); process.exit(2); }",
      "  process.stdout.write(args[1] === 'context-presenter-revoke' ? " + JSON.stringify(JSON.stringify(revoked))
        + " : " + JSON.stringify(JSON.stringify(expanded)) + ");",
      "});",
      "",
    ].join("\n"), { mode: 0o700 });
    chmodSync(script, 0o700);
    const client = new SubprocessInstanceClient({ instanceBinary: script });

    const answer = await client.presentedUse(root, capability, { mode: "expand", recovery_id: recoveryId });
    assert.equal(answer.content, "ledger");
    assert.deepEqual(answer.source_ids, [sourceId]);
    let captured = JSON.parse(readFileSync(seen, "utf8"));
    assert.equal(captured.args[1], "context-presented-use");
    assert.deepEqual(JSON.parse(captured.body), { schema_version: 1, use: { mode: "expand", recovery_id: recoveryId } });

    assert.equal((await client.revokePresenter(root, capability)).revoked, true);
    captured = JSON.parse(readFileSync(seen, "utf8"));
    assert.equal(captured.args[1], "context-presenter-revoke");
    assert.deepEqual(captured.shown, { text: secret, mode: 0o600 });
    for (const outside of [captured.args.join(" "), JSON.stringify(captured.env), captured.body]) {
      assert.ok(!outside.includes(secret), "the capability travels in the file only");
    }
    assert.throws(() => readFileSync(captured.args[captured.args.indexOf("--presenter-capability-file") + 1]), /ENOENT/);

    // An answer to another question, the Engine's one refusal, and input
    // that never reaches the Engine.
    await assert.rejects(client.presentedUse(root, capability, { mode: "check", content: "ledger" }), InstanceProtocolError);
    await assert.rejects(
      client.presentedUse(root, capability, { mode: "check", content: "refuse-me" }),
      /presenter_capability_refused/,
    );
    await assert.rejects(client.presentedUse(root, capability, { mode: "expand", recovery_id: "../x" }), ValidationError);

    // A repeated id must not stand in for a source the answer leaves out, and an
    // answer for other sources than the authorized ones is not an answer to the request.
    const { parsePresentedUse } = await import("../dist/planning.js");
    assert.throws(
      () => parsePresentedUse({ ...expanded, source_ids: [sourceId, sourceId] }, { mode: "expand", recovery_id: recoveryId }, capability),
      ValidationError,
    );
    await assert.rejects(
      client.presentedUse(root, capability, { mode: "expand", recovery_id: recoveryId }, [sourceId, "source-other"]),
      InstanceProtocolError,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("source bindings accept canonical UTF-8 ordering for non-ASCII references", () => {
  const nonBmp = makeSource("\u{10000}", "source-non-bmp", "non-BMP source body");
  const privateUse = makeSource("\uE000", "source-private-use", "private-use source body");
  const parsed = parseSourcePlan(
    JSON.stringify(sourcePlan([nonBmp, privateUse])),
    request,
    [nonBmp, privateUse],
  );
  assert.deepEqual(
    parsed.source_bindings.map((binding) => binding.object_ref),
    ["\uE000", "\u{10000}"],
  );
});

test("source response rejects non-integer protocol numbers", () => {
  const rich = sourcePlan([source], {
    provider_stats: {
      [sourceId]: {
        candidates_offered: 1,
        candidates_selected: 1,
        tokens_used: 4,
      },
    },
    evidence: [{
      schema_version: 1,
      kind: "ProviderReceipt",
      uri: "provider:test",
      digest: "sha256:" + "0".repeat(64),
      signature_status: "NotSigned",
    }],
  });
  const raw = JSON.stringify(rich);
  const mutations = [
    (value) => value.replace('"schema_version":1,"transport_version"', '"schema_version":1.0,"transport_version"'),
    (value) => value.replace('"transport_version":1,"engine_interface_version"', '"transport_version":1e0,"engine_interface_version"'),
    (value) => value.replace('"schema_version":1,"context_plan_id"', '"schema_version":1.0,"context_plan_id"'),
    (value) => value.replace('"budget_tokens":64,"selections"', '"budget_tokens":64.0,"selections"'),
    (value) => value.replace('"token_count":4,"sha256_digest"', '"token_count":4e0,"sha256_digest"'),
    (value) => value.replace('"candidates_offered":1,"candidates_selected"', '"candidates_offered":1.0,"candidates_selected"'),
    (value) => value.replace('"candidates_selected":1,"tokens_used"', '"candidates_selected":1e0,"tokens_used"'),
    (value) => value.replace('"tokens_used":4}', '"tokens_used":4.0}'),
    (value) => value.replace('"evidence":[{"schema_version":1,"kind"', '"evidence":[{"schema_version":1e0,"kind"'),
  ];
  for (const mutate of mutations) {
    const mutated = mutate(raw);
    assert.notEqual(mutated, raw);
    assert.throws(() => parseSourcePlan(mutated, request, [source]), ValidationError);
  }
});

test("materialization rejects non-integer envelope numbers", () => {
  const materialization = responses().materialization;
  for (const mutated of [
    materialization.replace('"schema_version":1,"transport_version"', '"schema_version":1.0,"transport_version"'),
    materialization.replace('"transport_version":1,"engine_interface_version"', '"transport_version":1e0,"engine_interface_version"'),
    materialization.replace('"materialized_token_count":4,"content"', '"materialized_token_count":4.0,"content"'),
  ]) {
    assert.throws(() => parseMaterialization(mutated, request, [source]), ValidationError);
  }
});

test("extension numeric syntax remains outside protocol integer paths", () => {
  const raw = JSON.stringify(sourcePlan()).replace(
    '"context_plan_evaluation_v1":{"schema_version":1,',
    '"context_plan_evaluation_v1":{"schema_version":1.0,',
  );
  assert.notEqual(raw, JSON.stringify(sourcePlan()));
  assert.equal(
    parseSourcePlan(raw, request, [source]).result.plan.context_plan_evaluation_v1.schema_version,
    1,
  );
});

test("source plan preserves __proto__ data and rejects stale hashes", () => {
  const stale = protoPlan(false);
  assert.equal(Object.prototype.leanctxProtoPolluted, undefined);
  assert.throws(
    () => parseSourcePlan(JSON.stringify(stale), request, [source]),
    ValidationError,
  );

  const parsed = parseSourcePlan(JSON.stringify(protoPlan(true)), request, [source]);
  assert.equal(Object.prototype.leanctxProtoPolluted, undefined);
  assert.equal(Object.prototype.hasOwnProperty.call(parsed.result.plan, "__proto__"), true);
  assert.equal(parsed.result.plan["__proto__"].marker, "extension-data");
  assert.equal(Object.prototype.hasOwnProperty.call(parsed.result.plan.provider_stats, "__proto__"), true);
  assert.equal(parsed.result.plan.provider_stats["__proto__"].candidates_offered, 1);
});

test("source input validation fails before process launch", async () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-source-validation-"));
  try {
    const binary = join(root, "never-started");
    writeFileSync(binary, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
    chmodSync(binary, 0o700);
    const client = new InstanceSourcePlanningClient(
      new SubprocessInstanceClient({ instanceBinary: binary }),
    );
    await assert.rejects(
      client.contextPlanSources(root, request, [source, source]),
      ValidationError,
    );
    await assert.rejects(
      client.contextMaterializeSources(root, request, [source], "bad"),
      ValidationError,
    );
    await assert.rejects(
      client.contextMaterializeSources(
        root,
        request,
        [source],
        sha256Digest("expected"),
        "0000-09-20T12:34:56Z",
      ),
      ValidationError,
    );
    assert.equal(readFileSync(binary, "utf8"), "#!/bin/sh\nexit 0\n");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("source descriptors snapshot caller-owned objects", () => {
  const descriptorInput = {
    object_ref: objectRef,
    source_id: sourceId,
    source_type: "filesystem",
    content_digest: source.descriptor.content_digest,
  };
  const detached = new InstanceSource(descriptorInput, content);
  descriptorInput.object_ref = "mutated";
  const wire = detached.toDict();
  wire.descriptor.object_ref = "mutated-wire";
  assert.equal(detached.descriptor.object_ref, objectRef);
});

test("source response tampering fails closed", async () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-source-tamper-"));
  try {
    const plan = sourcePlan();
    const tampered = { ...plan, binding_digest: sha256Digest("tampered") };
    const script = join(root, "engine");
    writeFileSync(script, [
      "#!" + process.execPath,
      "process.stdin.resume();",
      "process.stdin.on('end', () => process.stdout.write(" + JSON.stringify(JSON.stringify(tampered)) + "));",
      "",
    ].join("\n"), { mode: 0o700 });
    chmodSync(script, 0o700);
    const client = new InstanceSourcePlanningClient(
      new SubprocessInstanceClient({ instanceBinary: script }),
    );
    await assert.rejects(
      client.contextPlanSources(root, request, [source]),
      InstanceProtocolError,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
