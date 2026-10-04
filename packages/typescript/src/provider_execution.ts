// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** Strict TypeScript validation for governed Engine provider execution. */

import { EngineProtocolError, ValidationError } from "./errors.js";
import {
  ENGINE_INTERFACE_VERSION,
  MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES,
  EnginePlanningRequest,
} from "./planning.js";
import { sha256Digest, strictJsonLoads, validateDigest, validateRef } from "./protocol.js";
import { validatePlan, validateTask } from "./source_execution.js";

export const MAX_ENGINE_PROVIDER_EXECUTION_REQUEST_BYTES =
  MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES;
export const MAX_ENGINE_PROVIDER_EXECUTION_RESPONSE_BYTES = 1024 * 1024;
export const MAX_ENGINE_PROVIDER_OUTPUT_BYTES = 256 * 1024;
export const MAX_ENGINE_PROVIDER_OUTPUT_TOKENS = 65_536;

const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const PROVIDER_STATUSES = new Set([
  "succeeded",
  "failed",
  "rejected",
  "timed_out",
  "dispatch_uncertain",
]);
const FAILURE_CODES = new Set([
  "policy_rejected",
  "source_unavailable",
  "source_integrity_mismatch",
  "resource_limit",
  "unsupported_operation",
  "internal",
]);
const USAGE_FIELDS = new Set([
  "state",
  "uncached_input_tokens",
  "cache_write_input_tokens",
  "cache_read_input_tokens",
  "total_input_tokens",
  "output_tokens",
]);
const USAGE_COUNTERS = [
  "uncached_input_tokens",
  "cache_write_input_tokens",
  "cache_read_input_tokens",
  "total_input_tokens",
  "output_tokens",
] as const;
const COST_BASES = new Set(["unavailable", "usage_priced_estimate", "observed_charge"]);

type JsonRecord = Record<string, unknown>;

export type EngineProviderExecutionOutput = Readonly<{
  content: string;
  sha256_digest: string;
}>;

export type EngineProviderExecutionUsage = Readonly<{
  state: "measured" | "estimated" | "unavailable";
  uncached_input_tokens: number | null;
  cache_write_input_tokens: number | null;
  cache_read_input_tokens: number | null;
  total_input_tokens: number | null;
  output_tokens: number | null;
}>;

export type EngineProviderExecutionCost = Readonly<
  | { basis: "unavailable" }
  | { basis: "usage_priced_estimate" | "observed_charge"; micros: number }
>;

export type EngineProviderExecutionFailure = Readonly<{
  code:
    | "policy_rejected"
    | "source_unavailable"
    | "source_integrity_mismatch"
    | "resource_limit"
    | "unsupported_operation"
    | "internal";
  retryable_by_host: false;
  recovery_ref?: string | null;
}>;

export type EngineProviderExecutionResponse = Readonly<{
  schema_version: 1;
  transport_version: 1;
  engine_interface_version: "1.0.0";
  attempt_id: string;
  task_id: string;
  plan_id: string;
  context_digest: string;
  request_digest: string;
  provider: string;
  model: string;
  status: "succeeded" | "failed" | "rejected" | "timed_out" | "dispatch_uncertain";
  acceptance: "unknown";
  output?: EngineProviderExecutionOutput | null;
  usage: EngineProviderExecutionUsage;
  cost: EngineProviderExecutionCost;
  failure?: EngineProviderExecutionFailure | null;
}>;

export type ProviderExecutionRequestValidation = Readonly<{
  task: JsonRecord;
  plan: JsonRecord;
  maxOutputTokens: number;
}>;

function inputInteger(
  value: unknown,
  fieldName: string,
  minimum = 0,
  maximum = MAX_SAFE_INTEGER,
): number {
  if (
    typeof value !== "number"
    || !Number.isSafeInteger(value)
    || value < minimum
    || value > maximum
  ) {
    throw new ValidationError(fieldName + " is outside its protocol bounds");
  }
  return value;
}

function protocolInteger(
  value: unknown,
  fieldName: string,
  minimum = 0,
  maximum = MAX_SAFE_INTEGER,
): number {
  if (
    typeof value !== "number"
    || !Number.isSafeInteger(value)
    || value < minimum
    || value > maximum
  ) {
    throw new EngineProtocolError(fieldName + " is outside its protocol bounds");
  }
  return value;
}

