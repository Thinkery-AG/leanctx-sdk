// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import {
  EngineProtocolError,
  EnterpriseEngineClient,
  ValidationError,
} from "../dist/index.js";
import { canonicalBytes, sha256Digest } from "../dist/protocol.js";

const TENANT_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_TENANT_ID = "33333333-3333-4333-8333-333333333333";
const TASK_ID = "enterprise-task";
const TEST_CREDENTIAL = "credential-test";
const RECEIPT_DIGEST = sha256Digest("original receipt");
const DECISION_DIGEST = sha256Digest("context decision");
const PREVIOUS_RECEIPT_ID = sha256Digest("previous receipt id");
const SIGNALS = [
  { signal_type: "human_acceptance", value: { boolean: true } },
  { signal_type: "tests_passing", value: { count: 3 } },
  { signal_type: "correction", value: "unknown" },
];

function receiptDocument({
  taskId = TASK_ID,
  acceptance = "accepted",
  previousReceiptId = PREVIOUS_RECEIPT_ID,
  decisionDigest = DECISION_DIGEST,
  signerKeyId = "fixture-only",
} = {}) {
  const document = {
    schema_version: 1,
    chain: { previous_receipt_id: previousReceiptId },
    lineage: { task_id: taskId },
    status: "succeeded",
    values: [],
    outcome: { state: acceptance },
    issued_at: "2026-09-20T00:00:00Z",
    signer: {
      algorithm: "ed25519",
      key_id: signerKeyId,
      key_admission: "external_trust_store",
    },
    evidence_refs: [{
      kind: "runtime",
      digest: decisionDigest,
      uri: "artifact://execution/evidence/" + decisionDigest.slice(7),
    }],
  };
  document.receipt_id = sha256Digest(canonicalBytes(document));
  document.signature = "fixture-only-not-a-trusted-signature";
  return canonicalBytes(document).toString("utf8");
}

function response({
  tenantId = TENANT_ID,
  acceptance = "accepted",
  alreadyRecorded = false,
  document = receiptDocument({ acceptance }),
  receiptDigest = sha256Digest(document),
  originalReceiptDigest = RECEIPT_DIGEST,
} = {}) {
  const parsed = JSON.parse(document);
  return {
    schema_version: 1,
    tenant_id: tenantId,
    outcome: {
      schema_version: 1,
      receipt_id: parsed.receipt_id,
      receipt_digest: receiptDigest,
      original_receipt_digest: originalReceiptDigest,
      acceptance,
      already_recorded: alreadyRecorded,
      receipt_document_json: document,
    },
  };
}

