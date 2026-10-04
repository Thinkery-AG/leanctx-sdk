// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/**
 * Preview: read-only access to the local Engine's Context Store.
 *
 * `readPolicyEvidence` returns the scope's content-free read-strategy evidence
 * (`lean-ctx engine context-policy-evidence`); `readTaskLineage` one task's
 * decision lineage (`lean-ctx engine context-lineage`) with every missing
 * link named as a gap. Both reads are confined to one tenant/project scope.
 * Parsing mirrors the Engine's validation: unknown fields or values and
 * inconsistent counts are rejected, and "unmeasured" never reads as a passing
 * measurement. Contract `leanctx-context-store-preview` 0.1 — may change in
 * minor releases.
 */

import { EngineProtocolError, ValidationError } from "./errors.js";
import type { SubprocessEngineClient } from "./engine.js";
import { canonicalBytes, strictJsonLoads } from "./protocol.js";

export const CONTEXT_STORE_PREVIEW_CONTRACT = "leanctx-context-store-preview" as const;
export const CONTEXT_STORE_PREVIEW_VERSION = "0.1.0" as const;
export const MAX_STORE_REQUEST_BYTES = 16 * 1024;
export const MAX_STORE_RESPONSE_BYTES = 8 * 1024 * 1024;

const ENGINE_INTERFACE_VERSION = "1.0.0";
const MAX_EVIDENCE_RECORDS = 4096;
const MAX_LINEAGE_ITEMS = 4096;
const MAX_REF_BYTES = 512;
const MAX_TEXT_BYTES = 4096;

export const TASK_CLASSES = ["bug_fix", "refactor", "test_addition", "documentation", "investigation"] as const;
export const LANGUAGES = [
  "rust", "python", "type_script", "java_script", "go", "java", "c", "cpp", "c_sharp",
  "swift", "kotlin", "ruby", "php", "shell", "other", "none",
] as const;
export const SIZES = ["tiny", "small", "medium", "large", "very_large"] as const;
export const STRATEGIES = [
  "full", "map", "signatures", "aggressive", "entropy", "task", "reference", "diff", "lines", "auto", "other",
] as const;
const EVIDENCE_TIERS = [
  "mechanism", "deterministic_quality", "recorded_regression", "live_task_evaluation", "production_outcome",
] as const;
const VERDICTS = ["improved", "non_inferior", "regressed", "underpowered"] as const;
const STEP_KINDS = [
  "task_started", "plan_created", "context_delivered", "model_invoked", "engine_invoked",
  "receipt_signed", "canonical_receipt_recorded", "outcome_recorded", "decision_recorded",
] as const;
const OUTCOMES = ["accepted", "rejected", "unknown"] as const;
const DELIVERY_OUTCOMES = ["delivered", "withheld", "failed"] as const;
const DELIVERY_ERRORS = ["missing", "unreadable", "tampered"] as const;
const GAPS = [
  "ledger_unverified", "no_plan_recorded", "no_delivery_recorded", "delivery_unverified",
  "deliveries_incomplete", "no_outcome_recorded",
] as const;

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const FIELD = /^[a-z][a-z0-9_]{0,63}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;

export type Workload = Readonly<{
  taskClass: (typeof TASK_CLASSES)[number];
  language: (typeof LANGUAGES)[number];
  size: (typeof SIZES)[number];
}>;
/** Absent counts mean "not measured", never zero. */
export type QualityEvidence = Readonly<{
  measured: boolean;
  retained?: number;
  recoverable?: number;
  lost?: number;
  handlesEmitted?: number;
  handlesVerified?: number;
  failures?: number;
  criticalFailures?: number;
}>;
export type SecurityEvidence = Readonly<{ measured: boolean; regressions?: number }>;
export type StrategyOutcomeRecord = Readonly<{
  workload: Workload;
  strategy: (typeof STRATEGIES)[number];
  observedDay: number;
  samples: number;
  accepted: number;
  rejected: number;
  explicitOverrides: number;
  tokenSamples: number;
  signalSamples: number;
  bounceTasks: number;
  expandTasks: number;
  editFailureTasks: number;
  tokensOriginal: number;
  tokensDelivered: number;
  quality: QualityEvidence;
  security: SecurityEvidence;
}>;
export type StrategyEvaluation = Readonly<{
  strategy: (typeof STRATEGIES)[number];
  evidenceTier: (typeof EVIDENCE_TIERS)[number];
  verdict: (typeof VERDICTS)[number];
  pairs: number;
  powered: boolean;
  deltaMilli: number;
  ciLowMilli: number;
  ciHighMilli: number;
  marginMilli: number;
}>;
export type ContextPolicyEvidence = Readonly<{
  records: readonly StrategyOutcomeRecord[];
  evaluations: readonly StrategyEvaluation[];
}>;
export type LineageStep = Readonly<{
  sequence: number;
  kind: (typeof STEP_KINDS)[number];
  timestamp: string;
  fields: Readonly<Record<string, string>>;
}>;
export type DeliverySummary = Readonly<{
  outcome: (typeof DELIVERY_OUTCOMES)[number];
  destination: string;
  policyDigest: string | null;
  inspected: number;
  delivered: number;
  withheld: number;
  redactions: number;
  tokensOriginal: number;
  tokensDelivered: number;
  finalContext: string | null;
}>;
export type LineageDelivery = Readonly<{
  digest: string;
  verified: boolean;
  error: (typeof DELIVERY_ERRORS)[number] | null;
  summary: DeliverySummary | null;
}>;
export type TaskLineage = Readonly<{
  taskId: string;
  scope: string | null;
  steps: readonly LineageStep[];
  deliveries: readonly LineageDelivery[];
  ledgerError: string | null;
  outcome: (typeof OUTCOMES)[number];
  gaps: readonly (typeof GAPS)[number][];
  /** True when no link of plan → delivery → outcome is missing. */
  isComplete: boolean;
}>;

