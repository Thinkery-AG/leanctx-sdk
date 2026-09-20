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
import { canonicalBytes, sha256Digest } from "../dist/protocol.js";

const tenantId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const otherTenantId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const sourceId = "11111111-1111-4111-8111-111111111111";
const otherSourceId = "22222222-2222-4222-8222-222222222222";
const request = new EnginePlanningRequest("enterprise-source-task", "invoice ledger", 64);

function descriptor(id = sourceId, permission = "permitted") {
  const content = "enterprise source body";
  return {
    object_ref: id,
    source_id: id,
    source_type: "filesystem",
    content_digest: sha256Digest(content),
    revision: "revision-one",
    owner: "owner",
    observed_at: null,
    valid_until: null,
    classification: "Internal",
    permission,
  };
}

function sourcePlan(overrides = {}) {
  const binding = descriptor(
    overrides.sourceId ?? sourceId,
    overrides.permission ?? "permitted",
  );
  const unsignedPlan = {
    schema_version: 1,
    context_plan_id: "plan:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    task_id: request.taskId,
    budget_tokens: request.budgetTokens,
    selections: [{
      source_ref: binding.object_ref,
      provider: binding.source_id,
      disposition: "selected",
      token_count: 4,
      sha256_digest: binding.content_digest,
      reason_codes: ["relevant"],
    }],
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
  const sourceBindings = [binding];
  return {
    result,
    source_bindings: sourceBindings,
    binding_digest: sha256Digest(canonicalBytes([result, sourceBindings])),
  };
}

function response({
  tenant = tenantId,
  revision = 7,
  plan = sourcePlan(),
} = {}) {
  return JSON.stringify({
    schema_version: 1,
    tenant_id: tenant,
    governance_revision: revision,
    plan,
  });
}

async function loopbackServer(handler) {
  const server = createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.equal(typeof address, "object");
  return {
    server,
    url: "http://127.0.0.1:" + address.port,
  };
}

function client(url, tenant = tenantId) {
  return new EnterpriseEngineClient(url, "credential-test", tenant, {
    allowLoopbackHttp: true,
    timeout: 2,
  });
}

test("Enterprise source planning posts the canonical tenant-bound request", async () => {
  let method;
  let path;
  let authorization;
  let body;
  const planned = sourcePlan();
  const { server, url } = await loopbackServer((req, res) => {
    method = req.method;
    path = req.url;
    authorization = req.headers.authorization;
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      body = Buffer.concat(chunks);
      res.setHeader("content-type", "application/json");
      res.end(response({ plan: planned }));
    });
  });
  try {
    const result = await client(url).contextPlanSources(request, [sourceId]);
    assert.equal(method, "POST");
    assert.equal(path, "/v1/engine/context-plan");
    assert.equal(authorization, "Bearer credential-test");
    assert.deepEqual(body, canonicalBytes({
      planning: request.toDict(),
      source_ids: [sourceId],
    }));
    assert.equal(result.tenant_id, tenantId);
    assert.equal(result.governance_revision, 7);
    assert.equal(result.plan.binding_digest, planned.binding_digest);
    assert.equal(result.plan.source_bindings[0].permission, "permitted");
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("Enterprise source planning rejects tenant, source, and permission mismatches", async (t) => {
  const cases = [
    ["wrong tenant", response({ tenant: otherTenantId })],
    ["unrequested source", response({ plan: sourcePlan({ sourceId: otherSourceId }) })],
    ["denied source", response({ plan: sourcePlan({ permission: "denied" }) })],
    ["unknown prototype-named plan field", response().replace('"plan":{', '"plan":{"__proto__":{},')],
  ];
  for (const [name, payload] of cases) {
    await t.test(name, async () => {
      const { server, url } = await loopbackServer((_req, res) => {
        res.setHeader("content-type", "application/json");
        res.end(payload);
      });
      try {
        await assert.rejects(
          client(url).contextPlanSources(request, [sourceId]),
          EngineProtocolError,
        );
      } finally {
        await new Promise((resolve) => server.close(resolve));
      }
    });
  }
});

test("Enterprise source planning rejects non-integral envelope revisions", async () => {
  const payload = response().replace('"governance_revision":7', '"governance_revision":1.0');
  const { server, url } = await loopbackServer((_req, res) => res.end(payload));
  try {
    await assert.rejects(
      client(url).contextPlanSources(request, [sourceId]),
      EngineProtocolError,
    );
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("Enterprise source planning rejects non-integral nested plan fields", async (t) => {
  const mutations = [
    ["result schema", '"schema_version":1,"transport_version"', '"schema_version":1.0,"transport_version"'],
    ["plan schema", '"plan":{"schema_version":1,"context_plan_id"', '"plan":{"schema_version":1e0,"context_plan_id"'],
    ["budget", '"task_id":"enterprise-source-task","budget_tokens":64', '"task_id":"enterprise-source-task","budget_tokens":64.0'],
    ["token count", '"disposition":"selected","token_count":4', '"disposition":"selected","token_count":4e0'],
  ];
  for (const [name, from, to] of mutations) {
    await t.test(name, async () => {
      const payload = response().replace(from, to);
      assert.notEqual(payload, response());
      const { server, url } = await loopbackServer((_req, res) => res.end(payload));
      try {
        await assert.rejects(
          client(url).contextPlanSources(request, [sourceId]),
          EngineProtocolError,
        );
      } finally {
        await new Promise((resolve) => server.close(resolve));
      }
    });
  }
});

test("Enterprise source planning does not follow redirects", async () => {
  let calls = 0;
  const { server, url } = await loopbackServer((_req, res) => {
    calls += 1;
    res.statusCode = 302;
    res.setHeader("location", "http://127.0.0.1:1/v1/engine/context-plan");
    res.end();
  });
  try {
    await assert.rejects(
      client(url).contextPlanSources(request, [sourceId]),
      EngineProtocolError,
    );
    assert.equal(calls, 1);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("Enterprise source planning rejects invalid caller inputs before HTTP", async () => {
  let calls = 0;
  const { server, url } = await loopbackServer((_req, res) => {
    calls += 1;
    res.end(response());
  });
  try {
    await assert.rejects(
      client(url).contextPlanSources(request, [sourceId, sourceId]),
      ValidationError,
    );
    await assert.rejects(
      client(url).contextPlanSources(request, ["not-a-uuid"]),
      ValidationError,
    );
    assert.equal(calls, 0);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
