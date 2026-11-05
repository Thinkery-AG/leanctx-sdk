// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
// Source union retains private refusal/preparation/report cases; context-store tests are separate.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { INSTANCE_BINARY } from "./instance-env.mjs";

import { InstanceExecutionError, InstanceProtocolError, SubprocessInstanceClient } from "../dist/index.js";
import { admitEgress, keyedObjectDigest, receiptKeyId, parseEgressAdmission, parseEgressHostReport, parseEgressPreparation, prepareEgress, reportEgress } from "../dist/preview.js";

const FIXTURES = new URL("../../../fixtures/gateway-preview-v1/", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("manifest.json", FIXTURES), "utf8"));
const load = (kind, name) => JSON.parse(readFileSync(new URL(`${kind}/${name}.json`, FIXTURES), "utf8"));

test("real Engine responses parse with their recorded shape", () => {
  for (const [name, expected] of Object.entries(manifest.valid)) {
    const admission = parseEgressAdmission(load("valid", name));
    const receipt = admission.receipt;
    assert.equal(admission.disposition, expected.disposition, name);
    assert.equal(admission.classification, expected.classification, name);
    assert.equal(receipt.outcome, expected.outcome, name);
    assert.equal(receipt.decisions.length, expected.decisions, name);
    assert.equal(receipt.decisions.filter((d) => d.disposition === "deny").length, expected.denied, name);
    assert.equal(receipt.security.redactions, expected.redactions, name);
    assert.equal(receipt.signals.length, expected.signals, name);
    assert.equal(admission.maySend, true, name);
    assert.equal(receipt.principal.kind, "unknown", name);
  }
});

test("receipt v2 objects are keyed like the Engine keys them", () => {
  const vector = manifest.keyed_v2;
  const key = Buffer.from(vector.key_hex, "hex");
  const plain = `sha256:${createHash("sha256").update(vector.object_text, "utf8").digest("hex")}`;
  assert.equal(receiptKeyId(key), vector.key_id);
  assert.equal(keyedObjectDigest(key, plain), vector.keyed_object);
  const receipt = parseEgressAdmission(load("valid", vector.fixture)).receipt;
  assert.deepEqual([receipt.schemaVersion, receipt.objectDigest.algorithm, receipt.objectDigest.keyId], [2, "hmac-sha256", vector.key_id]);
  const objects = receipt.decisions.map((d) => d.object);
  assert.deepEqual(objects, [vector.keyed_object]);
  assert.ok(!objects.includes(plain), "a plain-SHA-256 guess confirms nothing");
  assert.notEqual(keyedObjectDigest(Buffer.alloc(32), plain), vector.keyed_object);
  const v1 = parseEgressAdmission(load("valid", "masked")).receipt;
  assert.deepEqual([v1.schemaVersion, v1.objectDigest], [1, undefined]);
});

test("every invalid case is rejected", () => {
  for (const name of manifest.invalid) {
    assert.throws(() => parseEgressAdmission(load("invalid", name)), InstanceProtocolError, name);
  }
});

test("prepare preserves exact text and report stays HostReported", async () => {
  const preparedBody = '{ "model" : "m" }';
  const finalContext = `sha256:${createHash("sha256").update(Buffer.from(preparedBody, "utf8")).digest("hex")}`;
  const decision = {
    schema_version: 1, disposition: "forward", body: { model: "m" }, classification: "internal",
    receipt: {
      schema_version: 1, receipt_id: "egress-test", mode: "governed", principal: { kind: "unknown" },
      destination: { provider: "openai", locality: "remote" },
      sources: { inspected: 0, permitted: 0, selected: 0, blocked: 0 },
      security: { redactions: 0, blocked_objects: 0, quarantined_objects: 0, injection_signals: 0, incomplete_coverage: 0 },
      tokens: { original: 0, delivered: 0 }, outcome: "delivered", duration_us: 0, final_context: finalContext,
    },
  };
  const preparation = { schema_version: 2, decision, prepared_body: preparedBody, admission_receipt_digest: `sha256:${"b".repeat(64)}` };
  const parsed = parseEgressPreparation(preparation);
  assert.equal(parsed.preparedBody, preparedBody);
  assert.equal(parsed.maySend, true);
  assert.equal(parsed.admissionReceiptDigest, preparation.admission_receipt_digest);
  assert.throws(() => parseEgressPreparation({ ...preparation, prepared_body: '{"model":"m"}' }), InstanceProtocolError);
  assert.equal(parseEgressPreparation({ schema_version: 2, decision: { schema_version: 1, disposition: "refused", refusal: "withheld" } }).maySend, false);
  assert.equal(parseEgressPreparation({ schema_version: 2,
    decision: { schema_version: 1, disposition: "forward", body: { model: "m" } }, prepared_body: preparedBody }).maySend, true);
  assert.equal(parseEgressPreparation({ ...preparation,
    decision: { ...decision, disposition: "rewritten" } }).maySend, true);
  for (const disposition of ["forward", "rewritten"]) {
    for (const outcome of ["withheld", "failed"]) {
      const receipt = { ...decision.receipt, outcome };
      delete receipt.final_context;
      assert.throws(() => parseEgressPreparation({ ...preparation,
        decision: { ...decision, disposition, receipt } }), InstanceProtocolError);
    }
  }
  assert.throws(() => parseEgressPreparation({ schema_version: 2, decision: {
    schema_version: 1, disposition: "refused", refusal: "withheld", receipt: decision.receipt,
  } }), InstanceProtocolError);

  const calls = [];
  const engine = {
    async egressPrepare(...args) { calls.push("egress-prepare"); return Buffer.from(JSON.stringify(preparation)); },
    async egressReport(_root, payload) {
      calls.push("egress-report");
      const request = JSON.parse(payload.toString("utf8"));
      const observation = { ...request, observer: "host_reported" };
      const canonical = JSON.stringify(Object.fromEntries(Object.keys(observation).sort().map((key) => [key, observation[key]])));
      const observationDigest = `sha256:${createHash("sha256").update(Buffer.from(canonical, "utf8")).digest("hex")}`;
      return Buffer.from(JSON.stringify({ schema_version: 1, observation_digest: observationDigest, observation }));
    },
  };
  const prepared = await prepareEgress(engine, ".", { provider: "openai", upstreamBase: "https://api.openai.com", body: { model: "m" } });
  assert.equal(prepared.preparedBody, preparedBody);
  const report = await reportEgress(engine, ".", { admission_receipt_digest: preparation.admission_receipt_digest,
    attempt: 2, transport: "http", result: "no_response_observed", failure: "timeout" });
  assert.deepEqual(calls, ["egress-prepare", "egress-report"]);
  assert.equal(report.observation.result, "no_response_observed");
  assert.equal(report.observation.observer, "host_reported");
  assert.throws(() => parseEgressHostReport({ schema_version: 1, observation_digest: `sha256:${"a".repeat(64)}`,
    observation: { schema_version: 1, admission_receipt_digest: preparation.admission_receipt_digest,
      attempt: 2, transport: "http", result: "no_response_observed", failure: "timeout", observer: "engine_observed" } },
  { schema_version: 1, admission_receipt_digest: preparation.admission_receipt_digest, attempt: 2,
    transport: "http", result: "no_response_observed", failure: "timeout" }), InstanceProtocolError);
});

