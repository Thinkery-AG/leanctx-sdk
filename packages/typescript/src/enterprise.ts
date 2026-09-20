// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** Authenticated Enterprise Engine planning and source-execution adapter. */

import {
  _postJson,
  _validateBaseUrl,
  _validateCredential,
  _validateTimeout,
  type EngineContextClientOptions,
} from "./context.js";
import { ConfigurationError, EngineProtocolError, ValidationError } from "./errors.js";
import {
  MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_PLAN_RESPONSE_BYTES,
  SCHEMA_VERSION,
  EnginePlanningRequest,
  parseSourcePlan,
  type EngineSourcePlan,
} from "./planning.js";
import {
  MAX_ENGINE_SOURCE_EXECUTION_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_EXECUTION_V2_TOTAL_BYTES,
  collectSourcePlanIntegerPaths,
  normalizeSourceIds,
  parseSourceExecutionV2Response,
  validateExecutionRequest,
  validatePlanningEvaluationTime,
  validateSourcePlanScope,
  type EngineSourceExecutionV2,
} from "./source_execution.js";
import {
  MAX_ENGINE_PROVIDER_EXECUTION_REQUEST_BYTES,
  MAX_ENGINE_PROVIDER_EXECUTION_RESPONSE_BYTES,
  parseProviderExecutionResponse,
  validateProviderExecutionRequest,
  type EngineProviderExecutionResponse,
} from "./provider_execution.js";
import { canonicalBytes, strictJsonLoads, validateDigest } from "./protocol.js";
import {
  MAX_ENGINE_OUTCOME_RESPONSE_BYTES, parseEngineOutcomeResponse, validateOutcomeRequest,
  type EngineOutcomeResponse, type EngineOutcomeSignal,
} from "./outcome.js";

const CONTEXT_PLAN_PATH = "/v1/engine/context-plan";
const PROVIDER_EXECUTION_PATH = "/v1/engine/provider-execute";
const EXECUTION_V2_PATH = "/v2/engine/context-execute";
const MAX_U64 = Number.MAX_SAFE_INTEGER;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type JsonRecord = Record<string, unknown>;

function protocolRecord(value: unknown, fieldName: string): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new EngineProtocolError(fieldName + " must be an object");
  }
  return value as JsonRecord;
}

function protocolU64(value: unknown, fieldName: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > MAX_U64) {
    throw new EngineProtocolError(fieldName + " must be an unsigned bounded integer");
  }
  return value;
}

function protocolUuid(value: unknown, fieldName: string): string {
  try {
    return configuredUuid(value, fieldName);
  } catch (cause) {
    throw new EngineProtocolError(fieldName + " must be a non-nil UUID", { cause });
  }
}

function parseEnterpriseSourcePlanResponse(
  raw: Uint8Array,
  request: EnginePlanningRequest,
  sourceIds: readonly string[],
  tenantId: string,
): Readonly<{
  schema_version: 1;
  tenant_id: string;
  governance_revision: number;
  plan: EngineSourcePlan;
}> {
  const label = "Enterprise Engine source-plan response";
  let initial: unknown;
  try {
    initial = strictJsonLoads(raw, label);
  } catch (cause) {
    throw new EngineProtocolError(label + " is not valid JSON", { cause });
  }
  const initialResponse = protocolRecord(initial, label);
  let parsed: unknown;
  try {
    parsed = strictJsonLoads(raw, label, [
      ["schema_version"],
      ["governance_revision"],
      ...collectSourcePlanIntegerPaths(initialResponse.plan),
    ]);
  } catch (cause) {
    throw new EngineProtocolError(label + " is not valid JSON", { cause });
  }
  const response = protocolRecord(parsed, label);
  const expectedKeys = new Set(["schema_version", "tenant_id", "governance_revision", "plan"]);
  const keys = Object.keys(response);
  if (keys.length !== expectedKeys.size || keys.some((key) => !expectedKeys.has(key))) {
    throw new EngineProtocolError("Enterprise Engine source-plan response fields do not match the v1 contract");
  }
  if (protocolU64(response.schema_version, "schema_version") !== 1) {
    throw new EngineProtocolError("Enterprise Engine source-plan response schema_version is unsupported");
  }
  const responseTenant = protocolUuid(response.tenant_id, "tenant_id");
  if (responseTenant !== tenantId) {
    throw new EngineProtocolError("Enterprise Engine source-plan response tenant binding does not match");
  }
  const governanceRevision = protocolU64(response.governance_revision, "governance_revision");
  let plan: EngineSourcePlan;
  try {
    plan = parseSourcePlan(canonicalBytes(response.plan), request);
  } catch (cause) {
    throw new EngineProtocolError("Enterprise Engine source plan failed validation", { cause });
  }
  validateSourcePlanScope(plan, sourceIds);
  return {
    schema_version: 1,
    tenant_id: responseTenant,
    governance_revision: governanceRevision,
    plan,
  };
}

