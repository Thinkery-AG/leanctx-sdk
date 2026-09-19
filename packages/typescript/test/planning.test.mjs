// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  EnginePlanningRequest,
  EngineProtocolError,
  EngineSource,
  EngineSourcePlanningClient,
  parseMaterialization,
  SubprocessEngineClient,
  ValidationError,
  parseSourcePlan,
} from "../dist/index.js";
import { canonicalBytes, sha256Digest } from "../dist/protocol.js";

const content = "invoice ledger source body";
const sourceId = "source-file";
const objectRef = "file";
const request = new EnginePlanningRequest("source-task", "invoice ledger", 64);
const source = new EngineSource({
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
  return new EngineSource({
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
    const client = new SubprocessEngineClient({ engineBinary: fakeEngine(root) });
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
      EngineProtocolError,
    );
    await assert.rejects(
      client.contextMaterializeSources(
        root,
        request,
        [source],
        planned.binding_digest,
        "2026-09-20T12:34:57Z",
      ),
      EngineProtocolError,
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

test("source input validation fails before process launch", async () => {
  const root = mkdtempSync(join(tmpdir(), "leanctx-ts-source-validation-"));
  try {
    const binary = join(root, "never-started");
    writeFileSync(binary, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
    chmodSync(binary, 0o700);
    const client = new EngineSourcePlanningClient(
      new SubprocessEngineClient({ engineBinary: binary }),
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
  const detached = new EngineSource(descriptorInput, content);
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
    const client = new EngineSourcePlanningClient(
      new SubprocessEngineClient({ engineBinary: script }),
    );
    await assert.rejects(
      client.contextPlanSources(root, request, [source]),
      EngineProtocolError,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
