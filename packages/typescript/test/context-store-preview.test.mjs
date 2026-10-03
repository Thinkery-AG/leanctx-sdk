// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { EngineProtocolError, SubprocessEngineClient, ValidationError } from "../dist/index.js";
import { parsePolicyEvidence, parseTaskLineage, readPolicyEvidence, readTaskLineage } from "../dist/preview.js";

const FIXTURES = new URL("../../../fixtures/context-store-preview-v1/", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("manifest.json", FIXTURES), "utf8"));
const load = (kind, validity, name) => JSON.parse(readFileSync(new URL(`${kind}/${validity}/${name}.json`, FIXTURES), "utf8"));
const parsers = { evidence: parsePolicyEvidence, lineage: parseTaskLineage };

test("every valid document parses and every invalid one is rejected", () => {
  for (const [kind, parse] of Object.entries(parsers)) {
    for (const name of manifest.documents[kind].valid) parse(load(kind, "valid", name));
    for (const name of manifest.documents[kind].invalid) {
      assert.throws(() => parse(load(kind, "invalid", name)), EngineProtocolError, `${kind}/${name}`);
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

const engineBinary = process.env.LEANCTX_ENGINE_BINARY;
test("live Engine: fresh scope has no evidence, unknown task reports gaps", { skip: !engineBinary }, async () => {
  const engine = new SubprocessEngineClient({ engineBinary });
  // Private Engine storage refuses symlinked ancestors (macOS /var).
  const root = realpathSync(mkdtempSync(join(tmpdir(), "leanctx-store-")));
  const evidence = await readPolicyEvidence(engine, root, { projectId: "sdk-preview-fresh" });
  assert.deepEqual([evidence.records.length, evidence.evaluations.length], [0, 0]);
  const lineage = await readTaskLineage(engine, root, "sdk-preview-unknown-task", { projectId: "sdk-preview-fresh", tenantId: "tenant-a" });
  assert.equal(lineage.outcome, "unknown");
  assert.ok(lineage.gaps.includes("no_plan_recorded"));
  assert.ok(lineage.scope.includes("tenant-a"));
});