type Json = Record<string, unknown>;

function bad(message: string): EngineProtocolError {
  return new EngineProtocolError(`context store: ${message}`);
}

function object(value: unknown, label: string): Json {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw bad(`${label} must be an object`);
  return value as Json;
}

function keys(value: Json, required: readonly string[], optional: readonly string[], label: string): void {
  for (const key of required) if (!(key in value)) throw bad(`${label} lacks ${key}`);
  for (const key of Object.keys(value)) {
    if (!required.includes(key) && !optional.includes(key)) throw bad(`${label} has unknown field ${key}`);
  }
}

function int(value: unknown, label: string, signed = false): number {
  const minimum = signed ? -Number.MAX_SAFE_INTEGER : 0;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw bad(`${label} must be ${signed ? "a safe" : "a non-negative"} integer`);
  }
  return value;
}

function bool(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw bad(`${label} must be a boolean`);
  return value;
}

function text(value: unknown, label: string, maximum = MAX_REF_BYTES): string {
  if (typeof value !== "string" || Buffer.byteLength(value, "utf8") > maximum || CONTROL.test(value)) {
    throw bad(`${label} must be bounded text`);
  }
  return value;
}

function oneOf<T extends readonly string[]>(value: unknown, allowed: T, label: string): T[number] {
  if (typeof value !== "string" || !allowed.includes(value)) throw bad(`${label} has unknown value ${String(value)}`);
  return value as T[number];
}

function digest(value: unknown, label: string): string {
  if (typeof value !== "string" || !DIGEST.test(value)) throw bad(`${label} must be a sha256 digest`);
  return value;
}

function list(value: unknown, label: string, maximum: number): unknown[] {
  if (!Array.isArray(value) || value.length > maximum) throw bad(`${label} must be a list of at most ${maximum}`);
  return value;
}

function workload(raw: unknown, label: string): Workload {
  const value = object(raw, label);
  keys(value, ["task_class", "language", "size"], [], label);
  return Object.freeze({
    taskClass: oneOf(value.task_class, TASK_CLASSES, `${label}.task_class`),
    language: oneOf(value.language, LANGUAGES, `${label}.language`),
    size: oneOf(value.size, SIZES, `${label}.size`),
  });
}

function quality(raw: unknown, label: string): QualityEvidence {
  const value = object(raw, label);
  if (value.state === "unmeasured") {
    keys(value, ["state"], [], label);
    return Object.freeze({ measured: false });
  }
  if (value.state !== "measured") throw bad(`${label}.state has unknown value ${String(value.state)}`);
  keys(value, ["state", "retention", "recovery"], [], label);
  const retention = object(value.retention, `${label}.retention`);
  keys(retention, ["retained", "recoverable", "lost"], [], `${label}.retention`);
  const recovery = object(value.recovery, `${label}.recovery`);
  keys(recovery, ["handles_emitted", "handles_verified", "failures", "critical_failures"], [], `${label}.recovery`);
  const result = {
    measured: true,
    retained: int(retention.retained, `${label}.retained`),
    recoverable: int(retention.recoverable, `${label}.recoverable`),
    lost: int(retention.lost, `${label}.lost`),
    handlesEmitted: int(recovery.handles_emitted, `${label}.handles_emitted`),
    handlesVerified: int(recovery.handles_verified, `${label}.handles_verified`),
    failures: int(recovery.failures, `${label}.failures`),
    criticalFailures: int(recovery.critical_failures, `${label}.critical_failures`),
  };
  if (
    result.handlesVerified > result.handlesEmitted
    || result.failures > result.handlesEmitted
    || result.criticalFailures > result.failures
  ) {
    throw bad(`${label} has impossible recovery counts`);
  }
  return Object.freeze(result);
}