const instanceBinary = INSTANCE_BINARY;
test("live Engine: credential masked, restricted withheld, marking classified", { skip: !instanceBinary }, async () => {
  const engine = new SubprocessInstanceClient({ instanceBinary });
  const root = mkdtempSync(join(tmpdir(), "leanctx-gateway-"));
  const admit = (text, provider = "openai", upstreamBase = "https://api.openai.com") =>
    admitEgress(engine, root, { provider, upstreamBase, body: { model: "m", temperature: 0.2, messages: [{ role: "user", content: text }] } });

  const credential = "AKIA" + "Q3EGRZ7LIVEX4KEY";
  const masked = await admit(`deploy fails with ${credential}`);
  assert.equal(masked.disposition, "rewritten");
  assert.ok(!JSON.stringify(masked.body).includes(credential));
  assert.equal(masked.body.temperature, 0.2);
  assert.equal(masked.receipt.security.redactions, 1);

  const restricted = await admit("Classification: Secret\nroot cause and customer list", "anthropic", "https://api.anthropic.com");
  assert.equal(restricted.classification, "restricted");
  assert.equal(restricted.receipt.outcome, "withheld");
  assert.equal(restricted.disposition, "refused");
  assert.equal(restricted.maySend, false);
  assert.equal(restricted.body, null);
  assert.ok(restricted.receipt.decisions[0].reasonCodes.includes("destination.remote_restricted"));

  const marked = await admit("Classification: Confidential\nboard minutes");
  assert.equal(marked.disposition, "forward");
  assert.equal(marked.classification, "confidential");
  assert.equal(marked.receipt.destination.locality, "remote");

  const model = "host-selected-model-2026";
  const preparation = await prepareEgress(engine, root, {
    provider: "openai", upstreamBase: "https://api.openai.com",
    body: { model, messages: [{ role: "user", content: `deploy fails with ${credential}` }] },
  });
  assert.equal(preparation.maySend, true);
  assert.ok(preparation.preparedBody);
  assert.ok(preparation.admissionReceiptDigest);
  const preparedBytes = Buffer.from(preparation.preparedBody, "utf8");
  const prepared = JSON.parse(preparation.preparedBody);
  assert.equal(prepared.model, model);
  assert.ok(!preparation.preparedBody.includes(credential));
  assert.equal(preparation.decision.receipt.security.redactions, 1);
  assert.equal(preparation.decision.receipt.destination.provider, "openai");
  assert.equal(preparation.decision.receipt.destination.model, model);
  assert.equal(preparation.decision.receipt.finalContext,
    `sha256:${createHash("sha256").update(preparedBytes).digest("hex")}`);

  const reportRequest = {
    admission_receipt_digest: preparation.admissionReceiptDigest,
    attempt: 1, transport: "http", result: "no_response_observed", failure: "timeout",
  };
  const first = await reportEgress(engine, root, reportRequest);
  const replay = await reportEgress(engine, root, reportRequest);
  assert.equal(first.observation.observer, "host_reported");
  assert.equal(first.observation.result, "no_response_observed");
  assert.equal(first.observation.failure, "timeout");
  assert.equal(first.observationDigest, replay.observationDigest);

  await assert.rejects(() => reportEgress(engine, root, {
    admission_receipt_digest: preparation.admissionReceiptDigest,
    attempt: 1, transport: "http", result: "response_observed", http_status: 503,
  }), error => error instanceof InstanceExecutionError && /egress_observation_unavailable/.test(error.message));
});
