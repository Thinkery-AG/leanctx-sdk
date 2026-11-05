// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** Strict DTOs and validators for explicit Engine source planning. */

import {
  MAX_SOURCE_REQUEST_BYTES,
  MAX_SOURCE_RESPONSE_BYTES,
  type SourceOperation,
  SubprocessInstanceClient,
} from "./engine.js";
import { InstanceProtocolError, ValidationError } from "./errors.js";
import {
  canonicalBytes,
  sha256Digest,
  strictJsonLoads,
  validateDigest,
} from "./protocol.js";

export const SCHEMA_VERSION = 1 as const;
export const TRANSPORT_VERSION = 1 as const;
export const ENGINE_INTERFACE_VERSION = "1.0.0" as const;
export const MAX_ENGINE_CONTEXT_PLAN_QUERY_BYTES = 16 * 1024;
export const MAX_ENGINE_CONTEXT_PLAN_TOKENS = 1_048_576;
export const MAX_ENGINE_CONTEXT_PLAN_CANDIDATES = 256;
export const MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES = MAX_SOURCE_REQUEST_BYTES;
export const MAX_ENGINE_SOURCE_PLAN_RESPONSE_BYTES = MAX_SOURCE_RESPONSE_BYTES;
export const MAX_ENGINE_SOURCE_PLAN_SOURCES = 64;
export const MAX_ENGINE_SOURCE_CONTENT_BYTES = 64 * 1024;
export const MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES = 1 * 1024 * 1024;
const MAX_PROTOCOL_ITEMS = 256;
const MAX_IDENTIFIER_BYTES = 256;
const MAX_REFERENCE_BYTES = 1024;
const MAX_U64 = Number.MAX_SAFE_INTEGER;
const MAX_EXTENSION_VALUE_BYTES = 64 * 1024;
const MAX_EXTENSION_DEPTH = 8;

const SOURCE_TYPES = new Set(["filesystem", "issue_tracker", "relational_database", "other"] as const);
const SOURCE_PERMISSIONS = new Set(["permitted", "denied", "unknown"] as const);
const CLASSIFICATIONS = new Set(["Public", "Internal", "Confidential", "Restricted"] as const);
const DISPOSITIONS = new Set(["selected", "excluded", "deferred"] as const);
const REASON_CODES = new Set([
  "relevant",
  "required",
  "cache_hit",
  "budget_exceeded",
  "lower_utility",
  "policy_excluded",
  "deferred_for_later",
  "other",
] as const);
const EVIDENCE_KINDS = new Set([
  "ProviderReceipt",
  "RuntimeLog",
  "SignedBatch",
  "QualityMeasurement",
  "ExperimentOutcome",
] as const);
const SIGNATURE_STATUSES = new Set(["Verified", "Unverified", "NotSigned"] as const);
const TIMESTAMP_RE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z$/;

type JsonRecord = Record<string, unknown>;
type JsonPath = readonly (string | number)[];
type SourceType = "filesystem" | "issue_tracker" | "relational_database" | "other";
type SourcePermission = "permitted" | "denied" | "unknown";
type Classification = "Public" | "Internal" | "Confidential" | "Restricted";

function error(message: string): never {
  throw new ValidationError(message);
}

function object(value: unknown, fieldName: string): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    error(fieldName + " must be an object");
  }
  return value as JsonRecord;
}

function exactKeys(value: JsonRecord, expected: ReadonlySet<string>, fieldName: string): void {
  const keys = Object.keys(value);
  if (keys.length !== expected.size || keys.some((key) => !expected.has(key))) {
    error(fieldName + " fields do not match the v1 contract");
  }
}

function validText(
  value: unknown,
  fieldName: string,
  maximum: number,
  options: { controls?: boolean; rejectNul?: boolean; nonblank?: boolean } = {},
): string {
  if (typeof value !== "string") error(fieldName + " must be a string");
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!Number.isFinite(next) || next < 0xdc00 || next > 0xdfff) error(fieldName + " is not valid UTF-8");
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      error(fieldName + " is not valid UTF-8");
    }
  }
  const bytes = Buffer.byteLength(value, "utf8");
  if (bytes === 0 && options.nonblank !== false) error(fieldName + " must not be empty");
  if (bytes > maximum) error(fieldName + " exceeds its UTF-8 byte bound");
  if (options.rejectNul !== false && value.includes(String.fromCharCode(0))) error(fieldName + " contains NUL");
  if (options.controls !== false && [...value].some((character) => {
    const codePoint = character.codePointAt(0) as number;
    return codePoint <= 0x1f || (codePoint >= 0x7f && codePoint <= 0x9f);
  })) {
    error(fieldName + " contains a control character");
  }
  if (options.nonblank !== false && value.trim().length === 0) error(fieldName + " must not be blank");
  return value;
}

function validMaybeText(value: unknown, fieldName: string, maximum: number): string | null {
  if (value === null || value === undefined) return null;
  return validText(value, fieldName, maximum);
}

function integer(value: unknown, fieldName: string, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) {
    error(fieldName + " is outside its protocol bounds");
  }
  return value;
}

function enumValue<T extends string>(value: unknown, fieldName: string, allowed: ReadonlySet<T>): T {
  if (typeof value !== "string" || !allowed.has(value as T)) error(fieldName + " has an unsupported value");
  return value as T;
}

function timestamp(value: unknown, fieldName: string): string {
  const text = validText(value, fieldName, MAX_IDENTIFIER_BYTES);
  const match = TIMESTAMP_RE.exec(text);
  if (match === null) error(fieldName + " must use canonical UTC timestamp syntax");
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year === 0 || month < 1 || month > 12 || day < 1 || day > (days[month - 1] as number) || hour > 23 || minute > 59 || second > 59) {
    error(fieldName + " contains an invalid date or time");
  }
  return text;
}

function optionalTimestamp(value: unknown, fieldName: string): string | null {
  return value === null || value === undefined ? null : timestamp(value, fieldName);
}

function optionalReference(value: unknown, fieldName: string): string | null {
  return value === null || value === undefined ? null : validText(value, fieldName, MAX_REFERENCE_BYTES);
}

