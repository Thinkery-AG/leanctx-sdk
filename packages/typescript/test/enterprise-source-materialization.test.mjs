// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import {
  EnginePlanningRequest,
  EngineProtocolError,
  EngineRejected,
  EngineUnavailable,
  EnterpriseEngineClient,
  PolicyAdmissionError,
  ValidationError,
} from "../dist/index.js";
import { canonicalBytes, sha256Digest } from "../dist/protocol.js";

const tenantId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const otherTenantId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const sourceId = "11111111-1111-4111-8111-111111111111";
const otherSourceId = "22222222-2222-4222-8222-222222222222";
const epoch = "2026-01-01T00:00:00Z";
const otherEpoch = "2026-02-02T00:00:00Z";
const revision = 7;
const credential = "credential-test";
const request = new EnginePlanningRequest("enterprise-materialize-task", "invoice ledger", 64);
const content = "## leanctx-source-v1\nfile\nsource-file\ninvoice ledger body\n";

function descriptor(id, permission) {
  const body = "enterprise source body";
  return {
    object_ref: id,
    source_id: id,
    source_type: "filesystem",
    content_digest: sha256Digest(body),
    revision: "revision-one",
    owner: "owner",
    observed_at: null,
    valid_until: null,
    classification: "Internal",
    permission,
  };
}

function sourcePlan({
  id = sourceId,
  permission = "permitted",
  evaluationTime = epoch,
} = {}) {
  const binding = descriptor(id, permission);
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
    context_plan_evaluation_v1: { evaluation_time: evaluationTime },
  };
  const plan = { ...unsignedPlan, projection_digest: sha256Digest(canonicalBytes(unsignedPlan)) };
  const result = { schema_version: 1, transport_version: 1, engine_interface_version: "1.0.0", plan };
  const sourceBindings = [binding];
  return {
    result,
    source_bindings: sourceBindings,
    binding_digest: sha256Digest(canonicalBytes([result, sourceBindings])),
  };
}

const boundPlan = sourcePlan();

function response({
  tenant = tenantId,
  governanceRevision = revision,
  plan = boundPlan,
  materializedContent = content,
  materializedDigest = sha256Digest(content),
  tokenCount = 4,
  engineInterfaceVersion = "1.0.0",
} = {}) {
  return JSON.stringify({
    schema_version: 1,
    tenant_id: tenant,
    governance_revision: governanceRevision,
    materialization: {
      schema_version: 1,
      transport_version: 1,
      engine_interface_version: engineInterfaceVersion,
      plan,
      materialized_digest: materializedDigest,
      materialized_token_count: tokenCount,
      content: materializedContent,
    },
  });
}

async function loopbackServer(handler) {
  const server = createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.equal(typeof address, "object");
  return { server, url: "http://127.0.0.1:" + address.port };
}

function client(url, tenant = tenantId) {
  return new EnterpriseEngineClient(url, credential, tenant, {
    allowLoopbackHttp: true,
    timeout: 2,
  });
}

async function withServer(handler, body) {
  const { server, url } = await loopbackServer(handler);
  try {
    return await body(url);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function respondWith(payload, counter) {
  return (_req, res) => {
    if (counter !== undefined) counter.calls += 1;
    res.setHeader("content-type", "application/json");
    res.end(payload);
  };
}

function materialize(url, overrides = {}) {
  return client(url).contextMaterializeSources(
    overrides.request ?? request,
    overrides.sourceIds ?? [sourceId],
    overrides.governanceRevision ?? revision,
    overrides.bindingDigest ?? boundPlan.binding_digest,
    overrides.options ?? { planningEvaluationTime: epoch },
  );
}

test("Enterprise materialization posts the canonical bound request and validates the projection", async () => {
  let method;
  let path;
  let authorization;
  let body;
  const result = await withServer((req, res) => {
    method = req.method;
    path = req.url;
    authorization = req.headers.authorization;
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      body = Buffer.concat(chunks);
      res.setHeader("content-type", "application/json");
      res.end(response());
    });
  }, (url) => materialize(url));
  assert.equal(method, "POST");
  assert.equal(path, "/v1/engine/context-materialize");
  assert.equal(authorization, "Bearer " + credential);
  assert.deepEqual(body, canonicalBytes({
    planning: request.toDict(),
    source_ids: [sourceId],
    expected_governance_revision: revision,
    expected_binding_digest: boundPlan.binding_digest,
    planning_evaluation_time: epoch,
  }));
  assert.equal(result.schema_version, 1);
  assert.equal(result.tenant_id, tenantId);
  assert.equal(result.governance_revision, revision);
  assert.equal(result.materialization.transport_version, 1);
  assert.equal(result.materialization.engine_interface_version, "1.0.0");
  assert.equal(result.materialization.plan.binding_digest, boundPlan.binding_digest);
  assert.equal(result.materialization.plan.source_bindings[0].permission, "permitted");
  assert.equal(result.materialization.materialized_digest, sha256Digest(content));
  assert.equal(result.materialization.materialized_token_count, 4);
  assert.equal(result.materialization.content, content);
});

test("Enterprise materialization omits an unset evaluation epoch from the request", async () => {
  let body;
  await withServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      body = Buffer.concat(chunks);
      res.end(response());
    });
  }, (url) => materialize(url, { options: {} }));
  assert.deepEqual(body, canonicalBytes({
    planning: request.toDict(),
    source_ids: [sourceId],
    expected_governance_revision: revision,
    expected_binding_digest: boundPlan.binding_digest,
  }));
});

