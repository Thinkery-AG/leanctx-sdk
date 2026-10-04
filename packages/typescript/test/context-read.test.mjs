// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { parseContextReadResponse } from "../dist/context.js";
import { strictJsonLoads } from "../dist/protocol.js";
import {
  EngineContextClient,
  EngineProtocolError,
  EngineRejected,
  EngineTimeout,
  PolicyAdmissionError,
  ValidationError,
} from "../dist/index.js";

const digest = "sha256:" + "a".repeat(64);

test("guarded context read matches shared cross-language receipt fixtures", () => {
  const fixture = JSON.parse(readFileSync(new URL("../../../fixtures/guarded-context-read-v1/conformance.json", import.meta.url), "utf8"));
  assert.deepEqual(parseContextReadResponse(JSON.stringify(fixture.response)).rawResponse, fixture.response);
  for (const override of fixture.invalid_receipt_overrides) {
    // JS erases the distinction between 1, 1.0 and 1e0; raw cases follow below.
    if (override.schema_version === 1) continue;
    const candidate = structuredClone(fixture.response);
    Object.assign(candidate.result._meta.canonical_receipt, override);
    assert.throws(() => parseContextReadResponse(JSON.stringify(candidate)), EngineProtocolError);
  }
  for (const literal of ["1.0", "1e0"]) {
    const candidate = JSON.stringify(fixture.response).replace('"schema_version":1', `"schema_version":${literal}`);
    assert.throws(() => parseContextReadResponse(candidate), EngineProtocolError);
  }
});

test("strict parser preserves prototype-named data without inheriting authority", () => {
  const parsed = strictJsonLoads('{"__proto__":{"schema_version":1}}');
  assert.equal(Object.getPrototypeOf(parsed), Object.prototype);
  assert.equal(Object.hasOwn(parsed, "__proto__"), true);
  assert.equal(parsed.schema_version, undefined);
  const candidate = JSON.parse(wireResponse());
  const receipt = candidate.result._meta.canonical_receipt;
  candidate.result._meta.canonical_receipt = JSON.parse(`{"__proto__":${JSON.stringify(receipt)}}`);
  assert.throws(() => parseContextReadResponse(JSON.stringify(candidate)), EngineProtocolError);
  const extension = JSON.parse(wireResponse());
  extension.result.extension = JSON.parse('{"__proto__":{"schema_version":1.5}}');
  assert.deepEqual(parseContextReadResponse(JSON.stringify(extension)).rawResponse, extension);
});

test("guarded context read rejects malformed Unicode and duplicate keys", () => {
  for (const text of ["\ud800", "\udfff"]) {
    assert.throws(() => parseContextReadResponse(wireResponse({ content: [{ type: "text", text }] })), EngineProtocolError);
    assert.throws(() => strictJsonLoads(text), /UTF-8|invalid/);
  }
  assert.throws(() => parseContextReadResponse(wireResponse().replace('"schema_version":1', '"schema_version":1,"schema_version":1')), EngineProtocolError);
});

test("guarded context read enforces deadline and declared response limit", async () => {
  const stalled = createServer((_request, _response) => {});
  await new Promise((resolve) => stalled.listen(0, "127.0.0.1", resolve));
  try {
    const url = `http://127.0.0.1:${stalled.address().port}`;
    await assert.rejects(new EngineContextClient(url, "credential-test", { allowLoopbackHttp: true, timeout: 0.1 }).contextRead("file"), EngineTimeout);
  } finally {
    stalled.closeAllConnections();
    await new Promise((resolve) => stalled.close(resolve));
  }
  const oversized = await serverFor("", 200, { "content-length": 16777217 });
  try {
    await assert.rejects(new EngineContextClient(oversized.baseUrl, "credential-test", { allowLoopbackHttp: true }).contextRead("file"), EngineProtocolError);
  } finally {
    await new Promise((resolve) => oversized.server.close(resolve));
  }
});

