// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** Bounded operator-attestation carrier; signer admission belongs to the host. */

import { EngineProtocolError, ValidationError } from "./errors.js";
import { canonicalBytes, exactKeys, sha256Digest, strictJsonLoads, validateDigest } from "./protocol.js";
import {
  MAX_ENGINE_SOURCE_EXECUTION_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES,
  MAX_ENGINE_SOURCE_RECEIPT_DOCUMENT_BYTES,
} from "./source_execution.js";

export const MAX_ENGINE_OUTCOME_REQUEST_BYTES = MAX_ENGINE_SOURCE_EXECUTION_REQUEST_BYTES;
export const MAX_ENGINE_OUTCOME_RESPONSE_BYTES = MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES;

export type EngineOutcomeSignal = Readonly<{
  signal_type: "build_success" | "tests_passing" | "lint_clean" | "typecheck_passing"
    | "human_acceptance" | "pr_merge" | "ci_passing" | "correction" | "rollback";
  value: "unknown" | Readonly<{ boolean: boolean }> | Readonly<{ count: number }>;
}>;

export type EngineOutcomeResponse = Readonly<{
  schema_version: 1;
  tenant_id: string;
  outcome: Readonly<{
    schema_version: 1;
    receipt_id: string;
    receipt_digest: string;
    original_receipt_digest: string;
    acceptance: "accepted" | "rejected";
    already_recorded: boolean;
    receipt_document_json: string;
  }>;
}>;

type RecordValue = Record<string, unknown>;
const SIGNAL_TYPES = new Set([
  "build_success", "tests_passing", "lint_clean", "typecheck_passing",
  "human_acceptance", "pr_merge", "ci_passing", "correction", "rollback",
]);

function record(value: unknown, label: string): RecordValue {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ValidationError(label + " must be an object");
  }
  return value as RecordValue;
}

function text(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || Buffer.byteLength(value) > maximum
      || /[\u0000-\u001f]/u.test(value)) {
    throw new ValidationError(label + " exceeds its UTF-8 text bounds");
  }
  canonicalBytes(value); // Also rejects unpaired UTF-16 surrogates.
  return value;
}

export function validateOutcomeRequest(
  taskId: string, receiptDigest: string, contextDecisionDigest: string,
  signals: readonly EngineOutcomeSignal[],
): Buffer {
  text(taskId, "task_id", 256);
  validateDigest(receiptDigest, "receipt_digest");
  validateDigest(contextDecisionDigest, "context_decision_digest");
  if (!Array.isArray(signals) || signals.length < 1 || signals.length > 16) {
    throw new ValidationError("outcome signals require between 1 and 16 entries");
  }
  for (const signal of signals) {
    exactKeys(signal, ["signal_type", "value"], "outcome signal");
    if (typeof signal.signal_type !== "string" || !SIGNAL_TYPES.has(signal.signal_type)) {
      throw new ValidationError("unsupported outcome signal type");
    }
    if (signal.value === "unknown") continue;
    const value = record(signal.value, "outcome signal value");
    if (Object.hasOwn(value, "boolean")) {
      exactKeys(value, ["boolean"], "outcome signal value");
      if (typeof value.boolean !== "boolean") throw new ValidationError("outcome boolean must be a boolean");
    } else {
      exactKeys(value, ["count"], "outcome signal value");
      if (typeof value.count !== "number" || !Number.isSafeInteger(value.count)
          || value.count < 0 || value.count > 0xffffffff) {
        throw new ValidationError("outcome count must be a bounded unsigned integer");
      }
    }
  }
  const payload = canonicalBytes({
    task_id: taskId, receipt_digest: receiptDigest, context_decision_digest: contextDecisionDigest, signals,
  });
  if (payload.byteLength > MAX_ENGINE_OUTCOME_REQUEST_BYTES) throw new ValidationError("outcome request exceeds byte bound");
  return payload;
}

