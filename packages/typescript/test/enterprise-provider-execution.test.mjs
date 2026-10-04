// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import {
  EnginePlanningRequest,
  EngineProtocolError,
  EnterpriseEngineClient,
  ValidationError,
} from "../dist/index.js";
import { sha256Digest } from "../dist/protocol.js";

const TENANT_ID = "11111111-1111-4111-8111-111111111111";
const SOURCE_ID = "22222222-2222-4222-8222-222222222222";
const TASK_ID = "provider-task";
const PLAN_ID = "provider-plan";
const EPOCH = "2026-09-20T12:34:56Z";
const request = new EnginePlanningRequest(TASK_ID, "invoice ledger", 64);

const task = {
  schema_version: 1,
  task_id: TASK_ID,
  trace_id: "trace-provider",
  project_id: "project-provider",
  session_id: "session-provider",
  agent_id: "agent-provider",
  complexity: "unknown",
  created_at: EPOCH,
  tenant_id: TENANT_ID,
};

const plan = {
  schema_version: 1,
  plan_id: PLAN_ID,
  task_id: TASK_ID,
  context_plan_id: "provider-context-plan",
  context_budget_tokens: 64,
  context_strategy: "minimal",
  knowledge_refs: [],
  capability_ids: ["capability://leanctx/context-optimization"],
  model: "gpt-4o",
  provider: "openai",
  reasoning_allocation_milli: 0,
  max_retries: 0,
  fallback_refs: [],
  stop_condition: "on_completion",
  expected_cost_micros: 0,
  expected_quality_milli: 0,
  expected_latency_ms: 30_000,
};

function response(overrides = {}) {
  const content = "provider output";
  return {
    schema_version: 1,
    transport_version: 1,
    engine_interface_version: "1.0.0",
    attempt_id: "provider-attempt-1",
    task_id: TASK_ID,
    plan_id: PLAN_ID,
    context_digest: sha256Digest("materialized context"),
    request_digest: sha256Digest("host-owned request"),
    provider: "openai",
    model: "gpt-4o",
    status: "succeeded",
    acceptance: "unknown",
    output: { content, sha256_digest: sha256Digest(content) },
    usage: {
      state: "measured",
      uncached_input_tokens: 100,
      cache_write_input_tokens: 0,
      cache_read_input_tokens: 0,
      total_input_tokens: 100,
      output_tokens: 20,
    },
    cost: { basis: "unavailable" },
    ...overrides,
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
        body: Buffer.concat(chunks).toString("utf8"),
      });
      const payload = typeof body === "function" ? body() : body;
      responseMessage.writeHead(200, {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(payload),
      });
      responseMessage.end(payload);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.equal(typeof address, "object");
  return { server, requests, baseUrl: `http://127.0.0.1:${address.port}/` };
}

function client(baseUrl) {
  return new EnterpriseEngineClient(baseUrl, "credential-test", TENANT_ID, {
    allowLoopbackHttp: true,
    timeout: 2,
  });
}

async function providerCall(baseUrl, options = {}) {
  return client(baseUrl).providerExecute(
    task,
    plan,
    request,
    [SOURCE_ID],
    7,
    sha256Digest("source binding"),
    { maxOutputTokens: 64, planningEvaluationTime: EPOCH, ...options },
  );
}

async function assertRejectedResponse(payload, label) {
  const server = await loopback(payload);
  try {
    await assert.rejects(
      providerCall(server.baseUrl),
      (error) => error instanceof EngineProtocolError,
      label,
    );
  } finally {
    await new Promise((resolve) => server.server.close(resolve));
  }
}