function protocolRecord(value: unknown, fieldName: string): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new EngineProtocolError(fieldName + " must be an object");
  }
  return value as JsonRecord;
}

function exactKeys(
  value: JsonRecord,
  expected: ReadonlySet<string>,
  fieldName: string,
): void {
  const keys = Object.keys(value);
  if (keys.length !== expected.size || keys.some((key) => !expected.has(key))) {
    throw new EngineProtocolError(fieldName + " fields do not match the v1 contract");
  }
}

function protocolText(
  value: unknown,
  fieldName: string,
  maximum = 1024,
  nonempty = true,
  controls = true,
): string {
  if (typeof value !== "string") {
    throw new EngineProtocolError(fieldName + " must be a string");
  }
  for (const character of value) {
    const codePoint = character.codePointAt(0) as number;
    if (
      codePoint >= 0xd800
      && codePoint <= 0xdfff
    ) {
      throw new EngineProtocolError(fieldName + " is not valid UTF-8");
    }
    if (controls && (codePoint <= 0x1f || (codePoint >= 0x7f && codePoint <= 0x9f))) {
      throw new EngineProtocolError(fieldName + " contains a control character");
    }
  }
  const bytes = Buffer.byteLength(value, "utf8");
  if ((nonempty && bytes === 0) || bytes > maximum) {
    throw new EngineProtocolError(fieldName + " exceeds its byte bound");
  }
  return value;
}

function providerIntegerPaths(): readonly (readonly (string | number)[])[] {
  return [
    ["schema_version"],
    ["transport_version"],
    ...USAGE_COUNTERS.map((field) => ["usage", field]),
    ["cost", "micros"],
  ];
}

function parseUsage(value: unknown): EngineProviderExecutionUsage {
  const raw = protocolRecord(value, "provider usage");
  exactKeys(raw, USAGE_FIELDS, "provider usage");
  const state = raw.state;
  if (state !== "measured" && state !== "estimated" && state !== "unavailable") {
    throw new EngineProtocolError("provider usage state is unsupported");
  }
  const counters: Record<string, number | null> = {};
  for (const field of USAGE_COUNTERS) {
    const counter = raw[field];
    counters[field] = counter === null
      ? null
      : protocolInteger(counter, "provider usage." + field, 0, MAX_SAFE_INTEGER);
  }
  if (state === "unavailable") {
    if (Object.values(counters).some((counter) => counter !== null)) {
      throw new EngineProtocolError("unavailable provider usage must omit every counter");
    }
  } else {
    if (Object.values(counters).some((counter) => counter === null)) {
      throw new EngineProtocolError(
        "measured or estimated provider usage requires every counter",
      );
    }
    const expected = (counters.uncached_input_tokens as number)
      + (counters.cache_write_input_tokens as number)
      + (counters.cache_read_input_tokens as number);
    if (counters.total_input_tokens !== expected) {
      throw new EngineProtocolError(
        "provider usage total_input_tokens does not match components",
      );
    }
  }
  return {
    state,
    uncached_input_tokens: counters.uncached_input_tokens ?? null,
    cache_write_input_tokens: counters.cache_write_input_tokens ?? null,
    cache_read_input_tokens: counters.cache_read_input_tokens ?? null,
    total_input_tokens: counters.total_input_tokens ?? null,
    output_tokens: counters.output_tokens ?? null,
  };
}

function parseCost(value: unknown): EngineProviderExecutionCost {
  const raw = protocolRecord(value, "provider cost");
  const basis = raw.basis;
  if (basis === "unavailable") {
    exactKeys(raw, new Set(["basis"]), "unavailable provider cost");
    return { basis: "unavailable" };
  }
  if (typeof basis !== "string" || !COST_BASES.has(basis)) {
    throw new EngineProtocolError("provider cost basis is unsupported");
  }
  exactKeys(raw, new Set(["basis", "micros"]), "provider cost");
  const micros = protocolInteger(raw.micros, "provider cost.micros", 0, MAX_SAFE_INTEGER);
  if (basis === "usage_priced_estimate") return { basis, micros };
  return { basis: "observed_charge", micros };
}

