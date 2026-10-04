// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/**
 * Preview: the information gateway's egress admission and decision receipts.
 *
 * `admitEgress` runs one model request through the local Engine's egress
 * admission (`lean-ctx engine egress-admit`) before the caller sends it:
 * secrets are masked, restricted content is withheld, and the request is
 * classified. Parsing mirrors the Engine's validation; unknown fields and
 * values are rejected. Contract `leanctx-gateway-preview` 0.1 — may change in
 * minor releases.
 */

import { EngineProtocolError, ValidationError } from "./errors.js";
import type { SubprocessEngineClient } from "./engine.js";
import { strictJsonLoads } from "./protocol.js";

export const GATEWAY_PREVIEW_CONTRACT = "leanctx-gateway-preview" as const;
export const GATEWAY_PREVIEW_VERSION = "0.1.0" as const;
export const EGRESS_SCHEMA_VERSION = 1 as const;
export const MAX_EGRESS_REQUEST_BYTES = 8 * 1024 * 1024;
export const MAX_EGRESS_RESPONSE_BYTES = 16 * 1024 * 1024;

const MAX_REF_BYTES = 512;
const MAX_DECISIONS = 4096;
const MAX_REASON_CODES = 32;
const MAX_SIGNALS = 32;
const U32 = 2 ** 32 - 1;

export const CLASSIFICATIONS = ["public", "internal", "confidential", "restricted"] as const;
const DISPOSITIONS = ["forward", "rewritten", "refused"] as const;
const MODES = ["developer", "governed", "sovereign"] as const;
const PRINCIPAL_KINDS = ["person", "team", "organization", "project", "agent", "session", "workload", "unknown"] as const;
const LOCALITIES = ["local", "remote", "unknown"] as const;
const OUTCOMES = ["delivered", "withheld", "failed"] as const;
const CONTEXT_DISPOSITIONS = [
  "allow", "allow_minimized", "allow_redacted", "allow_summary_only",
  "allow_local_model_only", "allow_with_approval", "quarantine", "deny",
] as const;
const TRANSFORMATIONS = [
  "redaction", "classification", "selection", "deduplication",
  "structural_extraction", "compression", "summarization", "recovery", "reranking",
] as const;
const CATEGORIES = ["secret", "pii", "prompt_injection", "classification", "policy", "custom"] as const;
const SEVERITIES = ["info", "low", "medium", "high", "critical"] as const;
const COVERAGE_KINDS = ["complete", "partial", "unsupported", "failed", "not_required"] as const;
const DETECTOR_STATUSES = ["completed", "failed", "timed_out", "skipped"] as const;

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const REASON = /^[a-z][a-z0-9_.]{2,63}$/;

export type Classification = (typeof CLASSIFICATIONS)[number];
export type EgressDisposition = (typeof DISPOSITIONS)[number];

export type ContextPrincipal = Readonly<{ kind: (typeof PRINCIPAL_KINDS)[number]; id?: string }>;
export type ContextDestination = Readonly<{
  provider: string;
  locality: (typeof LOCALITIES)[number];
  model?: string;
  organizationManaged: boolean;
  accountRef?: string;
  region?: string;
}>;
export type DetectorCoverage = Readonly<{
  kind: (typeof COVERAGE_KINDS)[number];
  bytesTotal: number;
  bytesInspected: number;
  chunksTotal: number;
  chunksInspected: number;
  reason?: string;
}>;
export type SecuritySignal = Readonly<{
  detectorId: string;
  detectorVersion: string;
  category: (typeof CATEGORIES)[number];
  severity: (typeof SEVERITIES)[number];
  evidenceCount: number;
  coverage: DetectorCoverage;
  status: (typeof DETECTOR_STATUSES)[number];
  latencyUs: number;
  calibrated: boolean;
  confidenceMilli?: number;
}>;
export type ContextDecision = Readonly<{
  object: string;
  disposition: (typeof CONTEXT_DISPOSITIONS)[number];
  reasonCodes: readonly string[];
  signals: readonly SecuritySignal[];
  requiredTransformations: readonly (typeof TRANSFORMATIONS)[number][];
  deliversContent: boolean;
}>;
export type ContextDecisionReceipt = Readonly<{
  receiptId: string;
  mode: (typeof MODES)[number];
  principal: ContextPrincipal;
  destination: ContextDestination;
  sources: Readonly<{ inspected: number; permitted: number; selected: number; blocked: number }>;
  security: Readonly<{ redactions: number; blockedObjects: number; quarantinedObjects: number; injectionSignals: number; incompleteCoverage: number }>;
  tokens: Readonly<{ original: number; delivered: number }>;
  outcome: (typeof OUTCOMES)[number];
  durationUs: number;
  decisions: readonly ContextDecision[];
  signals: readonly SecuritySignal[];
  policy?: Readonly<{ id: string; digest: string; version?: string }>;
  task?: string;
  finalContext?: string;
  quality?: Readonly<Record<string, unknown>>;
}>;
export type EgressAdmission = Readonly<{
  disposition: EgressDisposition;
  body: Readonly<Record<string, unknown>> | null;
  refusal: string | null;
  classification: Classification | null;
  receipt: ContextDecisionReceipt | null;
  /** True when `body` may be sent; a refused request must not be. */
  maySend: boolean;
}>;