async function loopback(body) {
  const requests = [];
  const server = createServer((requestMessage, responseMessage) => {
    const chunks = [];
    requestMessage.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    requestMessage.on("end", () => {
      requests.push({
        method: requestMessage.method,
        url: requestMessage.url,
        authorization: requestMessage.headers.authorization,
        contentType: requestMessage.headers["content-type"],
        body: Buffer.concat(chunks),
      });
      const payload = typeof body === "function" ? body() : body;
      const bytes = Buffer.isBuffer(payload) ? payload : Buffer.from(payload, "utf8");
      responseMessage.writeHead(200, {
        "content-type": "application/json",
        "content-length": bytes.byteLength,
      });
      responseMessage.end(bytes);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.equal(typeof address, "object");
  return {
    server,
    requests,
    baseUrl: "http://127.0.0.1:" + address.port + "/",
  };
}

function client(baseUrl, tenantId = TENANT_ID) {
  return new EnterpriseEngineClient(baseUrl, TEST_CREDENTIAL, tenantId, {
    allowLoopbackHttp: true,
    timeout: 2,
  });
}

async function close(server) {
  await new Promise((resolve) => server.close(resolve));
}

async function expectResponseError(payload, label) {
  const server = await loopback(payload);
  try {
    await assert.rejects(
      () => client(server.baseUrl).contextOutcome(TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, SIGNALS),
      (error) => error instanceof EngineProtocolError,
      label,
    );
    assert.equal(server.requests.length, 1);
  } finally {
    await close(server.server);
  }
}

function rebindDocument(outcome, document, recomputeIdentity = true) {
  outcome.receipt_document_json = document;
  outcome.receipt_digest = sha256Digest(document);
  if (recomputeIdentity) outcome.receipt_id = JSON.parse(document).receipt_id;
}

test("Enterprise contextOutcome preserves exact response bytes and server-owned fields", async () => {
  const fixture = response();
  const server = await loopback(JSON.stringify(fixture));
  try {
    const result = await client(server.baseUrl).contextOutcome(
      TASK_ID,
      RECEIPT_DIGEST,
      DECISION_DIGEST,
      SIGNALS,
    );
    assert.deepEqual(Object.keys(result).sort(), ["outcome", "schema_version", "tenant_id"]);
    assert.equal(result.schema_version, 1);
    assert.equal(result.tenant_id, TENANT_ID);
    assert.equal(result.outcome.receipt_document_json, fixture.outcome.receipt_document_json);
    assert.equal(result.outcome.receipt_digest, sha256Digest(fixture.outcome.receipt_document_json));
    assert.equal(server.requests.length, 1);
    assert.equal(server.requests[0].method, "POST");
    assert.equal(server.requests[0].url, "/v1/engine/context-outcome");
    assert.equal(server.requests[0].authorization, "Bearer " + TEST_CREDENTIAL);
    assert.equal(server.requests[0].contentType, "application/json");
    assert.deepEqual(JSON.parse(server.requests[0].body.toString("utf8")), {
      task_id: TASK_ID,
      receipt_digest: RECEIPT_DIGEST,
      context_decision_digest: DECISION_DIGEST,
      signals: SIGNALS,
    });
    assert.deepEqual(
      server.requests[0].body,
      canonicalBytes({
        task_id: TASK_ID,
        receipt_digest: RECEIPT_DIGEST,
        context_decision_digest: DECISION_DIGEST,
        signals: SIGNALS,
      }),
    );
  } finally {
    await close(server.server);
  }
});

test("Enterprise contextOutcome accepts accepted, rejected, and already-recorded outcomes", async () => {
  for (const [acceptance, alreadyRecorded] of [
    ["accepted", false],
    ["rejected", false],
    ["accepted", true],
  ]) {
    const fixture = response({ acceptance, alreadyRecorded });
    const server = await loopback(JSON.stringify(fixture));
    try {
      const result = await client(server.baseUrl).contextOutcome(
        TASK_ID,
        RECEIPT_DIGEST,
        DECISION_DIGEST,
        SIGNALS,
      );
      assert.equal(result.outcome.acceptance, acceptance);
      assert.equal(result.outcome.already_recorded, alreadyRecorded);
      assert.equal(result.outcome.receipt_document_json, fixture.outcome.receipt_document_json);
    } finally {
      await close(server.server);
    }
  }
});

test("Enterprise contextOutcome rejects invalid request signals before HTTP", async () => {
  const server = await loopback(JSON.stringify(response()));
  const cases = [
    ["empty task", "", RECEIPT_DIGEST, DECISION_DIGEST, SIGNALS],
    ["DEL task", TASK_ID + String.fromCharCode(0x7f), RECEIPT_DIGEST, DECISION_DIGEST, SIGNALS],
    ["C1 task", TASK_ID + String.fromCharCode(0x85), RECEIPT_DIGEST, DECISION_DIGEST, SIGNALS],
    ["invalid receipt digest", TASK_ID, "not-a-digest", DECISION_DIGEST, SIGNALS],
    ["invalid decision digest", TASK_ID, RECEIPT_DIGEST, "not-a-digest", SIGNALS],
    ["unknown signal", TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, [{ signal_type: "agent_completion", value: { boolean: true } }]],
    ["boolean type", TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, [{ signal_type: "human_acceptance", value: { boolean: 1 } }]],
    ["count type", TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, [{ signal_type: "tests_passing", value: { count: "3" } }]],
    ["count bound", TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, [{ signal_type: "tests_passing", value: { count: 4294967296 } }]],
    ["signal shape", TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, [{ signal_type: "tests_passing", value: { count: 1, extra: true } }]],
    ["empty signals", TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, []],
    ["nil signals", TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, null],
    ["too many signals", TASK_ID, RECEIPT_DIGEST, DECISION_DIGEST, Array.from({ length: 17 }, () => ({ signal_type: "correction", value: "unknown" }))],
  ];
  try {
    for (const [label, taskId, receiptDigest, decisionDigest, signals] of cases) {
      await assert.rejects(
        () => client(server.baseUrl).contextOutcome(taskId, receiptDigest, decisionDigest, signals),
        (error) => error instanceof ValidationError,
        label,
      );
    }
    assert.equal(server.requests.length, 0);
  } finally {
    await close(server.server);
  }
});

test("Enterprise contextOutcome rejects strict envelope and receipt joins", async (t) => {
  const cases = [
    ["foreign tenant", () => {
      const value = response({ tenantId: OTHER_TENANT_ID });
      return JSON.stringify(value);
    }],
    ["unknown outer field", () => {
      const value = response();
      value.extra = true;
      return JSON.stringify(value);
    }],
    ["nested schema", () => {
      const value = response();
      value.outcome.schema_version = 2;
      return JSON.stringify(value);
    }],
    ["original receipt", () => {
      const value = response();
      value.outcome.original_receipt_digest = DECISION_DIGEST;
      return JSON.stringify(value);
    }],
    ["receipt digest", () => {
      const value = response();
      value.outcome.receipt_digest = RECEIPT_DIGEST;
      return JSON.stringify(value);
    }],
    ["receipt identity", () => {
      const value = response();
      const document = value.outcome.receipt_document_json.replace(
        "2026-09-20T00:00:00Z",
        "2026-09-21T00:00:00Z",
      );
      rebindDocument(value.outcome, document, false);
      return JSON.stringify(value);
    }],
    ["noncanonical document", () => {
      const value = response();
      rebindDocument(value.outcome, " " + value.outcome.receipt_document_json, false);
      return JSON.stringify(value);
    }],
    ["C1 document", () => JSON.stringify(response({
      document: receiptDocument({ signerKeyId: "fixture-only" + String.fromCharCode(0x85) }),
    }))],
    ["wrong task", () => {
      const value = response({ document: receiptDocument({ taskId: "other-task" }) });
      rebindDocument(value.outcome, value.outcome.receipt_document_json);
      return JSON.stringify(value);
    }],
    ["wrong runtime evidence", () => {
      const value = response({ document: receiptDocument({ decisionDigest: RECEIPT_DIGEST }) });
      rebindDocument(value.outcome, value.outcome.receipt_document_json);
      return JSON.stringify(value);
    }],
    ["missing predecessor", () => {
      const value = response({ document: receiptDocument({ previousReceiptId: null }) });
      rebindDocument(value.outcome, value.outcome.receipt_document_json);
      return JSON.stringify(value);
    }],
    ["malformed predecessor", () => {
      const value = response({ document: receiptDocument({ previousReceiptId: "bad" }) });
      rebindDocument(value.outcome, value.outcome.receipt_document_json);
      return JSON.stringify(value);
    }],
    ["document outcome", () => {
      const value = response({ document: receiptDocument({ acceptance: "rejected" }) });
      rebindDocument(value.outcome, value.outcome.receipt_document_json);
      return JSON.stringify(value);
    }],
    ["invalid acceptance", () => {
      const value = response();
      value.outcome.acceptance = "unknown";
      return JSON.stringify(value);
    }],
    ["nonboolean replay", () => {
      const value = response();
      value.outcome.already_recorded = 1;
      return JSON.stringify(value);
    }],
  ];
  for (const [label, factory] of cases) {
    await t.test(label, async () => {
      await expectResponseError(factory(), label);
    });
  }
});

test("Enterprise contextOutcome rejects duplicate and lexical response fields", async () => {
  const fixture = response();
  const nested = JSON.stringify(fixture.outcome);
  await expectResponseError(
    "{\"schema_version\":1,\"tenant_id\":\"" + TENANT_ID + "\",\"tenant_id\":\"" + OTHER_TENANT_ID + "\",\"outcome\":" + nested + "}",
    "duplicate outer key",
  );
  const outerLexical = JSON.stringify(fixture).replace("\"schema_version\":1", "\"schema_version\":1.0");
  await expectResponseError(outerLexical, "outer schema lexical integer");
  const nestedLexical = JSON.stringify(fixture).replace(
    "\"outcome\":{\"schema_version\":1",
    "\"outcome\":{\"schema_version\":1e0",
  );
  await expectResponseError(nestedLexical, "nested schema lexical integer");
});
