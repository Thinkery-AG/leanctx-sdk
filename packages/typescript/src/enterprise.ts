// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** Authenticated Enterprise Engine source-execution adapter. */

import {
  _postJson,
  _validateBaseUrl,
  _validateCredential,
  _validateTimeout,
  type EngineContextClientOptions,
} from "./context.js";
import { ConfigurationError, ValidationError } from "./errors.js";
import { EnginePlanningRequest } from "./planning.js";
import {
  MAX_ENGINE_SOURCE_EXECUTION_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES,
  normalizeSourceIds,
  parseSourceExecutionV2Response,
  validateExecutionRequest,
  validatePlanningEvaluationTime,
  type EngineSourceExecutionV2,
} from "./source_execution.js";
import { canonicalBytes, validateDigest } from "./protocol.js";

const EXECUTION_V2_PATH = "/v2/engine/context-execute";
const MAX_U64 = Number.MAX_SAFE_INTEGER;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type EnterpriseEngineClientOptions = EngineContextClientOptions;

export type ContextExecuteV2Options = Readonly<{
  planningEvaluationTime?: string;
}>;

function configuredUuid(value: unknown, fieldName: string): string {
  if (
    typeof value !== "string" ||
    value.trim() !== value ||
    !UUID_RE.test(value) ||
    /^0{8}-0{4}-0{4}-0{4}-0{12}$/i.test(value)
  ) {
    throw new ConfigurationError(`${fieldName} must be a non-nil UUID`);
  }
  return value.toLowerCase();
}

function inputU64(value: unknown, fieldName: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > MAX_U64) {
    throw new ValidationError(`${fieldName} must be an unsigned bounded integer`);
  }
  return value;
}

/**
 * Authenticated remote Enterprise operations; source bodies and signer
 * settings remain server-owned and are never accepted by this adapter.
 */
export class EnterpriseEngineClient {
  readonly baseUrl: string;
  readonly tenantId: string;
  readonly timeout: number;
  private readonly endpoint: URL;
  private readonly credential: string;

  constructor(
    baseUrl: string,
    credential: string,
    tenantId: string,
    options: EnterpriseEngineClientOptions = {},
  ) {
    if (options.allowLoopbackHttp !== undefined && typeof options.allowLoopbackHttp !== "boolean") {
      throw new ConfigurationError("allowLoopbackHttp must be a boolean");
    }
    this.endpoint = _validateBaseUrl(baseUrl, options.allowLoopbackHttp ?? false, EXECUTION_V2_PATH);
    this.credential = _validateCredential(credential);
    this.timeout = _validateTimeout(options.timeout ?? 30);
    this.baseUrl = baseUrl;
    this.tenantId = configuredUuid(tenantId, "tenant_id");
  }

  /**
   * Execute one declared local-native source plan and preserve the exact v2
   * receipt-document string. This does not verify signer trust or acceptance.
   */
  async contextExecuteV2(
    task: Readonly<Record<string, unknown>>,
    plan: Readonly<Record<string, unknown>>,
    request: EnginePlanningRequest,
    sourceIds: readonly string[],
    expectedGovernanceRevision: number,
    expectedBindingDigest: string,
    options: ContextExecuteV2Options = {},
  ): Promise<EngineSourceExecutionV2> {
    const normalizedIds = normalizeSourceIds(sourceIds);
    const normalized = validateExecutionRequest(task, plan, request, this.tenantId);
    const governanceRevision = inputU64(expectedGovernanceRevision, "expected_governance_revision");
    const bindingDigest = validateDigest(expectedBindingDigest, "expected_binding_digest");
    const planningEvaluationTime = options.planningEvaluationTime === undefined
      ? undefined
      : validatePlanningEvaluationTime(options.planningEvaluationTime);
    const materialization: Record<string, unknown> = {
      planning: request.toDict(),
      source_ids: [...normalizedIds],
      expected_governance_revision: governanceRevision,
      expected_binding_digest: bindingDigest,
    };
    if (planningEvaluationTime !== undefined) materialization.planning_evaluation_time = planningEvaluationTime;
    const payload = canonicalBytes({
      task: normalized.task,
      plan: normalized.plan,
      materialization,
    });
    if (payload.byteLength > MAX_ENGINE_SOURCE_EXECUTION_REQUEST_BYTES) {
      throw new ValidationError("Enterprise Engine source execution v2 request exceeds its byte bound");
    }
    const raw = await _postJson(
      this.endpoint,
      this.credential,
      payload,
      this.timeout,
      MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES,
      "Enterprise source execution v2",
    );
    return parseSourceExecutionV2Response(
      raw,
      request,
      normalizedIds,
      normalized.task,
      normalized.plan,
      this.tenantId,
      governanceRevision,
      bindingDigest,
      planningEvaluationTime,
    );
  }
}
