// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** Strict Enterprise source-execution DTO validation. */

import { parseResponse } from "./engine.js";
import { EngineProtocolError, ValidationError } from "./errors.js";
import {
  ENGINE_INTERFACE_VERSION,
  EnginePlanningRequest,
  parseSourcePlan,
  type EngineContextPlan,
  type EngineSourcePlan,
} from "./planning.js";
import {
  canonicalBytes,
  SCHEMA_VERSION,
  sha256Digest,
  strictJsonLoads,
  TRANSPORT_VERSION,
  validateDigest,
  validateRef,
} from "./protocol.js";

export const MAX_ENGINE_SOURCE_RECEIPT_DOCUMENT_BYTES = 1024 * 1024;
export const MAX_ENGINE_SOURCE_EXECUTION_V2_RESPONSE_BYTES = 4 * 1024 * 1024;
export const MAX_ENGINE_SOURCE_EXECUTION_V2_WRAPPER_BYTES = 64 * 1024;
export const MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES =
  MAX_ENGINE_SOURCE_EXECUTION_V2_RESPONSE_BYTES + MAX_ENGINE_SOURCE_EXECUTION_V2_WRAPPER_BYTES;
export const MAX_ENGINE_SOURCE_EXECUTION_REQUEST_BYTES = 1024 * 1024;

const V2_SCHEMA_VERSION = 2;
const MAX_U64 = Number.MAX_SAFE_INTEGER;
const MAX_U32 = 0xffffffff;
const MAX_TEXT_BYTES = 1024 * 1024;
const MAX_IDENTIFIER_BYTES = 256;
const MAX_REFERENCE_BYTES = 1024;
const LOCAL_NATIVE = "local-native";
const CAPABILITY = "capability://leanctx/context-optimization";
const CAPABILITY_VERSION = "1.0.0";
const INPUT_REF_PREFIX = "input:source-materialization-sha256:";
const SOURCE_PLAN_EVIDENCE_PREFIX = "artifact://execution/evidence/";
const TASK_REF_PREFIX = "task:sha256:";
const PLAN_REF_PREFIX = "plan:sha256:";
const SEMVER_RE = /^[0-9]+\.[0-9]+\.[0-9]+$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TIMESTAMP_RE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z$/;

type JsonRecord = Record<string, unknown>;
type JsonPath = readonly (string | number)[];

export type EngineSourceExecution = Readonly<{
  schema_version: 1;
  transport_version: 1;
  engine_interface_version: "1.0.0";
  source_plan: EngineSourcePlan;
  execution_plan: EngineContextPlan;
  view: Readonly<{ text: string; output_ref: string; output_digest: string }>;
  invocation: Readonly<JsonRecord>;
  observation: Readonly<JsonRecord>;
  canonical_receipt: Readonly<JsonRecord>;
}>;

export type EngineSourceExecutionV2 = Readonly<{
  schema_version: 2;
  tenant_id: string;
  governance_revision: number;
  execution: Readonly<{
    schema_version: 2;
    execution: EngineSourceExecution;
    receipt_document_json: string;
  }>;
}>;

function fail(message: string, protocol = false): never {
  if (protocol) throw new EngineProtocolError(message);
  throw new ValidationError(message);
}

function record(value: unknown, fieldName: string, protocol = false): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    fail(fieldName + " must be an object", protocol);
  }
  return value as JsonRecord;
}

function exactKeys(value: JsonRecord, expected: ReadonlySet<string>, fieldName: string, protocol = false): void {
  const keys = Object.keys(value);
  if (keys.length !== expected.size || keys.some((key) => !expected.has(key))) {
    fail(fieldName + " fields do not match the v1 contract", protocol);
  }
}

function utf8Bytes(value: unknown, fieldName: string, protocol = false): Buffer {
  if (typeof value !== "string") fail(fieldName + " must be a string", protocol);
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!Number.isFinite(next) || next < 0xdc00 || next > 0xdfff) {
        fail(fieldName + " is not valid UTF-8", protocol);
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      fail(fieldName + " is not valid UTF-8", protocol);
    }
  }
  return Buffer.from(value, "utf8");
}

function text(value: unknown, fieldName: string, maximum = MAX_REFERENCE_BYTES, protocol = false): string {
  const bytes = utf8Bytes(value, fieldName, protocol);
  const stringValue = value as string;
  if (bytes.byteLength === 0 || bytes.byteLength > maximum || stringValue.includes("\0")) {
    fail(fieldName + " exceeds its byte bound", protocol);
  }
  if ([...stringValue].some((character) => {
    const code = character.codePointAt(0) as number;
    return code <= 0x1f || (code >= 0x7f && code <= 0x9f);
  })) {
    fail(fieldName + " contains a control character", protocol);
  }
  return stringValue;
}