function parseFailure(value: unknown): EngineProviderExecutionFailure {
  const raw = protocolRecord(value, "provider failure");
  const allowed = new Set(["code", "retryable_by_host", "recovery_ref"]);
  const required = new Set(["code", "retryable_by_host"]);
  if (
    ![...required].every((key) => key in raw)
    || Object.keys(raw).some((key) => !allowed.has(key))
  ) {
    throw new EngineProtocolError("provider failure fields do not match the v1 contract");
  }
  const code = raw.code;
  if (typeof code !== "string" || !FAILURE_CODES.has(code)) {
    throw new EngineProtocolError("provider failure code is unsupported");
  }
  if (typeof raw.retryable_by_host !== "boolean") {
    throw new EngineProtocolError("provider failure retryable_by_host must be boolean");
  }
  if (raw.retryable_by_host) {
    throw new EngineProtocolError("provider failures must not request host retry");
  }
  let recoveryRef: string | null | undefined = raw.recovery_ref as string | null | undefined;
  if (recoveryRef !== undefined && recoveryRef !== null) {
    try {
      recoveryRef = validateRef(recoveryRef, "provider failure recovery_ref");
    } catch (cause) {
      throw new EngineProtocolError("provider failure recovery_ref is invalid", { cause });
    }
  }
  if (code === "policy_rejected" && recoveryRef !== undefined && recoveryRef !== null) {
    throw new EngineProtocolError("policy rejection cannot request recovery");
  }
  if (
    (code === "source_unavailable" || code === "source_integrity_mismatch")
    && (recoveryRef === undefined || recoveryRef === null)
  ) {
    throw new EngineProtocolError("source failure requires a recovery_ref");
  }
  return {
    code: code as EngineProviderExecutionFailure["code"],
    retryable_by_host: false,
    ...(recoveryRef === undefined ? {} : { recovery_ref: recoveryRef }),
  };
}

function parseOutput(value: unknown): EngineProviderExecutionOutput {
  const raw = protocolRecord(value, "provider output");
  exactKeys(raw, new Set(["content", "sha256_digest"]), "provider output");
  const content = protocolText(
    raw.content,
    "provider output.content",
    MAX_ENGINE_PROVIDER_OUTPUT_BYTES,
    false,
    false,
  );
  let digest: string;
  try {
    digest = validateDigest(raw.sha256_digest, "provider output.sha256_digest");
  } catch (cause) {
    throw new EngineProtocolError("provider output digest is invalid", { cause });
  }
  if (digest !== sha256Digest(content)) {
    throw new EngineProtocolError("provider output digest does not match content");
  }
  return { content, sha256_digest: digest };
}

export function validateProviderExecutionRequest(
  task: unknown,
  plan: unknown,
  request: EnginePlanningRequest,
  tenantId: string,
  maxOutputTokens: unknown,
): ProviderExecutionRequestValidation {
  if (!(request instanceof EnginePlanningRequest)) {
    throw new ValidationError("providerExecute requires EnginePlanningRequest");
  }
  const normalizedTask = validateTask(task, request, tenantId);
  const normalizedPlan = validatePlan(plan, String(normalizedTask.task_id), false, true);
  const contextPlanId = normalizedPlan.context_plan_id;
  if (typeof contextPlanId !== "string" || contextPlanId.length === 0) {
    throw new ValidationError("providerExecute requires a concrete context_plan_id");
  }
  inputInteger(
    normalizedPlan.context_budget_tokens,
    "plan.context_budget_tokens",
    1,
    request.budgetTokens,
  );
  const provider = normalizedPlan.provider;
  const model = normalizedPlan.model;
  if (
    provider === "local-native"
    || provider === "auto"
    || model === "local-native"
    || model === "auto"
  ) {
    throw new ValidationError("providerExecute requires a concrete non-local provider plan");
  }
  if (
    normalizedPlan.max_retries !== 0
    || !Array.isArray(normalizedPlan.fallback_refs)
    || normalizedPlan.fallback_refs.length !== 0
  ) {
    throw new ValidationError("providerExecute does not permit host retries or fallbacks");
  }
  const outputTokens = inputInteger(
    maxOutputTokens,
    "maxOutputTokens",
    1,
    MAX_ENGINE_PROVIDER_OUTPUT_TOKENS,
  );
  return { task: normalizedTask, plan: normalizedPlan, maxOutputTokens: outputTokens };
}