type Json = Record<string, unknown>;

function bad(message: string): EngineProtocolError {
  return new EngineProtocolError(`egress admission: ${message}`);
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

function int(value: unknown, label: string, maximum = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > maximum) {
    throw bad(`${label} must be an integer in 0..${maximum}`);
  }
  return value;
}

function bool(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw bad(`${label} must be a boolean`);
  return value;
}

function ref(value: unknown, label: string): string {
  // eslint-disable-next-line no-control-regex
  if (typeof value !== "string" || value.trim() === "" || Buffer.byteLength(value, "utf8") > MAX_REF_BYTES || /[\u0000-\u001f\u007f]/.test(value)) {
    throw bad(`${label} must be a bounded reference`);
  }
  return value;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) throw bad(`${label} has unknown value ${JSON.stringify(value)}`);
  return value as T;
}

function digest(value: unknown, label: string): string {
  if (typeof value !== "string" || !DIGEST.test(value)) throw bad(`${label} must be a sha256 digest`);
  return value;
}

function reasons(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length > MAX_REASON_CODES) throw bad(`${label} must be a bounded list`);
  for (const code of value) if (typeof code !== "string" || !REASON.test(code)) throw bad(`${label} holds an invalid reason code`);
  return [...value] as string[];
}

function optionalRef(value: Json, key: string, label: string): Record<string, string> {
  return key in value ? { [label]: ref(value[key], key) } : {};
}

function principal(raw: unknown): ContextPrincipal {
  const value = object(raw, "principal");
  keys(value, ["kind"], ["id"], "principal");
  const kind = oneOf(value.kind, PRINCIPAL_KINDS, "principal.kind");
  if (kind === "unknown") {
    if ("id" in value) throw bad("an unknown principal must not carry an identity");
    return Object.freeze({ kind });
  }
  if (!("id" in value)) throw bad("a known principal requires an id");
  return Object.freeze({ kind, id: ref(value.id, "principal.id") });
}

function destination(raw: unknown): ContextDestination {
  const value = object(raw, "destination");
  keys(value, ["provider", "locality"], ["model", "organization_managed", "account_ref", "region"], "destination");
  return Object.freeze({
    provider: ref(value.provider, "destination.provider"),
    locality: oneOf(value.locality, LOCALITIES, "destination.locality"),
    organizationManaged: bool(value.organization_managed ?? false, "destination.organization_managed"),
    ...optionalRef(value, "model", "model"),
    ...optionalRef(value, "account_ref", "accountRef"),
    ...optionalRef(value, "region", "region"),
  });
}

function coverage(raw: unknown): DetectorCoverage {
  const value = object(raw, "coverage");
  keys(value, ["kind", "bytes_total", "bytes_inspected", "chunks_total", "chunks_inspected"], ["reason"], "coverage");
  const parsed: DetectorCoverage = Object.freeze({
    kind: oneOf(value.kind, COVERAGE_KINDS, "coverage.kind"),
    bytesTotal: int(value.bytes_total, "coverage.bytes_total"),
    bytesInspected: int(value.bytes_inspected, "coverage.bytes_inspected"),
    chunksTotal: int(value.chunks_total, "coverage.chunks_total", U32),
    chunksInspected: int(value.chunks_inspected, "coverage.chunks_inspected", U32),
    ...("reason" in value ? { reason: reasons([value.reason], "coverage.reason")[0] } : {}),
  });
  if (parsed.bytesInspected > parsed.bytesTotal || parsed.chunksInspected > parsed.chunksTotal) throw bad("coverage must not inspect more than the object holds");
  const allBytes = parsed.bytesInspected === parsed.bytesTotal;
  if (parsed.kind === "complete" && !allBytes) throw bad("complete coverage must inspect every byte");
  if (parsed.kind === "partial" && allBytes) throw bad("partial coverage must leave bytes uninspected");
  return parsed;
}