function integer(value: unknown, fieldName: string, minimum = 0, maximum = MAX_U64, protocol = false): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) {
    fail(fieldName + " is outside its protocol bounds", protocol);
  }
  return value;
}

function exactVersion(value: unknown, fieldName: string, expected: number, protocol = false): void {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value !== expected) {
    fail(fieldName + " is unsupported", protocol);
  }
}

function stringList(value: unknown, fieldName: string, protocol = false): string[] {
  if (!Array.isArray(value) || value.length > 256) fail(fieldName + " has an invalid shape", protocol);
  const result = value.map((item, index) => text(item, `${fieldName}[${index}]`, MAX_REFERENCE_BYTES, protocol));
  if (new Set(result).size !== result.length) fail(fieldName + " contains duplicates", protocol);
  return result;
}

function timestamp(value: unknown, fieldName: string, protocol = false): string {
  const normalized = text(value, fieldName, MAX_IDENTIFIER_BYTES, protocol);
  const match = TIMESTAMP_RE.exec(normalized);
  if (match === null) fail(fieldName + " must use canonical UTC timestamp syntax", protocol);
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year === 0 || month < 1 || month > 12 || day < 1 || day > (days[month - 1] as number) || hour > 23 || minute > 59 || second > 59) {
    fail(fieldName + " contains an invalid date or time", protocol);
  }
  return normalized;
}

function canonicalUuid(value: unknown, fieldName: string): string {
  if (typeof value !== "string" || value.trim() !== value || !UUID_RE.test(value) || /^0{8}-0{4}-0{4}-0{4}-0{12}$/i.test(value)) {
    throw new ValidationError(fieldName + " must be a non-nil canonical UUID");
  }
  return value.toLowerCase();
}

export function normalizeSourceIds(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.length > 64) throw new ValidationError("source_ids exceeds the Engine source bound");
  const normalized = value.map((item) => canonicalUuid(item, "source_id"));
  if (new Set(normalized).size !== normalized.length) throw new ValidationError("source_ids must not contain duplicates");
  return normalized;
}

export function validatePlanningEvaluationTime(value: unknown): string {
  return timestamp(value, "planning_evaluation_time");
}

const TASK_REQUIRED = new Set(["schema_version", "task_id", "trace_id", "project_id", "session_id", "agent_id", "complexity", "created_at"]);
const TASK_OPTIONAL = ["parent_task_id", "tenant_id", "intent", "task_class", "risk_class", "quality_requirement_milli", "cost_budget_micros", "latency_budget_ms", "data_classification", "region_policy_ref", "model_policy_ref", "context_state_ref", "outcome_contract_ref"];

function validateTask(value: unknown, request: EnginePlanningRequest, tenantId: string, protocol = false): JsonRecord {
  const raw = record(value, "task", protocol);
  for (const key of TASK_REQUIRED) if (!(key in raw)) fail("task is missing a required field", protocol);
  exactVersion(raw.schema_version, "task.schema_version", SCHEMA_VERSION, protocol);
  const taskId = text(raw.task_id, "task.task_id", MAX_IDENTIFIER_BYTES, protocol);
  if (taskId !== request.taskId) fail("task.task_id does not bind the planning request", protocol);
  for (const field of ["trace_id", "project_id", "session_id", "agent_id"]) text(raw[field], "task." + field, MAX_REFERENCE_BYTES, protocol);
  if (typeof raw.complexity !== "string" || !new Set(["unknown", "low", "medium", "high", "critical"]).has(raw.complexity)) fail("task.complexity is unsupported", protocol);
  text(raw.created_at, "task.created_at", MAX_REFERENCE_BYTES, protocol);
  if (raw.tenant_id !== tenantId) fail("task.tenant_id does not bind the authenticated tenant", protocol);
  if (raw.parent_task_id !== undefined && raw.parent_task_id !== null) {
    const parent = text(raw.parent_task_id, "task.parent_task_id", MAX_REFERENCE_BYTES, protocol);
    if (parent === taskId) fail("task cannot be its own parent", protocol);
  }
  for (const field of ["intent", "task_class", "region_policy_ref", "model_policy_ref", "context_state_ref", "outcome_contract_ref"]) {
    if (raw[field] !== undefined && raw[field] !== null) text(raw[field], "task." + field, MAX_REFERENCE_BYTES, protocol);
  }
  if (raw.risk_class !== undefined && raw.risk_class !== null && (typeof raw.risk_class !== "string" || !new Set(["low", "medium", "high", "critical"]).has(raw.risk_class))) fail("task.risk_class is unsupported", protocol);
  if (raw.quality_requirement_milli !== undefined && raw.quality_requirement_milli !== null) integer(raw.quality_requirement_milli, "task.quality_requirement_milli", 0, 1000, protocol);
  for (const field of ["cost_budget_micros", "latency_budget_ms"]) if (raw[field] !== undefined && raw[field] !== null) integer(raw[field], "task." + field, 0, MAX_U64, protocol);
  if (raw.data_classification !== undefined && raw.data_classification !== null && (typeof raw.data_classification !== "string" || !new Set(["Public", "Internal", "Confidential", "Restricted"]).has(raw.data_classification))) fail("task.data_classification is unsupported", protocol);
  const normalized = { ...raw };
  for (const field of TASK_OPTIONAL) if (normalized[field] === null) delete normalized[field];
  try {
    if (canonicalBytes(normalized).byteLength > 16 * 1024) fail("task exceeds its byte bound", protocol);
  } catch (cause) {
    if (cause instanceof ValidationError) fail("task contains non-canonical JSON data", protocol);
    throw cause;
  }
  return normalized;
}