function security(raw: unknown, label: string): SecurityEvidence {
  const value = object(raw, label);
  if (value.state === "unmeasured") {
    keys(value, ["state"], [], label);
    return Object.freeze({ measured: false });
  }
  if (value.state !== "measured") throw bad(`${label}.state has unknown value ${String(value.state)}`);
  keys(value, ["state", "regressions"], [], label);
  return Object.freeze({ measured: true, regressions: int(value.regressions, label) });
}

const COUNTS = [
  "observed_day", "samples", "accepted", "rejected", "explicit_overrides", "token_samples",
  "tokens_original", "tokens_delivered",
] as const;
const SIGNAL_COUNTS = ["signal_samples", "bounce_tasks", "expand_tasks", "edit_failure_tasks"] as const;

function outcomeRecord(raw: unknown, label: string): StrategyOutcomeRecord {
  const value = object(raw, label);
  keys(value, ["workload", "strategy", "quality", "security", ...COUNTS], SIGNAL_COUNTS, label);
  const count = (key: string) => int(value[key], `${label}.${key}`);
  // Signal counts default to zero, like the Engine's own deserializer.
  const signal = (key: string) => int(value[key] ?? 0, `${label}.${key}`);
  const record: StrategyOutcomeRecord = Object.freeze({
    workload: workload(value.workload, `${label}.workload`),
    strategy: oneOf(value.strategy, STRATEGIES, `${label}.strategy`),
    observedDay: count("observed_day"),
    samples: count("samples"),
    accepted: count("accepted"),
    rejected: count("rejected"),
    explicitOverrides: count("explicit_overrides"),
    tokenSamples: count("token_samples"),
    signalSamples: signal("signal_samples"),
    bounceTasks: signal("bounce_tasks"),
    expandTasks: signal("expand_tasks"),
    editFailureTasks: signal("edit_failure_tasks"),
    tokensOriginal: count("tokens_original"),
    tokensDelivered: count("tokens_delivered"),
    quality: quality(value.quality, `${label}.quality`),
    security: security(value.security, `${label}.security`),
  });
  if (record.samples === 0) throw bad(`${label} has no samples`);
  if (record.accepted + record.rejected !== record.samples) throw bad(`${label}: accepted + rejected must equal samples`);
  if (record.explicitOverrides > record.samples || record.tokenSamples > record.samples) {
    throw bad(`${label} counts more tasks than samples`);
  }
  if (
    record.signalSamples > record.samples
    || [record.bounceTasks, record.expandTasks, record.editFailureTasks].some((n) => n > record.signalSamples)
  ) {
    throw bad(`${label} signal counts exceed their attributed tasks`);
  }
  if (record.tokenSamples === 0 && (record.tokensOriginal > 0 || record.tokensDelivered > 0)) {
    throw bad(`${label} has tokens without token samples`);
  }
  if (record.tokensDelivered > record.tokensOriginal) throw bad(`${label} delivered more tokens than original`);
  return record;
}

function evaluation(raw: unknown, label: string): StrategyEvaluation {
  const value = object(raw, label);
  keys(value, ["strategy", "evidence_tier", "verdict", "pairs", "powered", "delta_milli", "ci_low_milli", "ci_high_milli", "margin_milli"], [], label);
  const result: StrategyEvaluation = Object.freeze({
    strategy: oneOf(value.strategy, STRATEGIES, `${label}.strategy`),
    evidenceTier: oneOf(value.evidence_tier, EVIDENCE_TIERS, `${label}.evidence_tier`),
    verdict: oneOf(value.verdict, VERDICTS, `${label}.verdict`),
    pairs: int(value.pairs, `${label}.pairs`),
    powered: bool(value.powered, `${label}.powered`),
    deltaMilli: int(value.delta_milli, `${label}.delta_milli`, true),
    ciLowMilli: int(value.ci_low_milli, `${label}.ci_low_milli`, true),
    ciHighMilli: int(value.ci_high_milli, `${label}.ci_high_milli`, true),
    marginMilli: int(value.margin_milli, `${label}.margin_milli`, true),
  });
  if (result.strategy === "other") throw bad(`${label} must name a known strategy`);
  if (result.ciLowMilli > result.ciHighMilli || result.marginMilli < 0) throw bad(`${label} has an impossible interval`);
  if (result.powered && result.pairs === 0) throw bad(`${label} is powered without pairs`);
  return result;
}

