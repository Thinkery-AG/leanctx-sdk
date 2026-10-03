// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { EngineProtocolError, SubprocessEngineClient } from "../dist/index.js";
import { admitEgress, parseEgressAdmission } from "../dist/preview.js";

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

test("every invalid case is rejected", () => {
  for (const name of manifest.invalid) {
    assert.throws(() => parseEgressAdmission(load("invalid", name)), EngineProtocolError, name);
  }
});

const engineBinary = process.env.LEANCTX_ENGINE_BINARY;
test("live Engine: credential masked, restricted withheld, marking classified", { skip: !engineBinary }, async () => {
  const engine = new SubprocessEngineClient({ engineBinary });
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
  assert.ok(!JSON.stringify(restricted.body).includes("customer list"));
  assert.ok(restricted.receipt.decisions[0].reasonCodes.includes("destination.remote_restricted"));

  const marked = await admit("Classification: Confidential\nboard minutes");
  assert.equal(marked.disposition, "forward");
  assert.equal(marked.classification, "confidential");
  assert.equal(marked.receipt.destination.locality, "remote");
});