const PLAN_REQUIRED = new Set(["schema_version", "plan_id", "task_id", "context_budget_tokens", "context_strategy", "knowledge_refs", "capability_ids", "model", "provider", "reasoning_allocation_milli", "max_retries", "fallback_refs", "stop_condition", "expected_cost_micros", "expected_quality_milli", "expected_latency_ms"]);

function validatePlan(value: unknown, taskId: string, protocol = false, allowContextPlanId = false): JsonRecord {
  const raw = record(value, "plan", protocol);
  for (const key of PLAN_REQUIRED) if (!(key in raw)) fail("plan is missing a required field", protocol);
  exactVersion(raw.schema_version, "plan.schema_version", SCHEMA_VERSION, protocol);
  if (text(raw.task_id, "plan.task_id", MAX_IDENTIFIER_BYTES, protocol) !== taskId) fail("plan.task_id does not bind task.task_id", protocol);
  text(raw.plan_id, "plan.plan_id", MAX_IDENTIFIER_BYTES, protocol);
  const budget = integer(raw.context_budget_tokens, "plan.context_budget_tokens", 0, MAX_U64, protocol);
  if (typeof raw.context_strategy !== "string" || !new Set(["minimal", "balanced", "comprehensive", "cached_first"]).has(raw.context_strategy)) fail("plan.context_strategy is unsupported", protocol);
  stringList(raw.knowledge_refs, "plan.knowledge_refs", protocol);
  const capabilities = stringList(raw.capability_ids, "plan.capability_ids", protocol);
  if (capabilities.length === 0) fail("plan.capability_ids must not be empty", protocol);
  text(raw.model, "plan.model", MAX_REFERENCE_BYTES, protocol);
  text(raw.provider, "plan.provider", MAX_REFERENCE_BYTES, protocol);
  integer(raw.reasoning_allocation_milli, "plan.reasoning_allocation_milli", 0, 1000, protocol);
  integer(raw.max_retries, "plan.max_retries", 0, MAX_U32, protocol);
  stringList(raw.fallback_refs, "plan.fallback_refs", protocol);
  if (typeof raw.stop_condition !== "string" || !new Set(["on_completion", "on_acceptance", "on_budget_exhaustion", "on_error", "manual"]).has(raw.stop_condition)) fail("plan.stop_condition is unsupported", protocol);
  integer(raw.expected_cost_micros, "plan.expected_cost_micros", 0, MAX_U64, protocol);
  integer(raw.expected_quality_milli, "plan.expected_quality_milli", 0, 1000, protocol);
  integer(raw.expected_latency_ms, "plan.expected_latency_ms", 0, MAX_U64, protocol);
  if (raw.context_plan_id !== undefined && raw.context_plan_id !== null) {
    if (!allowContextPlanId) fail("source execution request plan must not contain context_plan_id", protocol);
    text(raw.context_plan_id, "plan.context_plan_id", MAX_IDENTIFIER_BYTES, protocol);
  }
  const budgetPolicy = raw.context_budget_policy;
  if (budgetPolicy !== undefined && budgetPolicy !== null) {
    const policy = record(budgetPolicy, "plan.context_budget_policy", protocol);
    if (policy.kind === "token_limit") {
      exactKeys(policy, new Set(["kind", "tokens"]), "plan.context_budget_policy", protocol);
      if (integer(policy.tokens, "plan.context_budget_policy.tokens", 0, MAX_U64, protocol) !== budget) fail("plan.context_budget_policy disagrees with context_budget_tokens", protocol);
    } else if (policy.kind === "no_token_limit") {
      exactKeys(policy, new Set(["kind"]), "plan.context_budget_policy", protocol);
      if (budget !== 0) fail("plan.context_budget_policy disagrees with context_budget_tokens", protocol);
    } else fail("plan.context_budget_policy.kind is unsupported", protocol);
  }
  const estimates = raw.estimates;
  if (estimates !== undefined && estimates !== null) {
    const item = record(estimates, "plan.estimates", protocol);
    exactKeys(item, new Set(["cost_micros", "quality_milli", "latency_ms"]), "plan.estimates", protocol);
    for (const field of ["cost_micros", "quality_milli", "latency_ms"]) {
      if (item[field] !== null) integer(item[field], "plan.estimates." + field, 0, field === "quality_milli" ? 1000 : MAX_U64, protocol);
    }
    if (item.cost_micros !== null && item.cost_micros !== raw.expected_cost_micros) fail("plan.estimates.cost_micros disagrees with legacy scalar", protocol);
    if (item.quality_milli !== null && item.quality_milli !== raw.expected_quality_milli) fail("plan.estimates.quality_milli disagrees with legacy scalar", protocol);
    if (item.latency_ms !== null && item.latency_ms !== raw.expected_latency_ms) fail("plan.estimates.latency_ms disagrees with legacy scalar", protocol);
  }
  for (const field of ["policy_decision_ref", "scheduler_decision_ref", "executor_agent_id"]) if (raw[field] !== undefined && raw[field] !== null) text(raw[field], "plan." + field, MAX_REFERENCE_BYTES, protocol);
  if (raw.capability_bindings !== undefined && raw.capability_bindings !== null) {
    if (!Array.isArray(raw.capability_bindings) || raw.capability_bindings.length !== capabilities.length) fail("plan.capability_bindings has an invalid shape", protocol);
    const seen = new Set<string>();
    raw.capability_bindings.forEach((value, index) => {
      const binding = record(value, `plan.capability_bindings[${index}]`, protocol);
      exactKeys(binding, new Set(["capability_id", "version"]), "plan.capability_bindings entry", protocol);
      const capabilityId = text(binding.capability_id, "plan.capability_bindings.capability_id", MAX_REFERENCE_BYTES, protocol);
      const version = text(binding.version, "plan.capability_bindings.version", MAX_REFERENCE_BYTES, protocol);
      if (!capabilities.includes(capabilityId) || seen.has(capabilityId) || !SEMVER_RE.test(version)) fail("plan.capability_bindings does not match capability_ids", protocol);
      seen.add(capabilityId);
    });
  }
  const normalized = { ...raw };
  for (const field of ["context_plan_id", "context_budget_policy", "estimates", "policy_decision_ref", "scheduler_decision_ref", "executor_agent_id", "capability_bindings"]) if (normalized[field] === null) delete normalized[field];
  if (!allowContextPlanId) delete normalized.context_plan_id;
  try { canonicalBytes(normalized); } catch (cause) { if (cause instanceof ValidationError) fail("plan contains non-canonical JSON data", protocol); throw cause; }
  return normalized;
}