function validateDocument(raw: Buffer, receiptId: string, acceptance: string, taskId: string, decisionDigest: string): void {
  const document = strictJsonLoads(raw, "outcome receipt document", [["schema_version"]]);
  exactKeys(document, ["schema_version", "receipt_id", "lineage", "chain", "status", "values",
    "outcome", "evidence_refs", "issued_at", "signer", "signature"], "outcome receipt document");
  if (document.schema_version !== 1 || !canonicalBytes(document).equals(raw)) {
    throw new ValidationError("outcome receipt schema/bytes are not canonical v1");
  }
  const identity = Object.fromEntries(Object.entries(document).filter(([key]) => key !== "receipt_id" && key !== "signature"));
  if (sha256Digest(canonicalBytes(identity)) !== receiptId || document.receipt_id !== receiptId) {
    throw new ValidationError("outcome receipt identity differs");
  }
  if (record(document.outcome, "receipt outcome").state !== acceptance) throw new ValidationError("outcome receipt state differs");
  validateDigest(record(document.chain, "receipt chain").previous_receipt_id, "outcome predecessor");
  if (record(document.lineage, "receipt lineage").task_id !== taskId) throw new ValidationError("outcome receipt task differs");
  if (!Array.isArray(document.evidence_refs)) throw new ValidationError("outcome evidence references are invalid");
  const runtime = document.evidence_refs.filter((entry: unknown) => entry !== null && typeof entry === "object"
    && !Array.isArray(entry) && (entry as RecordValue).kind === "runtime") as RecordValue[];
  if (runtime.length === 0 || runtime.some((entry) => entry.digest !== decisionDigest
      || entry.uri !== "artifact://execution/evidence/" + decisionDigest.slice("sha256:".length))) {
    throw new ValidationError("outcome runtime evidence differs");
  }
}

/** Validate selected wire/document joins without admitting a signer or evaluating signals. */
export function parseEngineOutcomeResponse(
  raw: Uint8Array, taskId: string, receiptDigest: string, contextDecisionDigest: string, tenantId: string,
): EngineOutcomeResponse {
  try {
    text(taskId, "task_id", 256);
    validateDigest(receiptDigest, "receipt_digest");
    validateDigest(contextDecisionDigest, "context_decision_digest");
    if (raw.byteLength > MAX_ENGINE_OUTCOME_RESPONSE_BYTES) throw new ValidationError("outcome response exceeds byte bound");
    const response = strictJsonLoads(raw, "Enterprise outcome response", [["schema_version"], ["outcome", "schema_version"]]);
    exactKeys(response, ["schema_version", "tenant_id", "outcome"], "outcome response");
    if (response.schema_version !== 1 || response.tenant_id !== tenantId) throw new ValidationError("outcome schema/tenant differs");
    const outcome = response.outcome;
    exactKeys(outcome, ["schema_version", "receipt_id", "receipt_digest", "original_receipt_digest",
      "acceptance", "already_recorded", "receipt_document_json"], "outcome");
    if (outcome.schema_version !== 1 || outcome.original_receipt_digest !== receiptDigest) {
      throw new ValidationError("outcome schema/original receipt differs");
    }
    const receiptId = validateDigest(outcome.receipt_id, "outcome.receipt_id");
    const successorDigest = validateDigest(outcome.receipt_digest, "outcome.receipt_digest");
    if ((outcome.acceptance !== "accepted" && outcome.acceptance !== "rejected")
        || typeof outcome.already_recorded !== "boolean") throw new ValidationError("outcome acceptance/replay projection is invalid");
    const document = text(outcome.receipt_document_json, "outcome document", MAX_ENGINE_SOURCE_RECEIPT_DOCUMENT_BYTES);
    const documentBytes = Buffer.from(document, "utf8");
    if (sha256Digest(documentBytes) !== successorDigest) throw new ValidationError("outcome document digest differs");
    validateDocument(documentBytes, receiptId, outcome.acceptance, taskId, contextDecisionDigest);
    return Object.freeze({ schema_version: 1, tenant_id: tenantId, outcome: Object.freeze({
      schema_version: 1, receipt_id: receiptId, receipt_digest: successorDigest,
      original_receipt_digest: receiptDigest, acceptance: outcome.acceptance,
      already_recorded: outcome.already_recorded, receipt_document_json: document,
    }) });
  } catch (cause) {
    throw new EngineProtocolError("Enterprise outcome response violates the v1 contract", { cause });
  }
}