function projectionDigest(value: unknown, fieldName: string): string {
  const digest = validateDigest(value, fieldName);
  return digest;
}

function extensionValue(value: unknown, depth = 0): void {
  if (depth > MAX_EXTENSION_DEPTH) error("extension value exceeds its nesting bound");
  if (value !== null && typeof value === "object") {
    if (Array.isArray(value)) {
      if (value.length > MAX_PROTOCOL_ITEMS) error("extension array exceeds its item bound");
      value.forEach((nested) => extensionValue(nested, depth + 1));
    } else {
      const entries = Object.entries(value as JsonRecord);
      if (entries.length > MAX_PROTOCOL_ITEMS) error("extension object exceeds its item bound");
      entries.forEach(([key, nested]) => {
        validText(key, "extension object key", MAX_IDENTIFIER_BYTES, { nonblank: true });
        extensionValue(nested, depth + 1);
      });
    }
  } else if (typeof value === "number" && !Number.isFinite(value)) {
    error("extension number is not finite");
  }
  try {
    if (canonicalBytes(value).byteLength > MAX_EXTENSION_VALUE_BYTES) {
      error("extension value exceeds its serialized byte bound");
    }
  } catch (cause) {
    if (cause instanceof ValidationError) throw cause;
    error("extension value is not canonical JSON");
  }
}

function extensions(value: JsonRecord, reserved: ReadonlySet<string>): JsonRecord {
  const result = Object.create(null) as JsonRecord;
  for (const [key, nested] of Object.entries(value)) {
    if (reserved.has(key)) continue;
    validText(key, "extension key", MAX_IDENTIFIER_BYTES);
    extensionValue(nested);
    defineDataProperty(result, key, nested);
  }
  if (Object.keys(result).length > MAX_PROTOCOL_ITEMS) error("extensions exceed their field bound");
  return result;
}

const DESCRIPTOR_KEYS = new Set([
  "object_ref",
  "source_id",
  "source_type",
  "content_digest",
  "revision",
  "owner",
  "observed_at",
  "valid_until",
  "classification",
  "permission",
]);

export type InstanceSourceDescriptorInput = Readonly<{
  object_ref: string;
  source_id: string;
  source_type: SourceType;
  content_digest: string;
  revision?: string | null;
  owner?: string | null;
  observed_at?: string | null;
  valid_until?: string | null;
  classification?: Classification | null;
  permission?: SourcePermission;
}>;

export type InstanceSourceDescriptor = Readonly<{
  object_ref: string;
  source_id: string;
  source_type: SourceType;
  content_digest: string;
  revision: string | null;
  owner: string | null;
  observed_at: string | null;
  valid_until: string | null;
  classification: Classification | null;
  permission: SourcePermission;
}>;

function descriptor(value: unknown, fieldName = "descriptor"): InstanceSourceDescriptor {
  const raw = object(value, fieldName);
  if (Object.keys(raw).some((key) => !DESCRIPTOR_KEYS.has(key))) {
    error(fieldName + " fields do not match the v1 contract");
  }
  for (const key of ["object_ref", "source_id", "source_type", "content_digest"]) {
    if (!(key in raw)) error(fieldName + " is missing a required field");
  }
  const observedAt = optionalTimestamp(raw.observed_at, fieldName + ".observed_at");
  const validUntil = optionalTimestamp(raw.valid_until, fieldName + ".valid_until");
  if (observedAt !== null && validUntil !== null && validUntil <= observedAt) {
    error(fieldName + " validity window is inverted");
  }
  return {
    object_ref: validText(raw.object_ref, fieldName + ".object_ref", MAX_REFERENCE_BYTES),
    source_id: validText(raw.source_id, fieldName + ".source_id", MAX_IDENTIFIER_BYTES),
    source_type: enumValue(raw.source_type, fieldName + ".source_type", SOURCE_TYPES),
    content_digest: validateDigest(raw.content_digest, fieldName + ".content_digest"),
    revision: optionalReference(raw.revision, fieldName + ".revision"),
    owner: optionalReference(raw.owner, fieldName + ".owner"),
    observed_at: observedAt,
    valid_until: validUntil,
    classification: raw.classification === null || raw.classification === undefined
      ? null
      : enumValue(raw.classification, fieldName + ".classification", CLASSIFICATIONS),
    permission: raw.permission === undefined
      ? "unknown"
      : enumValue(raw.permission, fieldName + ".permission", SOURCE_PERMISSIONS),
  };
}

export class InstancePlanningRequest {
  readonly taskId: string;
  readonly query: string;
  readonly budgetTokens: number;
  readonly maxCandidates: number;

  constructor(taskId: string, query: string, budgetTokens: number, maxCandidates = 64) {
    this.taskId = validText(taskId, "task_id", MAX_IDENTIFIER_BYTES);
    this.query = validText(query, "query", MAX_ENGINE_CONTEXT_PLAN_QUERY_BYTES, { controls: false });
    this.budgetTokens = integer(budgetTokens, "budget_tokens", 1, MAX_ENGINE_CONTEXT_PLAN_TOKENS);
    this.maxCandidates = integer(maxCandidates, "max_candidates", 1, MAX_ENGINE_CONTEXT_PLAN_CANDIDATES);
  }

  toDict(): Readonly<JsonRecord> {
    const result: JsonRecord = {
      schema_version: SCHEMA_VERSION,
      transport_version: TRANSPORT_VERSION,
      engine_interface_version: ENGINE_INTERFACE_VERSION,
      task_id: this.taskId,
      query: this.query,
      budget_tokens: this.budgetTokens,
      max_candidates: this.maxCandidates,
    };
    if (canonicalBytes(result).byteLength > MAX_ENGINE_CONTEXT_PLAN_REQUEST_BYTES) {
      error("LeanCTX instance context-plan request exceeds its byte bound");
    }
    return result;
  }
}

export const MAX_ENGINE_CONTEXT_PLAN_REQUEST_BYTES = 64 * 1024;

export class InstanceSource {
  readonly descriptor: InstanceSourceDescriptor;
  readonly content: string;