export function validateExecutionRequest(
  task: unknown,
  plan: unknown,
  request: EnginePlanningRequest,
  tenantId: string,
): { task: JsonRecord; plan: JsonRecord } {
  if (!(request instanceof EnginePlanningRequest)) throw new ValidationError("context_execute requires EnginePlanningRequest");
  const normalizedTask = validateTask(task, request, tenantId);
  const normalizedPlan = validatePlan(plan, String(normalizedTask.task_id), false, false);
  if (normalizedPlan.provider !== LOCAL_NATIVE || normalizedPlan.model !== LOCAL_NATIVE) throw new ValidationError("context_execute requires a local-native plan");
  if (Buffer.compare(canonicalBytes(normalizedPlan.capability_ids), canonicalBytes([CAPABILITY])) !== 0) throw new ValidationError("context_execute requires the local-native capability");
  if (Buffer.compare(canonicalBytes(normalizedPlan.capability_bindings), canonicalBytes([{ capability_id: CAPABILITY, version: CAPABILITY_VERSION }])) !== 0) throw new ValidationError("context_execute requires a bound local-native capability");
  return { task: normalizedTask, plan: normalizedPlan };
}

function validateSourceScope(sourcePlan: EngineSourcePlan, requestedIds: readonly string[]): void {
  const requested = new Set(requestedIds);
  const selections = record(sourcePlan.result.plan, "source plan result.plan", true).selections;
  if (!Array.isArray(selections)) fail("Enterprise Engine selections are invalid", true);
  for (const value of selections) {
    const selection = record(value, "source plan selection", true);
    if (!requested.has(String(selection.source_ref)) || !requested.has(String(selection.provider))) fail("Enterprise Engine selection is outside requested sources", true);
  }
  for (const binding of sourcePlan.source_bindings) {
    if (!requested.has(binding.object_ref) || !requested.has(binding.source_id)) fail("Enterprise Engine source binding is outside requested sources", true);
    if (binding.permission !== "permitted") fail("Enterprise Engine selected source is not permitted", true);
  }
}

function digestSuffix(value: unknown, prefix: string, fieldName: string): string {
  if (typeof value !== "string" || !value.startsWith(prefix)) fail(fieldName + " has an unsupported reference prefix", true);
  try { return validateDigest("sha256:" + value.slice(prefix.length), fieldName); } catch (cause) { throw new EngineProtocolError(fieldName + " is not digest-bound", { cause }); }
}