export type EnterpriseEngineClientOptions = EngineContextClientOptions;

export type ContextExecuteV2Options = Readonly<{
  planningEvaluationTime?: string;
}>;

export type ProviderExecuteOptions = Readonly<{
  maxOutputTokens: number;
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
  private readonly planEndpoint: URL;
  private readonly providerEndpoint: URL;
  private readonly outcomeEndpoint: URL;
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
    const allowLoopbackHttp = options.allowLoopbackHttp ?? false;
    this.endpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, EXECUTION_V2_PATH);
    this.planEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, CONTEXT_PLAN_PATH);
    this.providerEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, PROVIDER_EXECUTION_PATH);
    this.outcomeEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, "/v1/engine/context-outcome");
    this.credential = _validateCredential(credential);
    this.timeout = _validateTimeout(options.timeout ?? 30);
    this.baseUrl = baseUrl;
    this.tenantId = configuredUuid(tenantId, "tenant_id");
  }

  /**
   * Plan authenticated Enterprise source IDs without accepting source bodies.
   *
   * The Enterprise host remains authoritative for source admission and content;
   * this adapter validates only the returned digest-bound planning projection.
   */
  async contextPlanSources(
    request: EnginePlanningRequest,
    sourceIds: readonly string[],
  ): Promise<Readonly<{
    schema_version: 1;
    tenant_id: string;
    governance_revision: number;
    plan: EngineSourcePlan;
  }>> {
    if (!(request instanceof EnginePlanningRequest)) {
      throw new ValidationError("contextPlanSources requires EnginePlanningRequest");
    }
    const normalizedIds = normalizeSourceIds(sourceIds);
    const payload = canonicalBytes({
      planning: request.toDict(),
      source_ids: [...normalizedIds],
    });
    if (payload.byteLength > MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES) {
      throw new ValidationError("Enterprise Engine source planning request exceeds its byte bound");
    }
    const raw = await _postJson(
      this.planEndpoint,
      this.credential,
      payload,
      this.timeout,
      MAX_ENGINE_SOURCE_PLAN_RESPONSE_BYTES,
      "Enterprise source planning",
    );
    return parseEnterpriseSourcePlanResponse(raw, request, normalizedIds, this.tenantId);
  }

  /**
   * Execute one concrete provider plan using host-owned admission and dispatch.
   * The response remains a provider projection with unknown acceptance.
   */
  async providerExecute(
    task: Readonly<Record<string, unknown>>,
    plan: Readonly<Record<string, unknown>>,
    request: EnginePlanningRequest,
    sourceIds: readonly string[],
    expectedGovernanceRevision: number,
    expectedBindingDigest: string,
    options: ProviderExecuteOptions,
  ): Promise<EngineProviderExecutionResponse> {
    const normalizedIds = normalizeSourceIds(sourceIds);
    const normalized = validateProviderExecutionRequest(
      task,
      plan,
      request,
      this.tenantId,
      options.maxOutputTokens,
    );
    const governanceRevision = inputU64(
      expectedGovernanceRevision,
      "expected_governance_revision",
    );
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
    if (planningEvaluationTime !== undefined) {
      materialization.planning_evaluation_time = planningEvaluationTime;
    }
    const payload = canonicalBytes({
      schema_version: SCHEMA_VERSION,
      task: normalized.task,
      plan: normalized.plan,
      materialization,
      max_output_tokens: normalized.maxOutputTokens,
    });
    if (payload.byteLength > MAX_ENGINE_PROVIDER_EXECUTION_REQUEST_BYTES) {
      throw new ValidationError(
        "Enterprise Engine provider execution request exceeds its byte bound",
      );
    }
    const raw = await _postJson(
      this.providerEndpoint,
      this.credential,
      payload,
      this.timeout,
      MAX_ENGINE_PROVIDER_EXECUTION_RESPONSE_BYTES,
      "Enterprise provider execution",
    );
    return parseProviderExecutionResponse(
      raw,
      normalized.task,
      normalized.plan,
      this.tenantId,
    );
  }

  /** Carry operator signals; the host retains signer, evaluation and ledger authority. */
  async contextOutcome(
    taskId: string, receiptDigest: string, contextDecisionDigest: string, signals: readonly EngineOutcomeSignal[],
  ): Promise<EngineOutcomeResponse> {
    const payload = validateOutcomeRequest(taskId, receiptDigest, contextDecisionDigest, signals);
    const raw = await _postJson(this.outcomeEndpoint, this.credential, payload, this.timeout,
      MAX_ENGINE_OUTCOME_RESPONSE_BYTES, "Enterprise outcome");
    return parseEngineOutcomeResponse(raw, taskId, receiptDigest, contextDecisionDigest, this.tenantId);
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