test("Enterprise materialization rejects tampered and foreign responses", async (t) => {
  const tamperedContent = content.replace("invoice ledger body", "attacker ledger body");
  const foreignPlan = sourcePlan({ id: otherSourceId });
  const deniedPlan = sourcePlan({ permission: "denied" });
  const rebasedPlan = sourcePlan({ evaluationTime: otherEpoch });
  const cases = [
    ["foreign tenant", {
      payload: response({ tenant: otherTenantId }),
      message: "tenant binding does not match",
    }],
    ["changed governance revision", {
      payload: response({ governanceRevision: revision + 1 }),
      message: "governance revision does not match",
    }],
    ["foreign source id", {
      payload: response({ plan: foreignPlan }),
      overrides: { bindingDigest: foreignPlan.binding_digest },
      message: "outside requested sources",
    }],
    ["source not permitted", {
      payload: response({ plan: deniedPlan }),
      overrides: { bindingDigest: deniedPlan.binding_digest },
      message: "is not permitted",
    }],
    ["foreign binding digest", {
      payload: response(),
      overrides: { bindingDigest: sha256Digest("other-plan") },
      message: "binding digest does not match",
    }],
    ["changed evaluation epoch", {
      payload: response({ plan: rebasedPlan }),
      overrides: { bindingDigest: rebasedPlan.binding_digest },
      message: "changed the evaluation time",
    }],
    ["tampered content", {
      payload: response({ materializedContent: tamperedContent }),
      message: "materialized content digest does not match",
    }],
    ["tampered materialized digest", {
      payload: response({ materializedDigest: sha256Digest("other") }),
      message: "materialized content digest does not match",
    }],
    ["token count above the plan budget", {
      payload: response({ tokenCount: 65 }),
      message: "exceeds the plan budget",
    }],
    ["unsupported Engine interface", {
      payload: response({ engineInterfaceVersion: "2.0.0" }),
      message: "engine_interface_version is unsupported",
    }],
    ["unknown envelope field", {
      payload: response().replace('{"schema_version":1', '{"extra":1,"schema_version":1'),
      message: "fields do not match the v1 contract",
    }],
    ["missing materialization field", {
      payload: response().replace(',"materialized_token_count":4', ""),
      message: "fields do not match the v1 contract",
    }],
    ["prototype-named plan field", {
      payload: response().replace('"plan":{"result"', '"plan":{"__proto__":{},"result"'),
      message: "source plan failed validation",
    }],
    ["duplicate envelope key", {
      payload: response().replace('"tenant_id"', '"governance_revision":9,"tenant_id"'),
      message: "is not valid JSON",
    }],
  ];
  for (const [name, { payload, overrides, message }] of cases) {
    await t.test(name, async () => {
      await withServer(respondWith(payload), async (url) => {
        await assert.rejects(materialize(url, overrides), (error) => {
          assert.ok(error instanceof EngineProtocolError, name + " raised " + error.name);
          assert.match(error.message, new RegExp(message.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
          return true;
        });
      });
    });
  }
});

test("Enterprise materialization rejects non-integral response numbers", async (t) => {
  const mutations = [
    ["governance revision", '"governance_revision":7', '"governance_revision":7.0'],
    ["materialization schema", '"materialization":{"schema_version":1', '"materialization":{"schema_version":1.0'],
    ["transport version", '"transport_version":1,"engine_interface_version"', '"transport_version":1e0,"engine_interface_version"'],
    ["token count", '"materialized_token_count":4', '"materialized_token_count":4e0'],
    ["plan budget", '"budget_tokens":64', '"budget_tokens":64.0'],
    ["selection token count", '"token_count":4', '"token_count":4.0'],
  ];
  for (const [name, from, to] of mutations) {
    await t.test(name, async () => {
      const payload = response().replace(from, to);
      assert.notEqual(payload, response());
      await withServer(respondWith(payload), async (url) => {
        await assert.rejects(materialize(url), EngineProtocolError);
      });
    });
  }
});

test("Enterprise materialization rejects caller inputs before any HTTP call", async (t) => {
  const cases = [
    ["duplicate source ids", { sourceIds: [sourceId, sourceId] }],
    ["malformed source id", { sourceIds: ["not-a-uuid"] }],
    ["nil source id", { sourceIds: ["00000000-0000-0000-0000-000000000000"] }],
    ["fractional governance revision", { governanceRevision: 7.5 }],
    ["governance revision beyond the safe integer domain", { governanceRevision: Number.MAX_SAFE_INTEGER + 1 }],
    ["negative governance revision", { governanceRevision: -1 }],
    ["malformed binding digest", { bindingDigest: "sha256:not-hex" }],
    ["malformed evaluation epoch", { options: { planningEvaluationTime: "2026-01-01 00:00:00" } }],
    ["planning request of the wrong type", { request: { taskId: "enterprise-materialize-task" } }],
  ];
  for (const [name, overrides] of cases) {
    await t.test(name, async () => {
      const counter = { calls: 0 };
      await withServer(respondWith(response(), counter), async (url) => {
        await assert.rejects(materialize(url, overrides), ValidationError);
        assert.equal(counter.calls, 0);
      });
    });
  }
});

test("Enterprise materialization surfaces provider errors without a response body", async (t) => {
  const cases = [
    ["server error", 500, EngineUnavailable],
    ["policy rejection", 403, PolicyAdmissionError],
    ["authentication rejection", 401, EngineRejected],
    ["client rejection", 409, EngineRejected],
    ["redirect", 302, EngineProtocolError],
  ];
  for (const [name, status, expected] of cases) {
    await t.test(name, async () => {
      const counter = { calls: 0 };
      await withServer((_req, res) => {
        counter.calls += 1;
        res.statusCode = status;
        if (status === 302) res.setHeader("location", "http://127.0.0.1:1/v1/engine/context-materialize");
        res.end(response({ tenant: otherTenantId }));
      }, async (url) => {
        await assert.rejects(materialize(url), expected);
        assert.equal(counter.calls, 1);
      });
    });
  }
});