function parseReceipt(value: unknown): JsonRecord {
  const raw = record(value, "execution.canonical_receipt", true);
  exactKeys(raw, new Set(["receipt_id", "receipt_ref", "receipt_digest", "outcome"]), "execution.canonical_receipt", true);
  const receiptId = text(raw.receipt_id, "execution.canonical_receipt.receipt_id", MAX_REFERENCE_BYTES, true);
  const digest = validateDigest(raw.receipt_digest, "execution.canonical_receipt.receipt_digest");
  const receiptRef = validateRef(raw.receipt_ref, "execution.canonical_receipt.receipt_ref");
  if (receiptRef !== "id:" + digest) fail("execution canonical receipt reference is not digest-bound", true);
  if (raw.outcome !== "unknown") fail("execution canonical receipt outcome must remain unknown", true);
  return { receipt_id: receiptId, receipt_ref: receiptRef, receipt_digest: digest, outcome: "unknown" };
}

function validateLineage(invocation: JsonRecord, sourcePlanDigest: string, executionPlanDigest: string, taskDigest: string): void {
  const refs = invocation.source_refs;
  if (!Array.isArray(refs) || refs.length !== 4) fail("execution invocation must contain four lineage references", true);
  const inputRef = invocation.input_ref;
  const inputDigest = invocation.input_digest;
  if (digestSuffix(inputRef, INPUT_REF_PREFIX, "execution.invocation.input_ref") !== inputDigest) fail("execution input_ref does not bind input_digest", true);
  const counts = { input: 0, evidence: 0, task: 0, plan: 0 };
  for (const reference of refs) {
    if (typeof reference !== "string") fail("execution invocation source refs are invalid", true);
    if (reference.startsWith(INPUT_REF_PREFIX)) { counts.input += 1; if (digestSuffix(reference, INPUT_REF_PREFIX, "execution.invocation.source_refs") !== inputDigest) fail("execution input lineage does not bind input_digest", true); }
    else if (reference.startsWith(SOURCE_PLAN_EVIDENCE_PREFIX)) { counts.evidence += 1; if (digestSuffix(reference, SOURCE_PLAN_EVIDENCE_PREFIX, "execution.invocation.source_refs") !== sourcePlanDigest) fail("execution source-plan evidence is not digest-bound", true); }
    else if (reference.startsWith(TASK_REF_PREFIX)) { counts.task += 1; if (digestSuffix(reference, TASK_REF_PREFIX, "execution.invocation.source_refs") !== taskDigest) fail("execution task evidence is not digest-bound", true); }
    else if (reference.startsWith(PLAN_REF_PREFIX)) { counts.plan += 1; if (digestSuffix(reference, PLAN_REF_PREFIX, "execution.invocation.source_refs") !== executionPlanDigest) fail("execution plan evidence is not digest-bound", true); }
    else fail("execution invocation contains unknown lineage reference", true);
  }
  if (counts.input !== 1 || counts.evidence !== 1 || counts.task !== 1 || counts.plan !== 1) fail("execution invocation lineage is incomplete", true);
}

function sourcePlanIntegerPaths(paths: JsonPath[], value: unknown, base: JsonPath): void {
  const envelope = value as JsonRecord;
  const result = envelope && typeof envelope === "object" && !Array.isArray(envelope) ? envelope.result as JsonRecord : undefined;
  const plan = result && typeof result === "object" && !Array.isArray(result) ? result.plan as JsonRecord : undefined;
  if (!result || typeof result !== "object" || Array.isArray(result) || !plan || typeof plan !== "object" || Array.isArray(plan)) return;
  const add = (objectValue: unknown, key: string, path: JsonPath): void => { if (objectValue && typeof objectValue === "object" && !Array.isArray(objectValue) && typeof (objectValue as JsonRecord)[key] === "number") paths.push([...path, key]); };
  add(result, "schema_version", [...base, "result"]);
  add(result, "transport_version", [...base, "result"]);
  add(plan, "schema_version", [...base, "result", "plan"]);
  add(plan, "budget_tokens", [...base, "result", "plan"]);
  if (Array.isArray(plan.selections)) plan.selections.forEach((selection, index) => add(selection, "token_count", [...base, "result", "plan", "selections", index]));
  if (plan.provider_stats && typeof plan.provider_stats === "object" && !Array.isArray(plan.provider_stats)) for (const [provider, stats] of Object.entries(plan.provider_stats as JsonRecord)) {
    add(stats, "candidates_offered", [...base, "result", "plan", "provider_stats", provider]);
    add(stats, "candidates_selected", [...base, "result", "plan", "provider_stats", provider]);
    add(stats, "tokens_used", [...base, "result", "plan", "provider_stats", provider]);
  }
  if (Array.isArray(plan.evidence)) plan.evidence.forEach((evidence, index) => add(evidence, "schema_version", [...base, "result", "plan", "evidence", index]));
}