test("Enterprise providerExecute posts the bounded authenticated v1 request", async () => {
  const fixture = JSON.stringify(response());
  const server = await loopback(fixture);
  try {
    const result = await providerCall(server.baseUrl);
    assert.deepEqual(result, JSON.parse(fixture));
    assert.equal(result.acceptance, "unknown");
    assert.equal(result.usage.total_input_tokens, 100);
    assert.equal(server.requests.length, 1);
    assert.equal(server.requests[0].method, "POST");
    assert.equal(server.requests[0].url, "/v1/engine/provider-execute");
    assert.equal(server.requests[0].authorization, "Bearer credential-test");
    const sent = JSON.parse(server.requests[0].body);
    assert.deepEqual(Object.keys(sent).sort(), [
      "materialization",
      "max_output_tokens",
      "plan",
      "schema_version",
      "task",
    ]);
    assert.equal(sent.schema_version, 1);
    assert.deepEqual(sent.task, task);
    assert.deepEqual(sent.plan, plan);
    assert.equal(sent.max_output_tokens, 64);
    assert.deepEqual(sent.materialization, {
      expected_binding_digest: sha256Digest("source binding"),
      expected_governance_revision: 7,
      planning: request.toDict(),
      planning_evaluation_time: EPOCH,
      source_ids: [SOURCE_ID],
    });
  } finally {
    await new Promise((resolve) => server.server.close(resolve));
  }
});

test("providerExecute rejects missing or unbounded request plan authority before HTTP", async () => {
  const invalidPlans = [
    ["missing context plan", { ...plan, context_plan_id: undefined }],
    ["null context plan", { ...plan, context_plan_id: null }],
    ["no token limit", { ...plan, context_budget_tokens: 0, context_budget_policy: { kind: "no_token_limit" } }],
    ["excessive budget", { ...plan, context_budget_tokens: 65 }],
    ["local-native", { ...plan, provider: "local-native", model: "local-native" }],
    ["retry", { ...plan, max_retries: 1 }],
  ];
  let calls = 0;
  const server = await loopback(() => {
    calls += 1;
    return JSON.stringify(response());
  });
  try {
    for (const [label, invalidPlan] of invalidPlans) {
      await assert.rejects(
        client(server.baseUrl).providerExecute(
          task,
          invalidPlan,
          request,
          [SOURCE_ID],
          7,
          sha256Digest("source binding"),
          { maxOutputTokens: 64 },
        ),
        ValidationError,
        label,
      );
    }
    await assert.rejects(
      providerCall(server.baseUrl, { maxOutputTokens: 0 }),
      ValidationError,
      "zero output tokens",
    );
    assert.equal(calls, 0);
  } finally {
    await new Promise((resolve) => server.server.close(resolve));
  }
});

test("providerExecute rejects strict versions, counters, output, acceptance, and non-object responses", async () => {
  const mutations = [
    ["schema decimal", JSON.stringify(response()).replace('"schema_version":1', '"schema_version":1.0')],
    ["transport exponent", JSON.stringify(response()).replace('"transport_version":1', '"transport_version":1e0')],
    ["usage decimal", JSON.stringify(response()).replace('"total_input_tokens":100', '"total_input_tokens":100.0')],
    ["output digest", JSON.stringify(response({ output: { content: "tampered", sha256_digest: sha256Digest("provider output") } }))],
    ["acceptance", JSON.stringify(response({ acceptance: "accepted" }))],
    [
      "failure retry",
      JSON.stringify(response({
        status: "failed",
        output: null,
        failure: { code: "internal", retryable_by_host: true },
      })),
    ],
    ["scalar", "null"],
    ["array", "[]"],
  ];
  for (const [label, payload] of mutations) {
    await assertRejectedResponse(payload, label);
  }
});

test("providerExecute keeps usage and cost provenance strict", async () => {
  const unavailableUsage = {
    state: "unavailable",
    uncached_input_tokens: null,
    cache_write_input_tokens: null,
    cache_read_input_tokens: null,
    total_input_tokens: null,
    output_tokens: null,
  };
  await assertRejectedResponse(
    JSON.stringify(response({
      usage: unavailableUsage,
      cost: { basis: "usage_priced_estimate", micros: 1 },
    })),
    "priced estimate with unavailable usage",
  );
  await assertRejectedResponse(
    JSON.stringify(response({ provider: "other-provider" })),
    "provider mismatch",
  );
});