function wireResponse(overrides = {}) {
  return JSON.stringify({
    result: {
      content: [{ type: "text", text: "guarded context text\n", extension: { preserve: true } }],
      isError: false,
      _meta: {
        canonical_receipt: {
          schema_version: 1,
          receipt_id: "fixture-receipt",
          receipt_ref: `id:${digest}`,
          receipt_digest: digest,
          outcome: "unknown",
          delivery: "native_engine_view",
        },
        extension: true,
      },
      extension: [1, 2, 3],
      ...overrides,
    },
  });
}

async function serverFor(body, status = 200, headers = {}, host = "127.0.0.1") {
  const requests = [];
  const server = createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      requests.push({ headers: request.headers, body: Buffer.concat(chunks).toString("utf8"), url: request.url });
      response.writeHead(status, { "content-type": "application/json", ...headers });
      response.end(body);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, host, resolve);
  });
  const address = server.address();
  const renderedHost = host.includes(":") ? `[${host}]` : host;
  return { server, requests, baseUrl: `http://${renderedHost}:${address.port}/` };
}

test("guarded context read sends the canonical request and preserves extensions", async () => {
  const fixture = await serverFor(wireResponse());
  try {
    const result = await new EngineContextClient(fixture.baseUrl, "credential-test", { allowLoopbackHttp: true }).contextRead("src/context.md");
    assert.equal(result.text, "guarded context text\n");
    assert.deepEqual(result.rawResponse.result.extension, [1, 2, 3]);
    assert.equal(fixture.requests.length, 1);
    const request = fixture.requests[0];
    assert.equal(request.url, "/v1/tools/call");
    assert.equal(request.headers.authorization, "Bearer credential-test");
    assert.deepEqual(JSON.parse(request.body), {
      name: "ctx_read",
      arguments: { path: "src/context.md", mode: "aggressive", engine_interface: "v1" },
    });
  } finally {
    await new Promise((resolve) => fixture.server.close(resolve));
  }
});

test("guarded context read rejects path controls before network", async () => {
  const fixture = await serverFor(wireResponse());
  try {
    const client = new EngineContextClient(fixture.baseUrl, "credential-test", { allowLoopbackHttp: true });
    for (const path of ["", "\x00", "\x7f", "\x80", "\ud800"]) {
      await assert.rejects(client.contextRead(path), ValidationError);
    }
    assert.equal(fixture.requests.length, 0);
  } finally {
    await new Promise((resolve) => fixture.server.close(resolve));
  }
});

test("guarded context read accepts literal IPv6 loopback when available", async (t) => {
  let fixture;
  try {
    fixture = await serverFor(wireResponse(), 200, {}, "::1");
  } catch {
    t.skip("IPv6 loopback is unavailable in this environment");
    return;
  }
  try {
    const result = await new EngineContextClient(fixture.baseUrl, "credential-test", { allowLoopbackHttp: true }).contextRead("src/context.md");
    assert.equal(result.text, "guarded context text\n");
  } finally {
    await new Promise((resolve) => fixture.server.close(resolve));
  }
});

test("guarded context read maps status and malformed receipt failures", async (t) => {
  const cases = [
    [401, EngineRejected],
    [403, PolicyAdmissionError],
    [307, EngineProtocolError],
  ];
  for (const [status, errorType] of cases) {
    await t.test(`status ${status}`, async () => {
      const fixture = await serverFor("", status, status === 307 ? { location: "/redirect" } : {});
      try {
        await assert.rejects(
          new EngineContextClient(fixture.baseUrl, "credential-test", { allowLoopbackHttp: true }).contextRead("src/context.md"),
          errorType,
        );
      } finally {
        await new Promise((resolve) => fixture.server.close(resolve));
      }
    });
  }
  const fixture = await serverFor(wireResponse({ _meta: {} }));
  try {
    await assert.rejects(
      new EngineContextClient(fixture.baseUrl, "credential-test", { allowLoopbackHttp: true }).contextRead("src/context.md"),
      EngineProtocolError,
    );
  } finally {
    await new Promise((resolve) => fixture.server.close(resolve));
  }
});