function planIntegerPaths(paths: JsonPath[], value: unknown, base: JsonPath): void {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const objectValue = value as JsonRecord;
  const add = (key: string): void => { if (typeof objectValue[key] === "number") paths.push([...base, key]); };
  for (const key of ["schema_version", "context_budget_tokens", "reasoning_allocation_milli", "max_retries", "expected_cost_micros", "expected_quality_milli", "expected_latency_ms"]) add(key);
  if (objectValue.context_budget_policy && typeof objectValue.context_budget_policy === "object" && !Array.isArray(objectValue.context_budget_policy)) if (typeof (objectValue.context_budget_policy as JsonRecord).tokens === "number") paths.push([...base, "context_budget_policy", "tokens"]);
  if (objectValue.estimates && typeof objectValue.estimates === "object" && !Array.isArray(objectValue.estimates)) for (const key of ["cost_micros", "quality_milli", "latency_ms"]) if (typeof (objectValue.estimates as JsonRecord)[key] === "number") paths.push([...base, "estimates", key]);
}

function knownIntegerPaths(value: JsonRecord): readonly JsonPath[] {
  const paths: JsonPath[] = [];
  const add = (objectValue: unknown, key: string, path: JsonPath): void => { if (objectValue && typeof objectValue === "object" && !Array.isArray(objectValue) && typeof (objectValue as JsonRecord)[key] === "number") paths.push([...path, key]); };
  add(value, "schema_version", []);
  add(value, "governance_revision", []);
  const outer = value.execution as JsonRecord;
  add(outer, "schema_version", ["execution"]);
  const inner = outer && typeof outer === "object" && !Array.isArray(outer) ? outer.execution as JsonRecord : undefined;
  add(inner, "schema_version", ["execution", "execution"]);
  add(inner, "transport_version", ["execution", "execution"]);
  if (inner) {
    sourcePlanIntegerPaths(paths, inner.source_plan, ["execution", "execution", "source_plan"]);
    planIntegerPaths(paths, inner.execution_plan, ["execution", "execution", "execution_plan"]);
    const invocation = inner.invocation as JsonRecord;
    add(invocation, "schema_version", ["execution", "execution", "invocation"]);
    const observation = inner.observation as JsonRecord;
    add(observation, "schema_version", ["execution", "execution", "observation"]);
    if (observation && Array.isArray(observation.measurements)) observation.measurements.forEach((measurement, index) => add(measurement, "value", ["execution", "execution", "observation", "measurements", index]));
    const receiptLink = observation && observation.receipt_link as JsonRecord;
    add(receiptLink, "schema_version", ["execution", "execution", "observation", "receipt_link"]);
  }
  return paths;
}

function decode(raw: Uint8Array | string): JsonRecord {
  const bytes = typeof raw === "string" ? utf8Bytes(raw, "Engine source execution v2 response", true) : Buffer.from(raw);
  if (bytes.byteLength > MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES) fail("Enterprise source execution v2 response exceeds its byte bound", true);
  try {
    const initial = record(strictJsonLoads(raw, "Enterprise source execution v2 response"), "Enterprise source execution v2 response");
    return record(strictJsonLoads(raw, "Enterprise source execution v2 response", knownIntegerPaths(initial)), "Enterprise source execution v2 response");
  } catch (cause) {
    if (cause instanceof EngineProtocolError) throw cause;
    throw new EngineProtocolError("Enterprise source execution v2 response is not valid JSON", { cause });
  }
}