function signal(raw: unknown): SecuritySignal {
  const value = object(raw, "signal");
  keys(value, ["detector", "category", "severity", "evidence_count", "coverage", "status", "latency_us"], ["calibrated", "confidence_milli"], "signal");
  const detector = object(value.detector, "signal.detector");
  keys(detector, ["id", "version"], [], "signal.detector");
  const parsed: SecuritySignal = Object.freeze({
    detectorId: ref(detector.id, "signal.detector.id"),
    detectorVersion: ref(detector.version, "signal.detector.version"),
    category: oneOf(value.category, CATEGORIES, "signal.category"),
    severity: oneOf(value.severity, SEVERITIES, "signal.severity"),
    evidenceCount: int(value.evidence_count, "signal.evidence_count", U32),
    coverage: coverage(value.coverage),
    status: oneOf(value.status, DETECTOR_STATUSES, "signal.status"),
    latencyUs: int(value.latency_us, "signal.latency_us"),
    calibrated: bool(value.calibrated ?? false, "signal.calibrated"),
    ...("confidence_milli" in value ? { confidenceMilli: int(value.confidence_milli, "signal.confidence_milli", 1000) } : {}),
  });
  if ((parsed.status === "failed" || parsed.status === "timed_out") && parsed.coverage.kind === "complete") {
    throw bad("a failed or timed-out detector cannot claim complete coverage");
  }
  return parsed;
}

function decision(raw: unknown): ContextDecision {
  const value = object(raw, "decision");
  keys(value, ["object", "disposition"], ["reason_codes", "signals", "required_transformations"], "decision");
  const signals = value.signals ?? [];
  const transformations = value.required_transformations ?? [];
  if (!Array.isArray(signals) || signals.length > MAX_SIGNALS) throw bad("decision.signals must be a bounded list");
  if (!Array.isArray(transformations)) throw bad("decision.required_transformations must be a list");
  const disposition = oneOf(value.disposition, CONTEXT_DISPOSITIONS, "decision.disposition");
  const reasonCodes = reasons(value.reason_codes ?? [], "decision.reason_codes");
  if (disposition !== "allow" && reasonCodes.length === 0) throw bad("every non-allow decision requires at least one reason code");
  return Object.freeze({
    object: digest(value.object, "decision.object"),
    disposition,
    reasonCodes: Object.freeze(reasonCodes),
    signals: Object.freeze(signals.map(signal)),
    requiredTransformations: Object.freeze(transformations.map((kind) => oneOf(kind, TRANSFORMATIONS, "decision.required_transformations"))),
    deliversContent: CONTEXT_DISPOSITIONS.indexOf(disposition) <= CONTEXT_DISPOSITIONS.indexOf("allow_local_model_only"),
  });
}

function counts<K extends string>(raw: unknown, names: readonly K[], label: string, maximum: number): Record<K, number> {
  const value = object(raw, label);
  keys(value, names, [], label);
  return Object.fromEntries(names.map((name) => [name, int(value[name], `${label}.${name}`, maximum)])) as Record<K, number>;
}

/** Parse and validate a `ContextDecisionReceiptV1` document. */
export function parseDecisionReceipt(raw: unknown): ContextDecisionReceipt {
  const value = object(raw, "receipt");
  keys(value, ["schema_version", "receipt_id", "mode", "principal", "destination", "sources", "security", "tokens", "outcome", "duration_us"], ["task", "policy", "decisions", "final_context", "quality"], "receipt");
  if (value.schema_version !== 1) throw bad("unsupported receipt schema_version");
  const rawDecisions = value.decisions ?? [];
  if (!Array.isArray(rawDecisions) || rawDecisions.length > MAX_DECISIONS) throw bad("receipt.decisions must be a bounded list");
  const decisions = rawDecisions.map(decision);
  const sources = counts(value.sources, ["inspected", "permitted", "selected", "blocked"] as const, "receipt.sources", U32);
  if (sources.selected > sources.permitted || sources.permitted + sources.blocked > sources.inspected) {
    throw bad("source counts must satisfy selected <= permitted and permitted + blocked <= inspected");
  }
  const security = counts(value.security, ["redactions", "blocked_objects", "quarantined_objects", "injection_signals", "incomplete_coverage"] as const, "receipt.security", U32);
  if (security.blocked_objects !== decisions.filter((d) => d.disposition === "deny").length
    || security.quarantined_objects !== decisions.filter((d) => d.disposition === "quarantine").length) {
    throw bad("security counts must equal the recorded deny/quarantine decisions");
  }
  const outcome = oneOf(value.outcome, OUTCOMES, "receipt.outcome");
  const finalContext = "final_context" in value ? digest(value.final_context, "receipt.final_context") : undefined;
  if ((outcome === "delivered") !== (finalContext !== undefined)) throw bad("exactly a delivered receipt names the delivered context digest");
  let policy: ContextDecisionReceipt["policy"];
  if ("policy" in value) {
    const raw = object(value.policy, "receipt.policy");
    keys(raw, ["id", "digest"], ["version"], "receipt.policy");
    policy = Object.freeze({ id: ref(raw.id, "receipt.policy.id"), digest: digest(raw.digest, "receipt.policy.digest"), ...("version" in raw ? { version: ref(raw.version, "receipt.policy.version") } : {}) });
  }
  const tokens = counts(value.tokens, ["original", "delivered"] as const, "receipt.tokens", Number.MAX_SAFE_INTEGER);
  return Object.freeze({
    receiptId: ref(value.receipt_id, "receipt.receipt_id"),
    mode: oneOf(value.mode, MODES, "receipt.mode"),
    principal: principal(value.principal),
    destination: destination(value.destination),
    sources: Object.freeze(sources),
    security: Object.freeze({
      redactions: security.redactions,
      blockedObjects: security.blocked_objects,
      quarantinedObjects: security.quarantined_objects,
      injectionSignals: security.injection_signals,
      incompleteCoverage: security.incomplete_coverage,
    }),
    tokens: Object.freeze(tokens),
    outcome,
    durationUs: int(value.duration_us, "receipt.duration_us"),
    decisions: Object.freeze(decisions),
    signals: Object.freeze(decisions.flatMap((d) => d.signals)),
    ...(policy ? { policy } : {}),
    ...("task" in value ? { task: ref(value.task, "receipt.task") } : {}),
    ...(finalContext ? { finalContext } : {}),
    ...("quality" in value ? { quality: Object.freeze({ ...object(value.quality, "receipt.quality") }) } : {}),
  });
}

