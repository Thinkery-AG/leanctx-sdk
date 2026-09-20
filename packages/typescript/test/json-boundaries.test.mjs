// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
import assert from "node:assert/strict";
import { test } from "node:test";
import { canonicalJson, strictJsonLoads } from "../dist/protocol.js";

test("strict JSON treats prototype-named keys as own data", () => {
  const parsed = strictJsonLoads('{"__proto__":{"allowed":true}}');
  assert.equal(Object.getPrototypeOf(parsed), Object.prototype);
  assert.equal(Object.hasOwn(parsed, "__proto__"), true);
  assert.equal(parsed.allowed, undefined);
  assert.equal(JSON.stringify(parsed), '{"__proto__":{"allowed":true}}');
});

test("canonical JSON preserves prototype-named keys as own data", () => {
  const encoded = '{"__proto__":{"allowed":true},"nested":[{"__proto__":7}]}';
  const parsed = strictJsonLoads(encoded);
  assert.equal(canonicalJson(parsed), encoded);
  assert.equal(parsed.allowed, undefined);
});

test("strict integer lexemes apply only at the designated JSON path", () => {
  const path = [["receipt", "schema_version"]];
  for (const version of ["1.0", "1e0"]) {
    assert.throws(() => strictJsonLoads(`{"receipt":{"schema_version":${version}}}`, "receipt", path));
  }
  const valid = '{"receipt":{"schema_version":1},"extension":{"schema_version":1.5}}';
  assert.deepEqual(strictJsonLoads(valid, "receipt", path), JSON.parse(valid));
  assert.deepEqual(strictJsonLoads('{"receipt":{"schema_version":1.5}}'), { receipt: { schema_version: 1.5 } });
});

test("strict JSON rejects both lone surrogate directions", () => {
  for (const surrogate of ["\ud800", "\udfff"]) {
    assert.throws(() => strictJsonLoads(`{"text":"${surrogate}"}`));
  }
  for (const escaped of ["\\ud800", "\\udfff"]) {
    assert.throws(() => strictJsonLoads(`{"extension":"${escaped}"}`));
    assert.throws(() => strictJsonLoads(`{"${escaped}":true}`));
  }
  assert.deepEqual(strictJsonLoads('{"text":"\\ud83d\\ude00"}'), { text: "😀" });
});
