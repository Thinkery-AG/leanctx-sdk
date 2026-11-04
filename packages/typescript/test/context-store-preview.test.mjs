// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { INSTANCE_BINARY } from "./instance-env.mjs";

import { InstanceProtocolError, SubprocessInstanceClient, ValidationError } from "../dist/index.js";
import { parsePolicyEvidence, parseTaskLineage, readPolicyEvidence, readTaskLineage } from "../dist/preview.js";

const FIXTURES = new URL("../../../fixtures/context-store-preview-v1/", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("manifest.json", FIXTURES), "utf8"));
const load = (kind, validity, name) => JSON.parse(readFileSync(new URL(`${kind}/${validity}/${name}.json`, FIXTURES), "utf8"));
const parsers = { evidence: parsePolicyEvidence, lineage: parseTaskLineage };

test("every valid document parses and every invalid one is rejected", () => {
  for (const [kind, parse] of Object.entries(parsers)) {
    for (const name of manifest.documents[kind].valid) parse(load(kind, "valid", name));
    for (const name of manifest.documents[kind].invalid) {
      assert.throws(() => parse(load(kind, "invalid", name)), InstanceProtocolError, `${kind}/${name}`);
    }
  }
});

test("unmeasured never reads as measured and gaps name missing links", () => {
  const evidence = parsePolicyEvidence(load("evidence", "valid", "rich"));
  assert.equal(evidence.records[1].quality.measured, false);
  assert.equal(evidence.records[1].quality.retained, undefined);
  assert.equal(evidence.records[1].security.regressions, undefined);
  assert.deepEqual([evidence.records[0].security.measured, evidence.records[0].security.regressions], [true, 0]);
  const complete = parseTaskLineage(load("lineage", "valid", "complete"));
  assert.equal(complete.isComplete, true);
  const gapped = parseTaskLineage(load("lineage", "valid", "gapped"));
  assert.equal(gapped.isComplete, false);
  assert.equal(gapped.deliveries[0].summary, null);
});

test("blank identifiers are refused before the Engine runs", async () => {
  await assert.rejects(readTaskLineage(null, "/tmp", " "), ValidationError);
});

test("a version is written as an integer, in the envelope and in its document", async () => {
  // The answer as bytes: `1.0` and `1e0` only exist before JSON is parsed.
  const reads = {
    evidence: (engine) => readPolicyEvidence(engine, "/tmp", { projectId: "p" }),
    lineage: (engine) => readTaskLineage(engine, "/tmp", "task", { projectId: "p" }),
  };
  const fixtures = { evidence: "rich", lineage: "complete" };
  for (const [kind, read] of Object.entries(reads)) {
    const document = readFileSync(new URL(`${kind}/valid/${fixtures[kind]}.json`, FIXTURES), "utf8").trim();
    assert.ok(document.includes('"schema_version": 1') || document.includes('"schema_version":1'), kind);
    const envelope = (schema, transport, body) =>
      `{"schema_version":${schema},"transport_version":${transport},"engine_interface_version":"1.0.0","${kind}":${body}}`;
    const answering = (raw) => ({ contextStoreRead: async () => Buffer.from(raw, "utf8") });
    // The integer itself passes everywhere.
    await read(answering(envelope("1", "1", document)));
    for (const wrong of ["1.0", "1e0", "1.0000000000000000001", "true"]) {
      const inDocument = document.replace(/"schema_version":\s*1/, `"schema_version":${wrong}`);
      assert.notEqual(inDocument, document, `${kind} document ${wrong}`);
      for (const [where, raw] of [
        ["envelope schema_version", envelope(wrong, "1", document)],
        ["envelope transport_version", envelope("1", wrong, document)],
        ["document schema_version", envelope("1", "1", inDocument)],
      ]) {
        await assert.rejects(read(answering(raw)), InstanceProtocolError, `${kind}: ${where} ${wrong}`);
      }
    }
  }
});

const instanceBinary = INSTANCE_BINARY;
test("live Engine: fresh scope has no evidence, unknown task reports gaps", { skip: !instanceBinary }, async () => {
  const engine = new SubprocessInstanceClient({ instanceBinary });
  // Private Engine storage refuses symlinked ancestors (macOS /var).
  const root = realpathSync(mkdtempSync(join(tmpdir(), "leanctx-store-")));
  const evidence = await readPolicyEvidence(engine, root, { projectId: "sdk-preview-fresh" });
  assert.deepEqual([evidence.records.length, evidence.evaluations.length], [0, 0]);
  const lineage = await readTaskLineage(engine, root, "sdk-preview-unknown-task", { projectId: "sdk-preview-fresh", tenantId: "tenant-a" });
  assert.equal(lineage.outcome, "unknown");
  assert.ok(lineage.gaps.includes("no_plan_recorded"));
  assert.ok(lineage.scope.includes("tenant-a"));
});
