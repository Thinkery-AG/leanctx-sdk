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

import { InstanceProtocolError, ValidationError } from "./errors.js";
import { createHash, createHmac } from "node:crypto";
import type { SubprocessInstanceClient } from "./engine.js";
import { strictJsonLoads } from "./protocol.js";

export const GATEWAY_PREVIEW_CONTRACT = "leanctx-gateway-preview" as const;
export const GATEWAY_PREVIEW_VERSION = "0.1.0" as const;
export const EGRESS_SCHEMA_VERSION = 1 as const;
export const EGRESS_PREPARE_SCHEMA_VERSION = 2 as const;
export const MAX_EGRESS_ATTEMPTS = 32;
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
const KEY_ID = /^rk-[0-9a-f]{32}$/;
const OBJECT_DIGEST_ALGORITHMS = ["hmac-sha256"] as const;
const OBJECT_DOMAIN = Buffer.from("leanctx.receipt.object.v2\u0000", "utf8");
const KEY_ID_DOMAIN = Buffer.from("leanctx.receipt.key-id.v2", "utf8");
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
/** How a schema_version 2 receipt digests its decision objects: keyed under the installation key `keyId`. */
export type ReceiptObjectDigest = Readonly<{ algorithm: (typeof OBJECT_DIGEST_ALGORITHMS)[number]; keyId: string }>;
/**
 * In schemaVersion 2 every `decisions[].object` is HMAC-SHA256 under the installation's receipt key
 * (`objectDigest`); in schemaVersion 1 the plain SHA-256 of the object. `finalContext` is plain SHA-256 in both.
 */