  constructor(input: InstanceSourceDescriptorInput, content: string) {
    this.descriptor = Object.freeze(descriptor(input));
    this.content = validText(content, "content", MAX_ENGINE_SOURCE_CONTENT_BYTES, {
      controls: false,
      rejectNul: false,
    });
    if (this.descriptor.content_digest !== sha256Digest(Buffer.from(this.content, "utf8"))) {
      error("descriptor.content_digest does not match content");
    }
  }

  toDict(): Readonly<JsonRecord> {
    return { descriptor: { ...this.descriptor }, content: this.content };
  }
}

export type InstanceSourceSelection = Readonly<{
  source_ref: string;
  provider: string;
  disposition: string;
  token_count: number;
  sha256_digest?: string;
  reason_codes: readonly string[];
  reason_detail?: string;
}>;

export type InstanceContextPlan = Readonly<{
  schema_version: 1;
  context_plan_id: string;
  task_id: string;
  projection_digest?: string;
  budget_tokens: number;
  selections: readonly InstanceSourceSelection[];
  readonly [key: string]: unknown;
}>;

export type InstanceSourcePlanResult = Readonly<{
  schema_version: 1;
  transport_version: 1;
  engine_interface_version: "1.0.0";
  plan: InstanceContextPlan;
  readonly [key: string]: unknown;
}>;

export type InstanceSourcePlan = Readonly<{
  result: InstanceSourcePlanResult;
  source_bindings: readonly InstanceSourceDescriptor[];
  binding_digest: string;
}>;

function descriptorEquals(left: InstanceSourceDescriptor, right: InstanceSourceDescriptor): boolean {
  return Buffer.compare(canonicalBytes(left), canonicalBytes(right)) === 0;
}

function isJsonRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasOwn(value: JsonRecord, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function defineDataProperty(target: JsonRecord, key: string, value: unknown): void {
  Object.defineProperty(target, key, {
    value,
    enumerable: true,
    writable: true,
    configurable: true,
  });
}

function assignDataProperties(target: JsonRecord, source: JsonRecord): void {
  for (const [key, value] of Object.entries(source)) {
    defineDataProperty(target, key, value);
  }
}

function recordAt(value: unknown, path: readonly string[]): JsonRecord | null {
  let current: unknown = value;
  for (const key of path) {
    if (!isJsonRecord(current) || !hasOwn(current, key)) return null;
    current = current[key];
  }
  return isJsonRecord(current) ? current : null;
}

function addIntegerPath(
  paths: JsonPath[],
  value: unknown,
  path: JsonPath,
  key: string,
): void {
  if (isJsonRecord(value) && hasOwn(value, key) && typeof value[key] === "number") {
    paths.push([...path, key]);
  }
}

function addSourcePlanIntegerPaths(
  paths: JsonPath[],
  value: JsonRecord,
  envelopePath: readonly string[],
): void {
  const envelope = recordAt(value, envelopePath);
  const result = envelope === null ? null : recordAt(envelope, ["result"]);
  const plan = result === null ? null : recordAt(result, ["plan"]);
  if (envelope === null || result === null || plan === null) return;
  const resultPath = [...envelopePath, "result"];
  const planPath = [...resultPath, "plan"];
  addIntegerPath(paths, result, resultPath, "schema_version");
  addIntegerPath(paths, result, resultPath, "transport_version");
  addIntegerPath(paths, plan, planPath, "schema_version");
  addIntegerPath(paths, plan, planPath, "budget_tokens");
  if (Array.isArray(plan.selections)) {
    plan.selections.forEach((selection, index) => {
      addIntegerPath(paths, selection, [...planPath, "selections", index], "token_count");
    });
  }
  if (isJsonRecord(plan.provider_stats)) {
    for (const [provider, stats] of Object.entries(plan.provider_stats)) {
      const statsPath = [...planPath, "provider_stats", provider];
      addIntegerPath(paths, stats, statsPath, "candidates_offered");
      addIntegerPath(paths, stats, statsPath, "candidates_selected");
      addIntegerPath(paths, stats, statsPath, "tokens_used");
    }
  }
  if (Array.isArray(plan.evidence)) {
    plan.evidence.forEach((evidence, index) => {
      addIntegerPath(paths, evidence, [...planPath, "evidence", index], "schema_version");
    });
  }
}

function knownIntegerPaths(value: JsonRecord): readonly JsonPath[] {
  const paths: JsonPath[] = [];
  addIntegerPath(paths, value, [], "schema_version");
  addIntegerPath(paths, value, [], "transport_version");
  addIntegerPath(paths, value, [], "materialized_token_count");
  addSourcePlanIntegerPaths(paths, value, []);
  addSourcePlanIntegerPaths(paths, value, ["plan"]);
  return paths;
}

function decode(raw: Uint8Array | string, label: string): JsonRecord {
  const bytes = typeof raw === "string" ? Buffer.from(raw, "utf8") : Buffer.from(raw);
  if (bytes.byteLength > MAX_ENGINE_SOURCE_PLAN_RESPONSE_BYTES) error(label + " exceeds the response byte bound");
  try {
    const initial = object(strictJsonLoads(bytes, label), label);
    return object(strictJsonLoads(bytes, label, knownIntegerPaths(initial)), label);
  } catch (cause) {
    if (cause instanceof ValidationError) throw cause;
    error("invalid " + label);
  }
}

function compareReferences(left: string, right: string): number {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function header(value: JsonRecord, label: string): void {
  integer(value.schema_version, label + ".schema_version", SCHEMA_VERSION, SCHEMA_VERSION);
  integer(value.transport_version, label + ".transport_version", TRANSPORT_VERSION, TRANSPORT_VERSION);
  if (value.engine_interface_version !== ENGINE_INTERFACE_VERSION) {
    error(label + ".engine_interface_version is unsupported");
  }
}

const SELECTION_KEYS = new Set([
  "source_ref",
  "provider",
  "disposition",
  "token_count",
  "sha256_digest",
  "reason_codes",
  "reason_detail",
]);
const PLAN_KEYS = new Set([
  "schema_version",
  "context_plan_id",
  "task_id",
  "projection_digest",
  "budget_tokens",
  "selections",
  "provider_stats",
  "policy_decision_refs",
  "evidence",
]);

function parseSelection(value: unknown, fieldName: string): InstanceSourceSelection {
  const raw = object(value, fieldName);
  if (Object.keys(raw).some((key) => !SELECTION_KEYS.has(key))) error(fieldName + " fields do not match the v1 contract");
  for (const key of ["source_ref", "provider", "disposition", "token_count", "reason_codes"]) {
    if (!(key in raw)) error(fieldName + " is missing a required field");
  }
  if (!Array.isArray(raw.reason_codes) || raw.reason_codes.length === 0 || raw.reason_codes.length > MAX_PROTOCOL_ITEMS) {
    error(fieldName + ".reason_codes has an invalid shape");
  }
  const reasons = raw.reason_codes.map((reason, index) => enumValue(reason, fieldName + ".reason_codes[" + index + "]", REASON_CODES));
  if (new Set(reasons).size !== reasons.length) error(fieldName + ".reason_codes contains duplicates");
  const result: JsonRecord = {
    source_ref: validText(raw.source_ref, fieldName + ".source_ref", MAX_REFERENCE_BYTES),
    provider: validText(raw.provider, fieldName + ".provider", MAX_REFERENCE_BYTES),
    disposition: enumValue(raw.disposition, fieldName + ".disposition", DISPOSITIONS),
    token_count: integer(raw.token_count, fieldName + ".token_count", 0, MAX_U64),
    reason_codes: reasons,
  };
  if (raw.sha256_digest !== undefined && raw.sha256_digest !== null) {
    result.sha256_digest = projectionDigest(raw.sha256_digest, fieldName + ".sha256_digest");
  }
  if (raw.reason_detail !== undefined && raw.reason_detail !== null) {
    result.reason_detail = validText(raw.reason_detail, fieldName + ".reason_detail", MAX_IDENTIFIER_BYTES);
  }
  return result as InstanceSourceSelection;
}

function parseEvidence(value: unknown, fieldName: string): JsonRecord {
  const raw = object(value, fieldName);
  const reserved = new Set(["schema_version", "kind", "uri", "digest", "signature_status", "media_type"]);
  const extra = extensions(raw, reserved);
  for (const key of ["kind", "uri", "digest", "signature_status"]) {
    if (!(key in raw)) error(fieldName + " is missing a required field");
  }
  const result: JsonRecord = {};
  if (raw.schema_version !== undefined && raw.schema_version !== null) {
    integer(raw.schema_version, fieldName + ".schema_version", 1, 1);
    result.schema_version = 1;
  }
  result.kind = enumValue(raw.kind, fieldName + ".kind", EVIDENCE_KINDS);
  result.uri = validText(raw.uri, fieldName + ".uri", MAX_IDENTIFIER_BYTES);
  result.digest = validText(raw.digest, fieldName + ".digest", MAX_IDENTIFIER_BYTES);
  result.signature_status = enumValue(raw.signature_status, fieldName + ".signature_status", SIGNATURE_STATUSES);
  if (raw.media_type !== undefined && raw.media_type !== null) {
    result.media_type = validText(raw.media_type, fieldName + ".media_type", MAX_IDENTIFIER_BYTES);
  }
  assignDataProperties(result, extra);
  return result;
}

function parsePlan(value: unknown): InstanceContextPlan {
  const raw = object(value, "plan");
  const required = ["schema_version", "context_plan_id", "task_id", "budget_tokens", "selections"];
  if (required.some((key) => !(key in raw))) error("plan is missing a required field");
  const extra = extensions(raw, PLAN_KEYS);
  integer(raw.schema_version, "plan.schema_version", SCHEMA_VERSION, SCHEMA_VERSION);
  if (!Array.isArray(raw.selections) || raw.selections.length > MAX_PROTOCOL_ITEMS) error("plan.selections has an invalid shape");
  const selections = raw.selections.map((item, index) => parseSelection(item, "plan.selections[" + index + "]"));
  const refs = selections.map((item) => item.source_ref);
  if (new Set(refs).size !== refs.length) error("plan.selections contains duplicate source_ref values");
  const budget = integer(raw.budget_tokens, "plan.budget_tokens", 0, MAX_U64);
  const selectedTokens = selections.reduce((sum, item) => sum + (item.disposition === "selected" ? item.token_count : 0), 0);
  if (selectedTokens > budget) error("plan selected context exceeds budget_tokens");
  const result: JsonRecord = {
    schema_version: SCHEMA_VERSION,
    context_plan_id: validText(raw.context_plan_id, "plan.context_plan_id", MAX_IDENTIFIER_BYTES),
    task_id: validText(raw.task_id, "plan.task_id", MAX_IDENTIFIER_BYTES),
    budget_tokens: budget,
    selections,
  };
  if (raw.projection_digest !== undefined && raw.projection_digest !== null) {
    result.projection_digest = projectionDigest(raw.projection_digest, "plan.projection_digest");
  }
  if (raw.provider_stats !== undefined) {
    const stats = object(raw.provider_stats, "plan.provider_stats");
    if (Object.keys(stats).length > MAX_PROTOCOL_ITEMS) error("plan.provider_stats exceeds its item bound");
    const normalized = Object.create(null) as JsonRecord;
    for (const [provider, value] of Object.entries(stats)) {
      const providerName = validText(provider, "plan.provider_stats key", MAX_IDENTIFIER_BYTES);
      const entry = object(value, "plan.provider_stats entry");
      exactKeys(entry, new Set(["candidates_offered", "candidates_selected", "tokens_used"]), "plan.provider_stats entry");
      const offered = integer(entry.candidates_offered, "plan.provider_stats.candidates_offered", 0, MAX_U64);
      const selected = integer(entry.candidates_selected, "plan.provider_stats.candidates_selected", 0, MAX_U64);
      if (selected > offered) error("plan.provider_stats selected exceeds offered");
      defineDataProperty(normalized, providerName, {
        candidates_offered: offered,
        candidates_selected: selected,
        tokens_used: integer(entry.tokens_used, "plan.provider_stats.tokens_used", 0, MAX_U64),
      });
    }
    if (Object.keys(normalized).length > 0) result.provider_stats = normalized;
  }
  if (raw.policy_decision_refs !== undefined) {
    if (!Array.isArray(raw.policy_decision_refs) || raw.policy_decision_refs.length > MAX_PROTOCOL_ITEMS) {
      error("plan.policy_decision_refs has an invalid shape");
    }
    const policyRefs = raw.policy_decision_refs.map((ref, index) => validText(ref, "plan.policy_decision_refs[" + index + "]", MAX_IDENTIFIER_BYTES));
    if (new Set(policyRefs).size !== policyRefs.length) error("plan.policy_decision_refs contains duplicates");
    if (policyRefs.length > 0) result.policy_decision_refs = policyRefs;
  }
  if (raw.evidence !== undefined) {
    if (!Array.isArray(raw.evidence) || raw.evidence.length > MAX_PROTOCOL_ITEMS) error("plan.evidence has an invalid shape");
    const evidence = raw.evidence.map((item, index) => parseEvidence(item, "plan.evidence[" + index + "]"));
    if (evidence.length > 0) result.evidence = evidence;
  }
  assignDataProperties(result, extra);
  if (result.projection_digest !== undefined) {
    const unsigned = { ...result };
    delete unsigned.projection_digest;
    if (sha256Digest(canonicalBytes(unsigned)) !== result.projection_digest) {
      error("plan.projection_digest does not match canonical projection content");
    }
  }
  return result as InstanceContextPlan;
}

function parseResponse(value: JsonRecord, request: InstancePlanningRequest): InstanceSourcePlanResult {
  exactKeys(value, new Set(["schema_version", "transport_version", "engine_interface_version", "plan"]), "LeanCTX instance context-plan response");
  header(value, "LeanCTX instance context-plan response");
  const plan = parsePlan(value.plan);
  if (plan.task_id !== request.taskId) error("LeanCTX instance context-plan response task_id does not bind the request");
  if (plan.budget_tokens > request.budgetTokens) error("LeanCTX instance context-plan response budget exceeds the request");
  return {
    schema_version: SCHEMA_VERSION,
    transport_version: TRANSPORT_VERSION,
    engine_interface_version: ENGINE_INTERFACE_VERSION,
    plan,
  };
}

export function parseSourcePlan(
  raw: Uint8Array | string,
  request: InstancePlanningRequest,
  sources?: readonly InstanceSource[],
): InstanceSourcePlan {
  const value = decode(raw, "LeanCTX instance source-plan response");
  exactKeys(value, new Set(["result", "source_bindings", "binding_digest"]), "LeanCTX instance source-plan response");
  const result = parseResponse(object(value.result, "result"), request);
  if (result.plan.projection_digest === undefined) error("LeanCTX instance source-plan response requires projection_digest");
  if (!Array.isArray(value.source_bindings) || value.source_bindings.length > MAX_PROTOCOL_ITEMS) {
    error("source_bindings has an invalid shape");
  }
  const bindings = value.source_bindings.map((item, index) => descriptor(item, "source_bindings[" + index + "]"));
  for (let index = 1; index < bindings.length; index += 1) {
    if (compareReferences(bindings[index - 1]!.object_ref, bindings[index]!.object_ref) >= 0) error("source_bindings must be strictly sorted by object_ref");
  }
  const selected = result.plan.selections.filter((item) => item.disposition === "selected");
  if (selected.length !== bindings.length) error("source_bindings do not match selected plan entries");
  for (const binding of bindings) {
    if (!selected.some((item) =>
      item.source_ref === binding.object_ref &&
      item.provider === binding.source_id &&
      item.sha256_digest === binding.content_digest
    )) {
      error("source binding does not match a selected plan entry");
    }
  }
  const bindingDigest = validateDigest(value.binding_digest, "binding_digest");
  if (sha256Digest(canonicalBytes([result, bindings])) !== bindingDigest) {
    error("binding_digest does not match canonical source bindings");
  }
  if (sources !== undefined) {
    if (sources.length > MAX_ENGINE_SOURCE_PLAN_SOURCES) error("sources has an invalid shape");
    const requested = new Map<string, InstanceSourceDescriptor>();
    for (const source of sources) {
      if (!(source instanceof InstanceSource)) error("sources must contain InstanceSource values");
      if (requested.has(source.descriptor.object_ref)) error("sources contains duplicate object_ref values");
      requested.set(source.descriptor.object_ref, source.descriptor);
    }
    for (const binding of bindings) {
      const expected = requested.get(binding.object_ref);
      if (expected === undefined || !descriptorEquals(expected, binding)) {
        error("source binding is not present in the requested sources");
      }
    }
  }
  return { result, source_bindings: bindings, binding_digest: bindingDigest };
}

function planEvaluationTime(plan: InstanceContextPlan): string | undefined {
  const extension = plan.context_plan_evaluation_v1;
  if (extension === undefined) return undefined;
  const value = object(extension, "plan.context_plan_evaluation_v1");
  return timestamp(value.evaluation_time, "plan.context_plan_evaluation_v1.evaluation_time");
}

export type InstanceContextSourceMaterialization = Readonly<{
  schema_version: 1;
  transport_version: 1;
  engine_interface_version: "1.0.0";
  plan: InstanceSourcePlan;
  materialized_digest: string;
  materialized_token_count: number;
  content: string;
}>;

export function parseMaterialization(
  raw: Uint8Array | string,
  request: InstancePlanningRequest,
  sources: readonly InstanceSource[],
): InstanceContextSourceMaterialization {
  const value = decode(raw, "LeanCTX instance materialization response");
  exactKeys(value, new Set([
    "schema_version",
    "transport_version",
    "engine_interface_version",
    "plan",
    "materialized_digest",
    "materialized_token_count",
    "content",
  ]), "LeanCTX instance materialization response");
  header(value, "LeanCTX instance materialization response");
  const plan = parseSourcePlan(canonicalBytes(value.plan), request, sources);
  const content = validText(value.content, "materialized content", MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES, {
    controls: false,
    rejectNul: false,
    nonblank: false,
  });
  const materializedDigest = validateDigest(value.materialized_digest, "materialized_digest");
  if (sha256Digest(Buffer.from(content, "utf8")) !== materializedDigest) {
    error("LeanCTX instance materialized content digest mismatch");
  }
  const tokenCount = integer(value.materialized_token_count, "materialized_token_count", 0, plan.result.plan.budget_tokens);
  return {
    schema_version: SCHEMA_VERSION,
    transport_version: TRANSPORT_VERSION,
    engine_interface_version: ENGINE_INTERFACE_VERSION,
    plan,
    materialized_digest: materializedDigest,
    materialized_token_count: tokenCount,
    content,
  };
}

export { ENGINE_PRESENTER_CAPABILITY_MIN_ENGINE_VERSION } from "./engine.js";
export const MAX_ENGINE_PRESENTER_TTL_SECONDS = 24 * 60 * 60;
/** Proxy request header a host shows its capability in. The proxy never forwards it. */
export const PRESENTER_CAPABILITY_HEADER = "x-leanctx-presenter" as const;
/** MCP `_meta` key a host shows its capability under. Never a tool argument. */
export const PRESENTER_CAPABILITY_META_KEY = "leanctx/presenterCapability" as const;

const PRESENTER_CAPABILITY_RE = /^lcp1_[0-9a-f]{64}$/;
const PRESENTER_REF_RE = /^presenter:[0-9a-f]{32}$/;
const PRESENTER_SECRETS = new WeakMap<object, string>();

/**
 * A presenter capability: a host-internal bearer secret issued by the Engine.
 *
 * Showing it proves only that a request comes from the holder of the capability under
 * which sources were presented. It is not an agent name, an identity or an
 * authorization; the Engine re-checks current source rights at every use.
 *
 * The secret is not a property of this object: it does not appear in `JSON.stringify`,
 * string conversion or `util.inspect`. `reveal()` is for the two host-side transports
 * only — the proxy header {@link PRESENTER_CAPABILITY_HEADER} and the MCP `_meta` key
 * {@link PRESENTER_CAPABILITY_META_KEY}. Never put it in model context, a tool
 * argument, a log or a request body.
 */
export class PresenterCapability {
  constructor(secret: string) {
    if (typeof secret !== "string" || !PRESENTER_CAPABILITY_RE.test(secret)) {
      error("presenter capability is malformed");
    }
    PRESENTER_SECRETS.set(this, secret);
    Object.freeze(this);
  }

  reveal(): string {
    return PRESENTER_SECRETS.get(this) as string;
  }

  toString(): string {
    return "PresenterCapability(<redacted>)";
  }

  toJSON(): string {
    return "<redacted>";
  }

  [Symbol.for("nodejs.util.inspect.custom")](): string {
    return this.toString();
  }
}

export type InstancePresenterGrant = Readonly<{
  /** Content-free reference of the presenter entry; safe in records. */
  presenter_ref: string;
  expires_at_unix: number;
  /** Present when the capability was issued by this call; `null` on a renewal. */
  capability: PresenterCapability | null;
}>;

export type InstanceContextSourceMaterializationV2 = Readonly<{
  schema_version: 2;
  materialization: InstanceContextSourceMaterialization;
  presenter: InstancePresenterGrant;
}>;

export function parseMaterializationV2(
  raw: Uint8Array | string,
  request: InstancePlanningRequest,
  sources: readonly InstanceSource[],
  issued: boolean,
): InstanceContextSourceMaterializationV2 {
  const label = "LeanCTX instance materialization v2 response";
  const value = decode(raw, label);
  const keys = Object.keys(value);
  if (keys.length !== 3 || !["schema_version", "materialization", "presenter"].every((key) => keys.includes(key))) {
    error(label + " fields do not match the v2 contract");
  }
  if (value.schema_version !== 2) error(label + " schema_version is unsupported");
  const materialization = parseMaterialization(canonicalBytes(value.materialization), request, sources);
  const grant = object(value.presenter, "presenter");
  const allowed = new Set(["presenter_ref", "expires_at_unix", "capability"]);
  if (Object.keys(grant).some((key) => !allowed.has(key))) error("presenter fields do not match the v2 contract");
  const presenterRef = grant.presenter_ref;
  if (typeof presenterRef !== "string" || !PRESENTER_REF_RE.test(presenterRef)) error("presenter_ref is malformed");
  const expiresAt = integer(grant.expires_at_unix, "presenter.expires_at_unix", 1, Number.MAX_SAFE_INTEGER);
  if (issued !== (grant.capability !== undefined)) error("presenter grant does not match the requested mode");
  const capability = grant.capability === undefined ? null : new PresenterCapability(grant.capability as string);
  if (capability !== null && materialization.content.includes(capability.reveal())) {
    error("materialized content must not contain the presenter capability");
  }
  return {
    schema_version: 2,
    materialization,
    presenter: { presenter_ref: presenterRef, expires_at_unix: expiresAt, capability },
  };
}

export class InstanceSourcePlanningClient {
  constructor(private readonly engine: SubprocessInstanceClient) {}

  async contextPlanSources(
    projectRoot: string,
    request: InstancePlanningRequest,
    sources: readonly InstanceSource[],
  ): Promise<InstanceSourcePlan> {
    const input = sourcePlanRequest(request, sources);
    let raw: Buffer;
    try {
      raw = await this.engine.sourceOperation("context-plan-sources", projectRoot, input);
    } catch (cause) {
      throw cause;
    }
    try {
      return parseSourcePlan(raw, request, sources);
    } catch (cause) {
      if (cause instanceof ValidationError) throw new InstanceProtocolError("LeanCTX instance source-plan response failed validation", { cause });
      throw cause;
    }
  }

  async contextMaterializeSources(
    projectRoot: string,
    request: InstancePlanningRequest,
    sources: readonly InstanceSource[],
    expectedBindingDigest: string,
    planningEvaluationTime?: string,
  ): Promise<InstanceContextSourceMaterialization> {
    const sourcePlan = sourcePlanRequest(request, sources);
    const expectedDigest = validateDigest(expectedBindingDigest, "expected_binding_digest");
    const expectedEvaluationTime = planningEvaluationTime === undefined
      ? undefined
      : timestamp(planningEvaluationTime, "planning_evaluation_time");
    const body: JsonRecord = {
      source_plan: sourcePlan,
      expected_binding_digest: expectedDigest,
    };
    if (expectedEvaluationTime !== undefined) body.planning_evaluation_time = expectedEvaluationTime;
    const raw = await this.engine.sourceOperation("context-materialize-sources", projectRoot, body);
    try {
      const materialized = parseMaterialization(raw, request, sources);
      if (materialized.plan.binding_digest !== expectedDigest) {
        throw new InstanceProtocolError("LeanCTX instance materialization response binding digest mismatch");
      }
      if (planEvaluationTime(materialized.plan.result.plan) !== expectedEvaluationTime) {
        throw new InstanceProtocolError("LeanCTX instance materialization response evaluation epoch mismatch");
      }
      return materialized;
    } catch (cause) {
      if (cause instanceof ValidationError) throw new InstanceProtocolError("LeanCTX instance materialization response failed validation", { cause });
      throw cause;
    }
  }

  /**
   * `context-materialize-sources-v2`: the v1 exchange, presented under a presenter
   * capability. `{ ttlSeconds }` has one issued; a capability from an earlier grant
   * presents under it again (and is not returned a second time).
   */
  async contextMaterializeSourcesV2(
    projectRoot: string,
    request: InstancePlanningRequest,
    sources: readonly InstanceSource[],
    expectedBindingDigest: string,
    presenter: { ttlSeconds: number } | PresenterCapability,
    planningEvaluationTime?: string,
  ): Promise<InstanceContextSourceMaterializationV2> {
    const sourcePlan = sourcePlanRequest(request, sources);
    const expectedDigest = validateDigest(expectedBindingDigest, "expected_binding_digest");
    const expectedEvaluationTime = planningEvaluationTime === undefined
      ? undefined
      : timestamp(planningEvaluationTime, "planning_evaluation_time");
    const materialization: JsonRecord = {
      source_plan: sourcePlan,
      expected_binding_digest: expectedDigest,
    };
    if (expectedEvaluationTime !== undefined) materialization.planning_evaluation_time = expectedEvaluationTime;
    const held = presenter instanceof PresenterCapability ? presenter : null;
    const mode: JsonRecord = held !== null
      ? { mode: "renew" }
      : {
        mode: "issue",
        ttl_seconds: integer(
          (presenter as { ttlSeconds: number }).ttlSeconds,
          "presenter.ttlSeconds",
          1,
          MAX_ENGINE_PRESENTER_TTL_SECONDS,
        ),
      };
    const raw = await this.engine.presenterMaterializeOperation(
      projectRoot,
      { schema_version: 2, materialization, presenter: mode },
      held?.reveal(),
    );
    try {
      const response = parseMaterializationV2(raw, request, sources, held === null);
      if (response.materialization.plan.binding_digest !== expectedDigest) {
        throw new InstanceProtocolError("LeanCTX instance materialization response binding digest mismatch");
      }
      if (planEvaluationTime(response.materialization.plan.result.plan) !== expectedEvaluationTime) {
        throw new InstanceProtocolError("LeanCTX instance materialization response evaluation epoch mismatch");
      }
      return response;
    } catch (cause) {
      if (cause instanceof ValidationError) throw new InstanceProtocolError("LeanCTX instance materialization response failed validation", { cause });
      throw cause;
    }
  }

  /**
   * `context-presented-use`: what the holder of `capability` may do later with text
   * that was materialized under it — ask whether it is still usable (`check`), keep a
   * recovery copy (`retain`), get a kept copy back (`expand`). The capability reaches
   * the Engine through an owner-only file, never through argv, env or the request.
   *
   * `authorizedSourceIds` is the Engine's optional source constraint: when given, the
   * Engine answers only for a lineage of EXACTLY these source ids and stores nothing
   * for any other (`lineage.unknown`). A local host leaves it out; a caller that has
   * authorized the request for certain sources names them.
   */
  async presentedUse(
    projectRoot: string,
    capability: PresenterCapability,
    asked: PresentedUse,
    authorizedSourceIds?: readonly string[],
  ): Promise<PresentedUseAnswer> {
    if (!(capability instanceof PresenterCapability)) error("presented use requires a PresenterCapability");
    const use = presentedUseRequest(asked);
    const request: JsonRecord = authorizedSourceIds === undefined
      ? { schema_version: 1, use }
      : { schema_version: 1, authorized_source_ids: sourceConstraint(authorizedSourceIds), use };
    const raw = await this.engine.presenterMaterializeOperation(
      projectRoot,
      request,
      capability.reveal(),
      "context-presented-use",
      2 * MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES + 1024 + MAX_ENGINE_SOURCE_PLAN_SOURCES * 515,
    );
    try {
      const answer = parsePresentedUse(decode(raw, "LeanCTX instance presented use response"), asked, capability);
      // The constraint is set equality: a usable answer for other sources than the
      // ones this request was authorized for is not an answer to it.
      if (
        answer.usable && authorizedSourceIds !== undefined
        && (answer.source_ids.length !== authorizedSourceIds.length
          || !authorizedSourceIds.every((id) => answer.source_ids.includes(id)))
      ) {
        throw new InstanceProtocolError("LeanCTX instance presented use response is outside the authorized sources");
      }
      return answer;
    } catch (cause) {
      if (cause instanceof ValidationError) throw new InstanceProtocolError("LeanCTX instance presented use response failed validation", { cause });
      throw cause;
    }
  }

  /** `context-presenter-revoke`: from now on `capability` presents and reuses nothing. */
  async revokePresenter(projectRoot: string, capability: PresenterCapability): Promise<PresenterRevocation> {
    if (!(capability instanceof PresenterCapability)) error("revocation requires a PresenterCapability");
    const raw = await this.engine.presenterMaterializeOperation(
      projectRoot,
      { schema_version: 1 },
      capability.reveal(),
      "context-presenter-revoke",
      256,
    );
    try {
      return parsePresenterRevocation(decode(raw, "LeanCTX instance presenter revocation response"));
    } catch (cause) {
      if (cause instanceof ValidationError) throw new InstanceProtocolError("LeanCTX instance presenter revocation response failed validation", { cause });
      throw cause;
    }
  }
}

/** What the holder of a capability asks about text delivered under a presented lineage. */
export type PresentedUse =
  | Readonly<{ mode: "check"; content: string }>
  | Readonly<{ mode: "retain"; content: string }>
  | Readonly<{ mode: "expand"; recovery_id: string }>;

/** `usable: false` carries a content-free `reason` and nothing else. */
export type PresentedUseAnswer = Readonly<{
  schema_version: 1;
  presenter_ref: string;
  usable: boolean;
  reason: string | null;
  source_ids: readonly string[];
  recovery_id: string | null;
  content: string | null;
}>;

export type PresenterRevocation = Readonly<{
  schema_version: 1;
  presenter_ref: string;
  revoked: boolean;
}>;

const RECOVERY_ID_RE = /^[0-9a-f]{64}$/;
const PRESENTED_USE_REASON_RE = /^(?:lineage|recovery)\.[a-z_]{1,48}$/;

/** The wire member `authorized_source_ids`: a non-empty, bounded set of distinct ids. */
function sourceConstraint(ids: readonly string[]): string[] {
  if (
    !Array.isArray(ids) || ids.length === 0 || ids.length > MAX_ENGINE_SOURCE_PLAN_SOURCES
    || ids.some((id) => typeof id !== "string" || id.length === 0 || Buffer.byteLength(id, "utf8") > 256)
    || new Set(ids).size !== ids.length
  ) {
    error("authorized source ids must be a non-empty, bounded set of distinct ids");
  }
  return [...ids].sort();
}

/** The wire member `use`. A capability is never part of it. */
export function presentedUseRequest(asked: PresentedUse): JsonRecord {
  const mode = (asked as { mode?: unknown } | null)?.mode;
  if (mode === "expand") {
    const recoveryId = (asked as { recovery_id?: unknown }).recovery_id;
    if (typeof recoveryId !== "string" || !RECOVERY_ID_RE.test(recoveryId)) error("recovery_id is malformed");
    return { mode, recovery_id: recoveryId };
  }
  if (mode !== "check" && mode !== "retain") error("presented use mode must be check, retain or expand");
  const content = (asked as { content?: unknown }).content;
  if (
    typeof content !== "string" || content.length === 0
    || Buffer.byteLength(content, "utf8") > MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES
  ) {
    error("presented content is empty or exceeds its byte bound");
  }
  return { mode, content };
}

/**
 * Validate the Engine's answer to `asked`: text comes back for `expand` only, a
 * recovery id for `retain` and `expand`, and never the capability inside the text.
 */
export function parsePresentedUse(
  value: Record<string, unknown>,
  asked: PresentedUse,
  capability: PresenterCapability,
): PresentedUseAnswer {
  const allowed = new Set(["schema_version", "presenter_ref", "usable", "reason", "source_ids", "recovery_id", "content"]);
  const sourceIds = value.source_ids;
  if (
    Object.keys(value).some((key) => !allowed.has(key)) || value.schema_version !== 1
    || typeof value.presenter_ref !== "string" || !PRESENTER_REF_RE.test(value.presenter_ref)
    || typeof value.usable !== "boolean"
    || !Array.isArray(sourceIds) || !sourceIds.every((id) => typeof id === "string" && id.length > 0)
    // A lineage names every source once: a repeated id could stand in for one it does not name.
    || new Set(sourceIds).size !== sourceIds.length
  ) {
    error("presented use fields do not match the v1 contract");
  }
  const base = { schema_version: 1 as const, presenter_ref: value.presenter_ref as string };
  if (value.usable === false) {
    if (
      typeof value.reason !== "string" || !PRESENTED_USE_REASON_RE.test(value.reason)
      || (sourceIds as unknown[]).length !== 0 || value.recovery_id !== undefined || value.content !== undefined
    ) {
      error("presented use refusal does not match the v1 contract");
    }
    return { ...base, usable: false, reason: value.reason as string, source_ids: [], recovery_id: null, content: null };
  }
  const expanding = asked.mode === "expand";
  const recoveryId = value.recovery_id;
  const content = value.content;
  if (
    value.reason !== undefined || (sourceIds as unknown[]).length === 0
    || (recoveryId !== undefined) !== (asked.mode !== "check")
    || (recoveryId !== undefined && (typeof recoveryId !== "string" || !RECOVERY_ID_RE.test(recoveryId)))
    || (asked.mode === "expand" && recoveryId !== asked.recovery_id)
    || (content !== undefined) !== expanding
    || (content !== undefined && (typeof content !== "string" || content.includes(capability.reveal())))
  ) {
    error("presented use answer does not match what was asked");
  }
  return {
    ...base,
    usable: true,
    reason: null,
    source_ids: Object.freeze([...(sourceIds as string[])]),
    recovery_id: (recoveryId as string | undefined) ?? null,
    content: (content as string | undefined) ?? null,
  };
}

export function parsePresenterRevocation(value: Record<string, unknown>): PresenterRevocation {
  const keys = Object.keys(value);
  if (
    keys.length !== 3 || value.schema_version !== 1
    || typeof value.presenter_ref !== "string" || !PRESENTER_REF_RE.test(value.presenter_ref)
    || typeof value.revoked !== "boolean"
  ) {
    error("presenter revocation fields do not match the v1 contract");
  }
  return { schema_version: 1, presenter_ref: value.presenter_ref as string, revoked: value.revoked as boolean };
}

function sourcePlanRequest(
  request: InstancePlanningRequest,
  sources: readonly InstanceSource[],
): JsonRecord {
  if (!(request instanceof InstancePlanningRequest)) error("source planning requires InstancePlanningRequest");
  if (!Array.isArray(sources) || sources.length > MAX_ENGINE_SOURCE_PLAN_SOURCES) {
    error("sources must be a bounded list");
  }
  const descriptors = sources.map((source) => {
    if (!(source instanceof InstanceSource)) error("sources must contain InstanceSource values");
    return source.toDict();
  });
  const references = sources.map((source) => source.descriptor.object_ref);
  if (new Set(references).size !== references.length) error("source object references must be unique");
  const body: JsonRecord = { planning: request.toDict(), sources: descriptors };
  if (canonicalBytes(body).byteLength > MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES) {
    error("LeanCTX instance source-plan request exceeds its byte bound");
  }
  return body;
}

export type { SourceOperation };