function recordKey(record: StrategyOutcomeRecord): number[] {
  return [
    TASK_CLASSES.indexOf(record.workload.taskClass), LANGUAGES.indexOf(record.workload.language),
    SIZES.indexOf(record.workload.size), STRATEGIES.indexOf(record.strategy), record.observedDay,
  ];
}

function before(left: number[], right: number[]): boolean {
  for (let i = 0; i < left.length; i += 1) {
    const a = left[i] ?? 0;
    const b = right[i] ?? 0;
    if (a !== b) return a < b;
  }
  return false;
}

/** Parse and validate a `ContextPolicyEvidenceV1` document. */
export function parsePolicyEvidence(raw: unknown): ContextPolicyEvidence {
  const value = object(raw, "evidence");
  keys(value, ["schema_version", "records"], ["evaluations"], "evidence");
  if (value.schema_version !== 1) throw bad("unsupported evidence schema_version");
  const records = list(value.records, "records", MAX_EVIDENCE_RECORDS).map((item, i) => outcomeRecord(item, `records[${i}]`));
  let previousRecord: StrategyOutcomeRecord | undefined;
  for (const record of records) {
    if (previousRecord && !before(recordKey(previousRecord), recordKey(record))) {
      throw bad("records must be strictly sorted by workload, strategy and day");
    }
    previousRecord = record;
  }
  const evaluations = list(value.evaluations ?? [], "evaluations", STRATEGIES.length).map((item, i) => evaluation(item, `evaluations[${i}]`));
  let previousEvaluation: StrategyEvaluation | undefined;
  for (const item of evaluations) {
    if (previousEvaluation && STRATEGIES.indexOf(previousEvaluation.strategy) >= STRATEGIES.indexOf(item.strategy)) {
      throw bad("evaluations must be one per strategy, sorted");
    }
    previousEvaluation = item;
  }
  return Object.freeze({ records: Object.freeze(records), evaluations: Object.freeze(evaluations) });
}

function step(raw: unknown, label: string): LineageStep {
  const value = object(raw, label);
  keys(value, ["sequence", "kind", "timestamp", "fields"], [], label);
  const fields = object(value.fields, `${label}.fields`);
  const parsed: Record<string, string> = {};
  for (const [key, item] of Object.entries(fields)) {
    if (!FIELD.test(key)) throw bad(`${label}.fields has an invalid name`);
    parsed[key] = text(item, `${label}.fields.${key}`);
  }
  return Object.freeze({
    sequence: int(value.sequence, `${label}.sequence`),
    kind: oneOf(value.kind, STEP_KINDS, `${label}.kind`),
    timestamp: text(value.timestamp, `${label}.timestamp`),
    fields: Object.freeze(parsed),
  });
}

function summary(raw: unknown, label: string): DeliverySummary {
  const value = object(raw, label);
  keys(value, ["outcome", "destination", "policy_digest", "final_context", "inspected", "delivered", "withheld", "redactions", "tokens_original", "tokens_delivered"], [], label);
  const result: DeliverySummary = Object.freeze({
    outcome: oneOf(value.outcome, DELIVERY_OUTCOMES, `${label}.outcome`),
    destination: text(value.destination, `${label}.destination`),
    policyDigest: value.policy_digest === null ? null : digest(value.policy_digest, `${label}.policy_digest`),
    inspected: int(value.inspected, `${label}.inspected`),
    delivered: int(value.delivered, `${label}.delivered`),
    withheld: int(value.withheld, `${label}.withheld`),
    redactions: int(value.redactions, `${label}.redactions`),
    tokensOriginal: int(value.tokens_original, `${label}.tokens_original`),
    tokensDelivered: int(value.tokens_delivered, `${label}.tokens_delivered`),
    finalContext: value.final_context === null ? null : digest(value.final_context, `${label}.final_context`),
  });
  if (result.tokensDelivered > result.tokensOriginal) throw bad(`${label} delivered more tokens than original`);
  return result;
}

function delivery(raw: unknown, label: string): LineageDelivery {
  const value = object(raw, label);
  keys(value, ["digest", "verified"], ["error", "summary"], label);
  const verified = bool(value.verified, `${label}.verified`);
  if (verified !== ("summary" in value) || verified === ("error" in value)) {
    throw bad(`${label}: a verified delivery has a summary, an unverified one an error`);
  }
  return Object.freeze({
    digest: digest(value.digest, `${label}.digest`),
    verified,
    error: "error" in value ? oneOf(value.error, DELIVERY_ERRORS, `${label}.error`) : null,
    summary: verified ? summary(value.summary, `${label}.summary`) : null,
  });
}