function parseV1Value(
  value: JsonRecord,
  request: EnginePlanningRequest,
  requestedIds: readonly string[],
  task: JsonRecord,
  plan: JsonRecord,
  tenantId: string,
  expectedGovernanceRevision: number,
  expectedBindingDigest: string,
  planningEvaluationTime?: string,
): EngineSourceExecution {
  exactKeys(value, new Set(["schema_version", "tenant_id", "governance_revision", "execution"]), "Enterprise source execution response", true);
  exactVersion(value.schema_version, "response.schema_version", SCHEMA_VERSION, true);
  if (value.tenant_id !== tenantId) fail("Enterprise source execution response tenant binding does not match", true);
  const revision = integer(value.governance_revision, "response.governance_revision", 0, MAX_U64, true);
  if (revision !== expectedGovernanceRevision) fail("Enterprise source execution governance revision does not match", true);
  const executionRaw = record(value.execution, "execution", true);
  exactKeys(executionRaw, new Set(["schema_version", "transport_version", "engine_interface_version", "source_plan", "execution_plan", "view", "invocation", "observation", "canonical_receipt"]), "Enterprise source execution", true);
  exactVersion(executionRaw.schema_version, "execution.schema_version", SCHEMA_VERSION, true);
  exactVersion(executionRaw.transport_version, "execution.transport_version", TRANSPORT_VERSION, true);
  if (executionRaw.engine_interface_version !== ENGINE_INTERFACE_VERSION) fail("Enterprise source execution Engine interface is unsupported", true);
  const sourcePlanRaw = record(executionRaw.source_plan, "execution.source_plan", true);
  let sourcePlan: EngineSourcePlan;
  try { sourcePlan = parseSourcePlan(canonicalBytes(sourcePlanRaw), request); } catch (cause) { throw new EngineProtocolError("Enterprise source execution source plan is invalid", { cause }); }
  validateSourceScope(sourcePlan, requestedIds);
  const bindingDigest = validateDigest(expectedBindingDigest, "expected_binding_digest");
  if (sourcePlan.binding_digest !== bindingDigest) fail("Enterprise source execution binding digest does not match", true);
  const executionPlanRaw = record(executionRaw.execution_plan, "execution.execution_plan", true);
  const executionPlan = validatePlan(executionPlanRaw, request.taskId, true, true);
  const sourceResultPlan = record(record(sourcePlan.result, "source plan result", true).plan, "source plan result.plan", true);
  if (executionPlan.context_plan_id !== sourceResultPlan.context_plan_id) fail("Enterprise source execution plan does not bind source plan", true);
  if (executionPlan.context_budget_tokens !== sourceResultPlan.budget_tokens) fail("Enterprise source execution budget does not bind source plan", true);
  if (executionPlan.provider !== LOCAL_NATIVE || executionPlan.model !== LOCAL_NATIVE || Buffer.compare(canonicalBytes(executionPlan.capability_ids), canonicalBytes([CAPABILITY])) !== 0) fail("Enterprise source execution plan is not local-native", true);
  if (Buffer.compare(canonicalBytes(executionPlan.capability_bindings), canonicalBytes([{ capability_id: CAPABILITY, version: CAPABILITY_VERSION }])) !== 0) fail("Enterprise source execution capability binding is invalid", true);
  const expectedPlan: JsonRecord = { ...plan, context_plan_id: executionPlan.context_plan_id };
  if (!("context_autopilot_decision_ref" in expectedPlan)) {
    if (typeof executionPlan.context_autopilot_decision_ref !== "string" || executionPlan.context_autopilot_decision_ref.length === 0) fail("Enterprise source execution decision reference is missing", true);
    expectedPlan.context_autopilot_decision_ref = executionPlan.context_autopilot_decision_ref;
  }
  if (Buffer.compare(canonicalBytes(expectedPlan), canonicalBytes(executionPlan)) !== 0) fail("Enterprise source execution changed the declared plan", true);
  if (planningEvaluationTime !== undefined) {
    const marker = sourceResultPlan.context_plan_evaluation_v1;
    const actual = marker && typeof marker === "object" && !Array.isArray(marker) ? (marker as JsonRecord).evaluation_time : undefined;
    if (actual !== planningEvaluationTime) fail("Enterprise source execution changed the evaluation time", true);
  }
  const dummyRecovery = { recovery_ref: "recovery:source-execution", source_ref: "source:materialization", source_digest: sha256Digest("source-execution") };
  let parsed: ReturnType<typeof parseResponse>;
  try {
    parsed = parseResponse(canonicalBytes({ schema_version: 1, transport_version: 1, engine_interface_version: ENGINE_INTERFACE_VERSION, view: executionRaw.view, invocation: executionRaw.invocation, observation: executionRaw.observation, recovery: dummyRecovery }));
  } catch (cause) { throw new EngineProtocolError("Enterprise source execution invocation or observation is invalid", { cause }); }
  if (parsed.records === null || parsed.view.outputRef === null || parsed.view.outputDigest === null) fail("Enterprise source execution view is missing output binding", true);
  const viewTextBytes = utf8Bytes(parsed.view.text, "execution.view.text", true);
  if (viewTextBytes.byteLength > MAX_TEXT_BYTES) fail("Enterprise source execution view exceeds its byte bound", true);
  const invocation = parsed.records.invocation;
  const observation = parsed.records.observation;
  const policy = invocation.policy_admission;
  if (!policy || typeof policy !== "object" || Array.isArray(policy) || (policy as JsonRecord).decision !== "admitted") fail("Enterprise source execution invocation is not admitted", true);
  const operation = invocation.operation;
  if (
    !operation ||
    typeof operation !== "object" ||
    Array.isArray(operation) ||
    (operation as JsonRecord).capability_id !== CAPABILITY ||
    (operation as JsonRecord).capability_version !== CAPABILITY_VERSION
  ) {
    fail("Enterprise source execution invocation is not bound to the local-native capability", true);
  }
  if (observation.status !== "succeeded") fail("Enterprise source execution observation did not succeed", true);
  const sourceLineage = observation.source_lineage;
  const invocationRefs = invocation.source_refs;
  if (!Array.isArray(sourceLineage) || !Array.isArray(invocationRefs) || sourceLineage.length !== invocationRefs.length || sourceLineage.some((entry, index) => entry !== invocationRefs[index])) fail("Enterprise source execution lineage does not bind invocation", true);
  if (observation.output_ref !== parsed.view.outputRef || observation.output_digest !== parsed.view.outputDigest) fail("Enterprise source execution view does not bind observation", true);
  const sourcePlanDigest = sha256Digest(canonicalBytes(sourcePlanRaw));
  const executionPlanDigest = sha256Digest(canonicalBytes(executionPlanRaw));
  const taskDigest = sha256Digest(canonicalBytes(task));
  validateLineage(invocation, sourcePlanDigest, executionPlanDigest, taskDigest);
  const receipt = parseReceipt(executionRaw.canonical_receipt);
  return {
    schema_version: SCHEMA_VERSION,
    transport_version: TRANSPORT_VERSION,
    engine_interface_version: ENGINE_INTERFACE_VERSION,
    source_plan: sourcePlan,
    execution_plan: executionPlan as EngineContextPlan,
    view: { text: parsed.view.text, output_ref: parsed.view.outputRef, output_digest: parsed.view.outputDigest },
    invocation,
    observation,
    canonical_receipt: receipt,
  };
}

