// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import {
  EnginePlanningRequest,
  EngineProtocolError,
  EnterpriseEngineClient,
  parseSourceExecutionV2Response,
} from "../dist/index.js";
import { canonicalBytes, sha256Digest } from "../dist/protocol.js";

const TENANT_ID = "11111111-1111-4111-8111-111111111111";
const SOURCE_ID = "22222222-2222-4222-8222-222222222222";
const TASK_ID = "enterprise-task";
const EPOCH = "2026-09-20T12:34:56Z";
const request = new EnginePlanningRequest(TASK_ID, "source query", 64);
const sourceContentDigest = sha256Digest("source body");

const task = {
  schema_version: 1,
  task_id: TASK_ID,
  trace_id: "trace-1",
  project_id: "project-1",
  session_id: "session-1",
  agent_id: "agent-1",
  complexity: "low",
  created_at: "2026-09-20T12:00:00Z",
  tenant_id: TENANT_ID,
};

const plan = {
  schema_version: 1,
  plan_id: "plan-1",
  task_id: TASK_ID,
  context_budget_tokens: 64,
  context_strategy: "balanced",
  knowledge_refs: [],
  capability_ids: ["capability://leanctx/context-optimization"],
  model: "local-native",
  provider: "local-native",
  reasoning_allocation_milli: 1000,
  max_retries: 0,
  fallback_refs: [],
  stop_condition: "on_completion",
  expected_cost_micros: 0,
  expected_quality_milli: 1000,
  expected_latency_ms: 0,
  capability_bindings: [{ capability_id: "capability://leanctx/context-optimization", version: "1.0.0" }],
};

function sourceDescriptor() {
  return {
    object_ref: SOURCE_ID,
    source_id: SOURCE_ID,
    source_type: "filesystem",
    content_digest: sourceContentDigest,
    revision: "revision-1",
    owner: "owner-1",
    observed_at: null,
    valid_until: null,
    classification: "Internal",
    permission: "permitted",
  };
}

function buildFixture(overrides = {}) {
  const unsignedSourcePlan = {
    schema_version: 1,
    context_plan_id: "context-plan-1",
    task_id: TASK_ID,
    budget_tokens: 64,
    selections: [{
      source_ref: SOURCE_ID,
      provider: SOURCE_ID,
      disposition: "selected",
      token_count: 4,
      sha256_digest: sourceContentDigest,
      reason_codes: ["relevant"],
    }],
    context_plan_evaluation_v1: { schema_version: 1, evaluation_time: EPOCH },
  };
  const sourcePlanResult = {
    schema_version: 1,
    transport_version: 1,
    engine_interface_version: "1.0.0",
    plan: {
      ...unsignedSourcePlan,
      projection_digest: sha256Digest(canonicalBytes(unsignedSourcePlan)),
    },
  };
  const sourceBindings = [sourceDescriptor()];
  const sourcePlan = {
    result: sourcePlanResult,
    source_bindings: sourceBindings,
    binding_digest: sha256Digest(canonicalBytes([sourcePlanResult, sourceBindings])),
  };
  const executionPlan = {
    ...plan,
    context_plan_id: "context-plan-1",
    context_autopilot_decision_ref: "decision:context-1",
  };
  const inputDigest = sha256Digest("materialized context");
  const inputRef = "input:source-materialization-sha256:" + inputDigest.slice(7);
  const sourcePlanDigest = sha256Digest(canonicalBytes(sourcePlan));
  const executionPlanDigest = sha256Digest(canonicalBytes(executionPlan));
  const taskDigest = sha256Digest(canonicalBytes(task));
  const sourceRefs = [
    inputRef,
    "artifact://execution/evidence/" + sourcePlanDigest.slice(7),
    "task:sha256:" + taskDigest.slice(7),
    "plan:sha256:" + executionPlanDigest.slice(7),
  ];
  const invocation = {
    schema_version: 1,
    invocation_id: "invocation-1",
    engine: { engine_id: "lean-ctx-local", engine_version: "3.10.1" },
    operation: { capability_id: "capability://leanctx/context-optimization", capability_version: "1.0.0" },
    input_ref: inputRef,
    input_digest: inputDigest,
    source_refs: sourceRefs,
    policy_admission: { policy_ref: "policy:1", decision: "admitted" },
  };
  const viewText = "materialized execution context";
  const outputDigest = sha256Digest(viewText);
  const outputRef = "output:" + outputDigest.slice(7);
  // Opaque carrier fixture only; the SDK preserves bytes and does not claim signature trust.
  const receiptDocumentJson = JSON.stringify({ fixture: "opaque receipt carrier", task_id: TASK_ID });
  const receiptDigest = sha256Digest(receiptDocumentJson);
  const observation = {
    schema_version: 1,
    invocation_id: invocation.invocation_id,
    status: "succeeded",
    output_ref: outputRef,
    output_digest: outputDigest,
    source_lineage: sourceRefs,
    measurements: [],
    failure: null,
    receipt_link: {
      schema_version: 1,
      receipt_id: "receipt-1",
      receipt_ref: "receipt:" + receiptDigest,
      receipt_digest: receiptDigest,
      invocation_id: invocation.invocation_id,
    },
  };
  const execution = {
    schema_version: 1,
    transport_version: 1,
    engine_interface_version: "1.0.0",
    source_plan: sourcePlan,
    execution_plan: executionPlan,
    view: { text: viewText, output_ref: outputRef, output_digest: outputDigest },
    invocation,
    observation,
    canonical_receipt: {
      receipt_id: "receipt-1",
      receipt_ref: "id:" + receiptDigest,
      receipt_digest: receiptDigest,
      outcome: "unknown",
    },
  };
  const response = {
    schema_version: 2,
    tenant_id: TENANT_ID,
    governance_revision: 7,
    execution: {
      schema_version: 2,
      execution,
      receipt_document_json: receiptDocumentJson,
    },
    ...overrides,
  };
  return { response, sourcePlan, executionPlan, receiptDocumentJson, sourceRefs };
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
        body: Buffer.concat(chunks).toString("utf8"),
      });
      const payload = typeof body === "function" ? body() : body;
      responseMessage.writeHead(200, { "content-type": "application/json", "content-length": Buffer.byteLength(payload) });
      responseMessage.end(payload);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  return { server, requests, baseUrl: `http://127.0.0.1:${address.port}/` };
}