/** Parse and validate a `TaskLineageV1` document. */
export function parseTaskLineage(raw: unknown): TaskLineage {
  const value = object(raw, "lineage");
  keys(value, ["schema_version", "task_id", "steps", "deliveries", "outcome", "gaps"], ["scope", "ledger_error"], "lineage");
  if (value.schema_version !== 1) throw bad("unsupported lineage schema_version");
  const gaps = list(value.gaps, "gaps", GAPS.length).map((gap) => oneOf(gap, GAPS, "gaps"));
  if (new Set(gaps).size !== gaps.length) throw bad("gaps must not repeat");
  const ledgerError = value.ledger_error === undefined ? null : text(value.ledger_error, "ledger_error", MAX_TEXT_BYTES);
  if ((ledgerError !== null) !== gaps.includes("ledger_unverified")) {
    throw bad("a ledger error and the ledger_unverified gap go together");
  }
  const steps = list(value.steps, "steps", MAX_LINEAGE_ITEMS).map((item, i) => step(item, `steps[${i}]`));
  const deliveries = list(value.deliveries, "deliveries", MAX_LINEAGE_ITEMS).map((item, i) => delivery(item, `deliveries[${i}]`));
  if (deliveries.some((d) => !d.verified) !== gaps.includes("delivery_unverified")) {
    throw bad("an unverified delivery and the delivery_unverified gap go together");
  }
  return Object.freeze({
    taskId: text(value.task_id, "task_id"),
    scope: value.scope === undefined ? null : text(value.scope, "scope", MAX_TEXT_BYTES),
    steps: Object.freeze(steps),
    deliveries: Object.freeze(deliveries),
    ledgerError,
    outcome: oneOf(value.outcome, OUTCOMES, "outcome"),
    gaps: Object.freeze(gaps),
    isComplete: gaps.length === 0,
  });
}

export type ContextStoreScope = Readonly<{ projectId?: string; tenantId?: string }>;

function storeRequest(scope: ContextStoreScope, taskId?: string): Buffer {
  const request: Record<string, unknown> = {
    schema_version: 1,
    transport_version: 1,
    engine_interface_version: ENGINE_INTERFACE_VERSION,
  };
  for (const [key, item] of [["project_id", scope.projectId], ["tenant_id", scope.tenantId], ["task_id", taskId]] as const) {
    if (item === undefined) continue;
    if (typeof item !== "string" || item.trim() === "" || Buffer.byteLength(item, "utf8") > MAX_REF_BYTES) {
      throw new ValidationError(`${key} must be a non-empty bounded string`);
    }
    request[key] = item;
  }
  return canonicalBytes(request);
}

async function read(
  engine: SubprocessEngineClient,
  operation: "context-lineage" | "context-policy-evidence",
  projectRoot: string,
  payload: Buffer,
  body: string,
): Promise<unknown> {
  const raw = await engine.contextStoreRead(operation, projectRoot, payload, MAX_STORE_REQUEST_BYTES, MAX_STORE_RESPONSE_BYTES);
  let document: unknown;
  try {
    document = strictJsonLoads(raw, "context store");
  } catch (error) {
    throw bad(`response is not strict JSON (${String(error)})`);
  }
  const value = object(document, "response");
  keys(value, ["schema_version", "transport_version", "engine_interface_version", body], [], "response");
  if (value.schema_version !== 1 || value.transport_version !== 1 || value.engine_interface_version !== ENGINE_INTERFACE_VERSION) {
    throw bad("unsupported response versions");
  }
  return value[body];
}

/**
 * The scope's read-strategy evidence. Without `projectId` the Engine uses the
 * project root, as `lean-ctx autopilot` does by default.
 */
export async function readPolicyEvidence(
  engine: SubprocessEngineClient,
  projectRoot: string,
  scope: ContextStoreScope = {},
): Promise<ContextPolicyEvidence> {
  return parsePolicyEvidence(await read(engine, "context-policy-evidence", projectRoot, storeRequest(scope), "evidence"));
}

/** One task's lineage within the scope. */
export async function readTaskLineage(
  engine: SubprocessEngineClient,
  projectRoot: string,
  taskId: string,
  scope: ContextStoreScope = {},
): Promise<TaskLineage> {
  if (typeof taskId !== "string" || taskId.trim() === "") throw new ValidationError("taskId must be a non-empty string");
  return parseTaskLineage(await read(engine, "context-lineage", projectRoot, storeRequest(scope, taskId), "lineage"));
}