export type ContextDecisionReceipt = Readonly<{
  schemaVersion: 1 | 2;
  objectDigest?: ReceiptObjectDigest;
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
export type EgressPreparation = Readonly<{
  decision: EgressAdmission;
  /** Exact UTF-8 JSON text; transmit verbatim instead of serializing decision.body. */
  preparedBody: string | null;
  admissionReceiptDigest: string | null;
  maySend: boolean;
}>;
export type EgressTransport = "http" | "websocket";
export type EgressTransportResult = "response_observed" | "no_response_observed";
export type EgressTransportFailure = "connect" | "timeout" | "cancelled" | "other";
export type EgressHostReportRequest = Readonly<{
  schema_version: 1;
  admission_receipt_digest: string;
  attempt: number;
  transport: EgressTransport;
  result: EgressTransportResult;
  http_status?: number;
  failure?: EgressTransportFailure;
}>;
export type EgressHostReportResponse = Readonly<{
  observationDigest: string;
  observation: Readonly<Record<string, unknown>>;
}>;

type Json = Record<string, unknown>;

function bad(message: string): InstanceProtocolError {
  return new InstanceProtocolError(`egress admission: ${message}`);
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

function receiptKey(key: Uint8Array): Uint8Array {
  if (!(key instanceof Uint8Array) || key.length !== 32) throw new ValidationError("a receipt key is exactly 32 bytes");
  return key;
}

/** The public id a schema_version 2 receipt names for the 32-byte `key`. */
export function receiptKeyId(key: Uint8Array): string {
  return `rk-${createHmac("sha256", receiptKey(key)).update(KEY_ID_DOMAIN).digest("hex").slice(0, 32)}`;
}

/** The schema_version 2 `object` for the plain `sha256:<hex>` a schema_version 1 receipt would name. */
export function keyedObjectDigest(key: Uint8Array, plainObject: string): string {
  if (typeof plainObject !== "string" || !DIGEST.test(plainObject)) throw new ValidationError("plainObject must be a sha256 digest");
  const mac = createHmac("sha256", receiptKey(key)).update(OBJECT_DOMAIN).update(Buffer.from(plainObject, "ascii"));
  return `sha256:${mac.digest("hex")}`;
}

function objectDigest(raw: unknown): ReceiptObjectDigest {
  const value = object(raw, "receipt.object_digest");
  keys(value, ["algorithm", "key_id"], [], "receipt.object_digest");
  if (typeof value.key_id !== "string" || !KEY_ID.test(value.key_id)) throw bad("receipt.object_digest.key_id must be rk- and 32 hex digits");
  return Object.freeze({ algorithm: oneOf(value.algorithm, OBJECT_DIGEST_ALGORITHMS, "receipt.object_digest.algorithm"), keyId: value.key_id });
}

/** Parse and validate a `ContextDecisionReceiptV1` document (receipt schema_version 1 or 2). */
export function parseDecisionReceipt(raw: unknown): ContextDecisionReceipt {
  const value = object(raw, "receipt");
  keys(value, ["schema_version", "receipt_id", "mode", "principal", "destination", "sources", "security", "tokens", "outcome", "duration_us"], ["task", "policy", "decisions", "final_context", "quality", "object_digest"], "receipt");
  if (value.schema_version !== 1 && value.schema_version !== 2) throw bad("unsupported receipt schema_version");
  const schemaVersion: 1 | 2 = value.schema_version;
  if ((schemaVersion === 2) !== ("object_digest" in value)) throw bad("exactly a schema_version 2 receipt names its object digest key");
  const keyed = "object_digest" in value ? objectDigest(value.object_digest) : undefined;
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
    schemaVersion,
    ...(keyed ? { objectDigest: keyed } : {}),
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

function jsonEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((item, index) => jsonEqual(item, right[index]));
  }
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return false;
  const a = left as Json;
  const b = right as Json;
  const keysA = Object.keys(a);
  return keysA.length === Object.keys(b).length && keysA.every((key) => key in b && jsonEqual(a[key], b[key]));
}

/** Parse EngineEgressPrepareResponseV2 while preserving its exact body text. */
export function parseEgressPreparation(raw: unknown): EgressPreparation {
  const value = object(raw, "preparation");
  keys(value, ["schema_version", "decision"], ["prepared_body", "admission_receipt_digest"], "preparation");
  if (value.schema_version !== EGRESS_PREPARE_SCHEMA_VERSION) throw bad("unsupported egress preparation schema_version");
  const decision = parseEgressAdmission(value.decision);
  const receipt = decision.receipt;
  if (receipt !== null) {
    if (decision.disposition === "refused") {
      if (receipt.outcome === "delivered" || receipt.finalContext != null) {
        throw bad("decision receipt outcome/final_context do not match disposition");
      }
    } else if (receipt.outcome !== "delivered" || receipt.finalContext == null) {
      throw bad("decision receipt outcome/final_context do not match disposition");
    }
  }
  const preparedBody = value.prepared_body ?? null;
  if (decision.disposition === "refused") {
    if (preparedBody !== null) throw bad("a refused preparation cannot carry prepared bytes");
  } else {
    if (typeof preparedBody !== "string") throw bad("a dispatchable preparation must carry exact bytes");
    let parsedBody: unknown;
    try { parsedBody = strictJsonLoads(Buffer.from(preparedBody, "utf8"), "prepared egress body"); }
    catch { throw bad("prepared bytes are not strict JSON"); }
    if (parsedBody === null || typeof parsedBody !== "object" || Array.isArray(parsedBody)
      || !jsonEqual(parsedBody, decision.body)) throw bad("prepared bytes do not match the admitted decision body");
    const finalContext = decision.receipt?.finalContext;
    if (finalContext !== undefined) {
      const digestValue = `sha256:${createHash("sha256").update(Buffer.from(preparedBody, "utf8")).digest("hex")}`;
      if (digestValue !== finalContext) throw bad("prepared bytes do not match the decision receipt digest");
    }
  }
  const admissionReceiptDigest = value.admission_receipt_digest === undefined || value.admission_receipt_digest === null
    ? null : digest(value.admission_receipt_digest, "admission_receipt_digest");
  if (admissionReceiptDigest !== null && decision.receipt === null) throw bad("egress admission digest requires its decision receipt");
  return Object.freeze({ decision, preparedBody, admissionReceiptDigest,
    maySend: decision.maySend && preparedBody !== null });
}

export type EgressRequest = Readonly<{ provider: string; upstreamBase: string; body: Readonly<Record<string, unknown>> }>;

/** Prepare one request; callers transmit preparedBody verbatim only when maySend. */
export async function prepareEgress(engine: SubprocessInstanceClient, projectRoot: string, request: EgressRequest): Promise<EgressPreparation> {
  if (typeof request.provider !== "string" || request.provider.trim() === "") throw new ValidationError("provider must be a non-empty string");
  if (typeof request.upstreamBase !== "string" || !/^https?:\/\//.test(request.upstreamBase)) throw new ValidationError("upstreamBase must be an http(s) URL");
  if (request.body === null || typeof request.body !== "object" || Array.isArray(request.body)) throw new ValidationError("body must be a JSON object");
  let payload: Buffer;
  try {
    payload = Buffer.from(JSON.stringify({ schema_version: EGRESS_SCHEMA_VERSION, provider: request.provider, upstream_base: request.upstreamBase, body: request.body }), "utf8");
  } catch (error) { throw new ValidationError("body is not JSON data", { cause: error }); }
  if (payload.byteLength > MAX_EGRESS_REQUEST_BYTES) throw new ValidationError("egress request exceeds its bound");
  const raw = await engine.egressPrepare(projectRoot, payload, MAX_EGRESS_REQUEST_BYTES, 32 * 1024 * 1024);
  let document: unknown;
  try { document = strictJsonLoads(raw, "egress preparation"); }
  catch (error) { throw bad(`response is not strict JSON (${String(error)})`); }
  return parseEgressPreparation(document);
}

function buildHostReportRequest(input: Omit<EgressHostReportRequest, "schema_version">): EgressHostReportRequest {
  const request: EgressHostReportRequest = { schema_version: 1,
    admission_receipt_digest: digest(input.admission_receipt_digest, "admission_receipt_digest"),
    attempt: int(input.attempt, "attempt", MAX_EGRESS_ATTEMPTS),
    transport: oneOf(input.transport, ["http", "websocket"], "transport"),
    result: oneOf(input.result, ["response_observed", "no_response_observed"], "result"),
    ...(input.http_status === undefined ? {} : { http_status: int(input.http_status, "http_status", 599) }),
    ...(input.failure === undefined ? {} : { failure: oneOf(input.failure, ["connect", "timeout", "cancelled", "other"] as const, "failure") }),
  };
  if (request.attempt === 0) throw new ValidationError("attempt must be in 1..32");
  if (request.http_status !== undefined && request.http_status < 100) throw new ValidationError("http_status must be in 100..599");
  if (request.result === "response_observed") {
    if (request.failure !== undefined || (request.transport === "http" && request.http_status === undefined)) throw new ValidationError("response_observed requires no failure and an HTTP status for HTTP");
  } else if (request.http_status !== undefined || request.failure === undefined) {
    throw new ValidationError("no_response_observed requires a bounded failure and no HTTP status");
  }
  return request;
}

export function parseEgressHostReport(raw: unknown, request: EgressHostReportRequest): EgressHostReportResponse {
  const value = object(raw, "report");
  keys(value, ["schema_version", "observation_digest", "observation"], [], "report");
  if (value.schema_version !== EGRESS_SCHEMA_VERSION) throw bad("unsupported egress report schema_version");
  const observationDigest = digest(value.observation_digest, "observation_digest");
  const observation = object(value.observation, "observation");
  keys(observation, ["schema_version", "admission_receipt_digest", "attempt", "transport", "observer", "result"], ["http_status", "failure"], "observation");
  if (observation.schema_version !== EGRESS_SCHEMA_VERSION) throw bad("unsupported observation schema_version");
  if (observation.observer !== "host_reported") throw bad("host report response must be HostReported");
  for (const key of ["admission_receipt_digest", "attempt", "transport", "result", "http_status", "failure"] as const) {
    const actualPresent = key in observation;
    const expectedPresent = key in request;
    if (actualPresent !== expectedPresent || (expectedPresent && observation[key] !== request[key])) throw bad(`report observation does not match request field ${key}`);
  }
  digest(observation.admission_receipt_digest, "observation.admission_receipt_digest");
  int(observation.attempt, "observation.attempt", MAX_EGRESS_ATTEMPTS);
  oneOf(observation.transport, ["http", "websocket"], "observation.transport");
  oneOf(observation.result, ["response_observed", "no_response_observed"], "observation.result");
  if ("http_status" in observation) {
    const status = int(observation.http_status, "observation.http_status", 599);
    if (status < 100) throw bad("observation.http_status must be in 100..599");
  }
  if ("failure" in observation) oneOf(observation.failure, ["connect", "timeout", "cancelled", "other"], "observation.failure");
  const canonicalObservation = { ...request, observer: "host_reported" };
  const canonicalText = JSON.stringify(Object.fromEntries(Object.keys(canonicalObservation).sort().map((key) => [key, canonicalObservation[key as keyof typeof canonicalObservation]])));
  const expectedDigest = `sha256:${createHash("sha256").update(Buffer.from(canonicalText, "utf8")).digest("hex")}`;
  if (observationDigest !== expectedDigest) throw bad("observation_digest does not match the reported observation");
  return Object.freeze({ observationDigest, observation: Object.freeze({ ...observation }) });
}

/** Report host-observed facts; this API cannot claim EngineObserved. */
export async function reportEgress(engine: SubprocessInstanceClient, projectRoot: string, input: Omit<EgressHostReportRequest, "schema_version">): Promise<EgressHostReportResponse> {
  const request = buildHostReportRequest(input);
  const payload = Buffer.from(JSON.stringify(request), "utf8");
  if (payload.byteLength > 4096) throw new ValidationError("egress report request exceeds its bound");
  const raw = await engine.egressReport(projectRoot, payload, 4096, 32 * 1024 * 1024);
  let document: unknown;
  try { document = strictJsonLoads(raw, "egress report"); }
  catch (error) { throw bad(`response is not strict JSON (${String(error)})`); }
  return parseEgressHostReport(document, request);
}

/**
 * Admit one model request through the local Engine before sending it. Send
 * `admission.body`, never the original body, and only when `maySend`.
 */
export async function admitEgress(engine: SubprocessInstanceClient, projectRoot: string, request: EgressRequest): Promise<EgressAdmission> {
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

// Material change: union public context-store names with the private surface.
export {
  CONTEXT_STORE_PREVIEW_CONTRACT,
  CONTEXT_STORE_PREVIEW_VERSION,
  MAX_STORE_REQUEST_BYTES,
  MAX_STORE_RESPONSE_BYTES,
  TASK_CLASSES,
  LANGUAGES,
  SIZES,
  STRATEGIES,
  parsePolicyEvidence,
  parseTaskLineage,
  readPolicyEvidence,
  readTaskLineage,
} from "./context_store.js";
export type {
  Workload,
  QualityEvidence,
  SecurityEvidence,
  StrategyOutcomeRecord,
  StrategyEvaluation,
  ContextPolicyEvidence,
  LineageStep,
  DeliverySummary,
  LineageDelivery,
  TaskLineage,
  ContextStoreScope,
} from "./context_store.js";