test("Enterprise contextExecuteV2 uses the authenticated transport and preserves exact receipt text", async () => {
  const fixture = buildFixture();
  const server = await loopback(JSON.stringify(fixture.response));
  try {
    const client = new EnterpriseEngineClient(server.baseUrl, "credential-test", TENANT_ID, { allowLoopbackHttp: true });
    const result = await client.contextExecuteV2(task, plan, request, [SOURCE_ID], 7, fixture.sourcePlan.binding_digest, { planningEvaluationTime: EPOCH });
    assert.equal(result.execution.receipt_document_json, fixture.receiptDocumentJson);
    assert.equal(server.requests.length, 1);
    assert.equal(server.requests[0].url, "/v2/engine/context-execute");
    assert.equal(server.requests[0].authorization, "Bearer credential-test");
    const sent = JSON.parse(server.requests[0].body);
    assert.deepEqual(Object.keys(sent).sort(), ["materialization", "plan", "task"]);
    assert.deepEqual(sent.materialization.source_ids, [SOURCE_ID]);
    assert.equal(sent.materialization.expected_binding_digest, fixture.sourcePlan.binding_digest);
    assert.equal(sent.materialization.planning_evaluation_time, EPOCH);
    assert.equal(Object.hasOwn(sent, "source_body"), false);
  } finally {
    await new Promise((resolve) => server.server.close(resolve));
  }
});

test("v2 parser rejects tenant, revision, epoch, output, invocation, and receipt joins", () => {
  const fixture = buildFixture();
  assert.throws(
    () => parseSourceExecutionV2Response("\ud800", request, [SOURCE_ID], task, plan, TENANT_ID, 7, fixture.sourcePlan.binding_digest, EPOCH),
    EngineProtocolError,
  );
  const cases = [
    ["tenant", { ...fixture.response, tenant_id: "33333333-3333-4333-8333-333333333333" }],
    ["revision", { ...fixture.response, governance_revision: 8 }],
    ["output", { ...fixture.response, execution: { ...fixture.response.execution, execution: { ...fixture.response.execution.execution, view: { ...fixture.response.execution.execution.view, output_digest: sha256Digest("other") } } } }],
    ["invocation", { ...fixture.response, execution: { ...fixture.response.execution, execution: { ...fixture.response.execution.execution, invocation: { ...fixture.response.execution.execution.invocation, source_refs: ["input:source-materialization-sha256:" + "f".repeat(64)] } } } }],
    ["operation", { ...fixture.response, execution: { ...fixture.response.execution, execution: { ...fixture.response.execution.execution, invocation: { ...fixture.response.execution.execution.invocation, operation: { capability_id: "capability://other", capability_version: "1.0.0" } } } } }],
    ["receipt", { ...fixture.response, execution: { ...fixture.response.execution, receipt_document_json: "{\"different\":true}" } }],
  ];
  for (const [label, candidate] of cases) {
    assert.throws(
      () => parseSourceExecutionV2Response(JSON.stringify(candidate), request, [SOURCE_ID], task, plan, TENANT_ID, 7, fixture.sourcePlan.binding_digest, EPOCH),
      (error) => error instanceof EngineProtocolError,
      label,
    );
  }
});

test("v2 parser rejects a changed execution plan even when its lineage digest is rebound", () => {
  const fixture = buildFixture();
  const changedPlan = { ...fixture.executionPlan, max_retries: 1 };
  const sourcePlanDigest = sha256Digest(canonicalBytes(fixture.sourcePlan));
  const taskDigest = sha256Digest(canonicalBytes(task));
  const changedPlanDigest = sha256Digest(canonicalBytes(changedPlan));
  const changedRefs = [
    fixture.sourceRefs[0],
    "artifact://execution/evidence/" + sourcePlanDigest.slice(7),
    "task:sha256:" + taskDigest.slice(7),
    "plan:sha256:" + changedPlanDigest.slice(7),
  ];
  const candidate = {
    ...fixture.response,
    execution: {
      ...fixture.response.execution,
      execution: {
        ...fixture.response.execution.execution,
        execution_plan: changedPlan,
        invocation: { ...fixture.response.execution.execution.invocation, source_refs: changedRefs },
        observation: { ...fixture.response.execution.execution.observation, source_lineage: changedRefs },
      },
    },
  };
  assert.throws(
    () => parseSourceExecutionV2Response(JSON.stringify(candidate), request, [SOURCE_ID], task, plan, TENANT_ID, 7, fixture.sourcePlan.binding_digest, EPOCH),
    EngineProtocolError,
  );
});