export function parseSourceExecutionV2Response(
  raw: Uint8Array | string,
  request: EnginePlanningRequest,
  requestedIds: readonly string[],
  task: JsonRecord,
  plan: JsonRecord,
  tenantId: string,
  expectedGovernanceRevision: number,
  expectedBindingDigest: string,
  planningEvaluationTime?: string,
): EngineSourceExecutionV2 {
  const value = decode(raw);
  exactKeys(value, new Set(["schema_version", "tenant_id", "governance_revision", "execution"]), "Enterprise source execution v2 response", true);
  exactVersion(value.schema_version, "response.schema_version", V2_SCHEMA_VERSION, true);
  if (value.tenant_id !== tenantId) fail("Enterprise source execution v2 tenant binding does not match", true);
  const revision = integer(value.governance_revision, "response.governance_revision", 0, MAX_U64, true);
  if (revision !== expectedGovernanceRevision) fail("Enterprise source execution v2 governance revision does not match", true);
  const executionV2 = record(value.execution, "execution", true);
  exactKeys(executionV2, new Set(["schema_version", "execution", "receipt_document_json"]), "Enterprise source execution v2", true);
  exactVersion(executionV2.schema_version, "execution.schema_version", V2_SCHEMA_VERSION, true);
  const receiptDocumentJsonValue = executionV2.receipt_document_json;
  const receiptDocumentBytes = utf8Bytes(receiptDocumentJsonValue, "Enterprise source execution v2 receipt document", true);
  const receiptDocumentJson = receiptDocumentJsonValue as string;
  if (receiptDocumentBytes.byteLength > MAX_ENGINE_SOURCE_RECEIPT_DOCUMENT_BYTES) fail("Enterprise source execution v2 receipt document exceeds its byte bound", true);
  const nestedExecution = record(executionV2.execution, "execution.execution", true);
  const v1Wrapper = { schema_version: 1, tenant_id: tenantId, governance_revision: revision, execution: nestedExecution };
  let parsedExecution: EngineSourceExecution;
  try {
    parsedExecution = parseV1Value(v1Wrapper, request, requestedIds, task, plan, tenantId, revision, expectedBindingDigest, planningEvaluationTime);
  } catch (cause) {
    if (cause instanceof EngineProtocolError) throw cause;
    throw new EngineProtocolError("Enterprise source execution v2 nested execution is invalid", { cause });
  }
  if (parsedExecution.canonical_receipt.receipt_digest !== sha256Digest(receiptDocumentBytes)) fail("Enterprise source execution v2 receipt document digest does not match", true);
  try {
    const inner = { schema_version: V2_SCHEMA_VERSION, execution: nestedExecution, receipt_document_json: receiptDocumentJson };
    if (canonicalBytes(inner).byteLength > MAX_ENGINE_SOURCE_EXECUTION_V2_RESPONSE_BYTES) fail("Enterprise source execution v2 response exceeds its byte bound", true);
  } catch (cause) {
    if (cause instanceof EngineProtocolError) throw cause;
    throw new EngineProtocolError("Enterprise source execution v2 response contains non-canonical JSON data", { cause });
  }
  return {
    schema_version: V2_SCHEMA_VERSION,
    tenant_id: tenantId,
    governance_revision: revision,
    execution: { schema_version: V2_SCHEMA_VERSION, execution: parsedExecution, receipt_document_json: receiptDocumentJson },
  };
}