export function parseProviderExecutionResponse(
  raw: Uint8Array | string,
  task: Readonly<Record<string, unknown>>,
  plan: Readonly<Record<string, unknown>>,
  tenantId: string,
): EngineProviderExecutionResponse {
  const rawBytes = typeof raw === "string" ? Buffer.byteLength(raw, "utf8") : raw.byteLength;
  if (rawBytes > MAX_ENGINE_PROVIDER_EXECUTION_RESPONSE_BYTES) {
    throw new EngineProtocolError("provider execution response exceeds its byte bound");
  }
  let value: unknown;
  try {
    value = strictJsonLoads(
      raw,
      "Enterprise Engine provider execution response",
      providerIntegerPaths(),
    );
  } catch (cause) {
    throw new EngineProtocolError(
      "Enterprise Engine provider execution response is not valid JSON",
      { cause },
    );
  }
  const response = protocolRecord(value, "provider execution response");
  const required = new Set([
    "schema_version",
    "transport_version",
    "engine_interface_version",
    "attempt_id",
    "task_id",
    "plan_id",
    "context_digest",
    "request_digest",
    "provider",
    "model",
    "status",
    "acceptance",
    "usage",
    "cost",
  ]);
  const allowed = new Set([...required, "output", "failure"]);
  if (
    Object.keys(response).some((key) => !allowed.has(key))
    || ![...required].every((key) => key in response)
  ) {
    throw new EngineProtocolError(
      "provider execution response fields do not match the v1 contract",
    );
  }
  protocolInteger(response.schema_version, "response.schema_version", 1, 1);
  protocolInteger(response.transport_version, "response.transport_version", 1, 1);
  if (response.engine_interface_version !== ENGINE_INTERFACE_VERSION) {
    throw new EngineProtocolError("provider execution Engine interface is unsupported");
  }
  if (task.tenant_id !== tenantId) {
    throw new EngineProtocolError("provider execution task tenant does not bind the client");
  }
  const attemptId = protocolText(response.attempt_id, "response.attempt_id");
  const taskId = protocolText(response.task_id, "response.task_id");
  const planId = protocolText(response.plan_id, "response.plan_id");
  if (taskId !== task.task_id || planId !== plan.plan_id) {
    throw new EngineProtocolError("provider execution response task or plan does not bind request");
  }
  let contextDigest: string;
  let requestDigest: string;
  try {
    contextDigest = validateDigest(response.context_digest, "response.context_digest");
    requestDigest = validateDigest(response.request_digest, "response.request_digest");
  } catch (cause) {
    throw new EngineProtocolError("provider execution response digest is invalid", { cause });
  }
  const provider = protocolText(response.provider, "response.provider");
  const model = protocolText(response.model, "response.model");
  if (provider !== plan.provider || model !== plan.model) {
    throw new EngineProtocolError(
      "provider execution response provider or model does not bind plan",
    );
  }
  if (typeof response.status !== "string" || !PROVIDER_STATUSES.has(response.status)) {
    throw new EngineProtocolError("provider execution response status is unsupported");
  }
  if (response.acceptance !== "unknown") {
    throw new EngineProtocolError("provider execution response acceptance must remain unknown");
  }
  const usage = parseUsage(response.usage);
  const cost = parseCost(response.cost);
  if (cost.basis === "usage_priced_estimate" && usage.state === "unavailable") {
    throw new EngineProtocolError("provider cost cannot price unavailable usage");
  }
  const output = response.output === undefined || response.output === null
    ? null
    : parseOutput(response.output);
  const failure = response.failure === undefined || response.failure === null
    ? null
    : parseFailure(response.failure);
  if (response.status === "succeeded") {
    if (output === null || failure !== null) {
      throw new EngineProtocolError("succeeded provider response requires output only");
    }
  } else if (output !== null || failure === null) {
    throw new EngineProtocolError("non-success provider response requires failure only");
  }
  return {
    ...response,
    schema_version: 1,
    transport_version: 1,
    engine_interface_version: "1.0.0",
    attempt_id: attemptId,
    task_id: taskId,
    plan_id: planId,
    context_digest: contextDigest,
    request_digest: requestDigest,
    provider,
    model,
    status: response.status as EngineProviderExecutionResponse["status"],
    acceptance: "unknown",
    ...(response.output === undefined ? {} : { output }),
    usage,
    cost,
    ...(response.failure === undefined ? {} : { failure }),
  };
}

export const _parse_provider_execution_response = parseProviderExecutionResponse;