/** Parse and validate an `EngineEgressAdmissionResponseV1` document. */
export function parseEgressAdmission(raw: unknown): EgressAdmission {
  const value = object(raw, "response");
  keys(value, ["schema_version", "disposition"], ["body", "refusal", "classification", "receipt"], "response");
  if (value.schema_version !== EGRESS_SCHEMA_VERSION) throw bad("unsupported egress schema_version");
  const disposition = oneOf(value.disposition, DISPOSITIONS, "disposition");
  const body = value.body ?? null;
  const refusal = value.refusal ?? null;
  if (disposition === "refused") {
    if (body !== null || typeof refusal !== "string" || refusal.trim() === "") throw bad("a refused request carries a refusal and no body");
  } else if (refusal !== null || body === null || typeof body !== "object" || Array.isArray(body)) {
    throw bad("an admitted request carries a body object and no refusal");
  }
  const classification = value.classification === undefined ? null : oneOf(value.classification, CLASSIFICATIONS, "classification");
  return Object.freeze({
    disposition,
    body: body as EgressAdmission["body"],
    refusal: refusal as string | null,
    classification,
    receipt: value.receipt === undefined ? null : parseDecisionReceipt(value.receipt),
    maySend: disposition !== "refused",
  });
}

export type EgressRequest = Readonly<{ provider: string; upstreamBase: string; body: Readonly<Record<string, unknown>> }>;

/**
 * Admit one model request through the local Engine before sending it. Send
 * `admission.body`, never the original body, and only when `maySend`.
 */
export async function admitEgress(engine: SubprocessEngineClient, projectRoot: string, request: EgressRequest): Promise<EgressAdmission> {
  if (typeof request.provider !== "string" || request.provider.trim() === "") throw new ValidationError("provider must be a non-empty string");
  if (typeof request.upstreamBase !== "string" || !/^https?:\/\//.test(request.upstreamBase)) throw new ValidationError("upstreamBase must be an http(s) URL");
  if (request.body === null || typeof request.body !== "object" || Array.isArray(request.body)) throw new ValidationError("body must be a JSON object");
  // Model requests carry fractional numbers (temperature, top_p), so the
  // document is plain JSON rather than the integer-only canonical form.
  let payload: Buffer;
  try {
    payload = Buffer.from(JSON.stringify({ schema_version: EGRESS_SCHEMA_VERSION, provider: request.provider, upstream_base: request.upstreamBase, body: request.body }), "utf8");
  } catch (error) {
    throw new ValidationError("body is not JSON data", { cause: error });
  }
  const raw = await engine.egressAdmit(projectRoot, payload, MAX_EGRESS_REQUEST_BYTES, MAX_EGRESS_RESPONSE_BYTES);
  let document: unknown;
  try {
    document = strictJsonLoads(raw, "egress admission");
  } catch (error) {
    throw bad(`response is not strict JSON (${String(error)})`);
  }
  return parseEgressAdmission(document);
}

// Context Store reads (task lineage, policy evidence): leanctx-context-store-preview.
export * from "./context_store.js";
