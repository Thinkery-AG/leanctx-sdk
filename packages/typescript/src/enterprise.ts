// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** Authenticated Enterprise Engine planning and source-execution adapter. */

import {
  _postJson,
  _validateBaseUrl,
  _validateCredential,
  _validateTimeout,
  type InstanceContextClientOptions,
} from "./context.js";
import { ConfigurationError, InstanceProtocolError, ValidationError } from "./errors.js";
import {
  ENGINE_INTERFACE_VERSION,
  MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES,
  MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES,
  MAX_ENGINE_SOURCE_PLAN_RESPONSE_BYTES,
  SCHEMA_VERSION,
  TRANSPORT_VERSION,
  InstancePlanningRequest,
  MAX_ENGINE_PRESENTER_TTL_SECONDS,
  PresenterCapability,
  parsePresentedUse,
  parsePresenterRevocation,
  parseSourcePlan,
  presentedUseRequest,
  type InstanceContextSourceMaterialization,
  type InstancePresenterGrant,
  type InstanceSourcePlan,
  type PresentedUse,
  type PresentedUseAnswer,
  type PresenterRevocation,
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
  type InstanceSourceExecutionV2,
} from "./source_execution.js";
import {
  MAX_ENGINE_PROVIDER_EXECUTION_REQUEST_BYTES,
  MAX_ENGINE_PROVIDER_EXECUTION_RESPONSE_BYTES,
  parseProviderExecutionResponse,
  validateProviderExecutionRequest,
  type InstanceProviderExecutionResponse,
} from "./provider_execution.js";
import { canonicalBytes, sha256Digest, strictJsonLoads, validateDigest } from "./protocol.js";
import {
  MAX_ENGINE_OUTCOME_RESPONSE_BYTES, parseInstanceOutcomeResponse, validateOutcomeRequest,
  type InstanceOutcomeResponse, type InstanceOutcomeSignal,
} from "./outcome.js";

const CONTEXT_PLAN_PATH = "/v1/engine/context-plan";
const CONTEXT_MATERIALIZE_PATH = "/v1/engine/context-materialize";
const PROVIDER_EXECUTION_PATH = "/v1/engine/provider-execute";
const EXECUTION_V2_PATH = "/v2/engine/context-execute";
const PRESENTER_ENVELOPE_BYTES = 512;
const PRESENTER_UNSUPPORTED =
  "Enterprise suite does not serve presenter capabilities: LeanCTX 3.11.0 or later is required";
const MAX_U64 = Number.MAX_SAFE_INTEGER;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The materialization request reuses the bounded source-planning request budget. */
export const MAX_ENGINE_ENTERPRISE_MATERIALIZATION_REQUEST_BYTES = MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES;
/** Envelope budget plus the separately bounded materialized-context budget. */
export const MAX_ENGINE_ENTERPRISE_MATERIALIZATION_RESPONSE_BYTES =
  MAX_ENGINE_SOURCE_PLAN_RESPONSE_BYTES + MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES;

type JsonRecord = Record<string, unknown>;

function protocolRecord(value: unknown, fieldName: string): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new InstanceProtocolError(fieldName + " must be an object");
  }
  return value as JsonRecord;
}

function protocolU64(value: unknown, fieldName: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > MAX_U64) {
    throw new InstanceProtocolError(fieldName + " must be an unsigned bounded integer");
  }
  return value;
}

function protocolUuid(value: unknown, fieldName: string): string {
  try {
    return configuredUuid(value, fieldName);
  } catch (cause) {
    throw new InstanceProtocolError(fieldName + " must be a non-nil UUID", { cause });
  }
}

function protocolExactKeys(value: JsonRecord, expected: readonly string[], label: string): void {
  const allowed = new Set(expected);
  const keys = Object.keys(value);
  if (keys.length !== allowed.size || keys.some((key) => !allowed.has(key))) {
    throw new InstanceProtocolError(label + " fields do not match the v1 contract");
  }
}

/**
 * The strict JSON reader already rejects lone surrogates, so the materialized
 * body only needs its declared byte bound enforced before it is hashed.
 */
function protocolMaterializedContent(value: unknown): Buffer {
  if (typeof value !== "string") {
    throw new InstanceProtocolError("Enterprise instance materialized content must be a string");
  }
  const encoded = Buffer.from(value, "utf8");
  if (encoded.byteLength > MAX_ENGINE_SOURCE_MATERIALIZED_CONTEXT_BYTES) {
    throw new InstanceProtocolError("Enterprise instance materialized content exceeds its byte bound");
  }
  return encoded;
}

function parseEnterpriseSourcePlanResponse(
  raw: Uint8Array,
  request: InstancePlanningRequest,
  sourceIds: readonly string[],
  tenantId: string,
): Readonly<{
  schema_version: 1;
  tenant_id: string;
  governance_revision: number;
  plan: InstanceSourcePlan;
}> {
  const label = "Enterprise instance source-plan response";
  let initial: unknown;
  try {
    initial = strictJsonLoads(raw, label);
  } catch (cause) {
    throw new InstanceProtocolError(label + " is not valid JSON", { cause });
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
    throw new InstanceProtocolError(label + " is not valid JSON", { cause });
  }
  const response = protocolRecord(parsed, label);
  protocolExactKeys(response, ["schema_version", "tenant_id", "governance_revision", "plan"], label);
  if (protocolU64(response.schema_version, "schema_version") !== 1) {
    throw new InstanceProtocolError("Enterprise instance source-plan response schema_version is unsupported");
  }
  const responseTenant = protocolUuid(response.tenant_id, "tenant_id");
  if (responseTenant !== tenantId) {
    throw new InstanceProtocolError("Enterprise instance source-plan response tenant binding does not match");
  }
  const governanceRevision = protocolU64(response.governance_revision, "governance_revision");
  let plan: InstanceSourcePlan;
  try {
    plan = parseSourcePlan(canonicalBytes(response.plan), request);
  } catch (cause) {
    throw new InstanceProtocolError("Enterprise instance source plan failed validation", { cause });
  }
  validateSourcePlanScope(plan, sourceIds);
  return {
    schema_version: 1,
    tenant_id: responseTenant,
    governance_revision: governanceRevision,
    plan,
  };
}

/** Tenant-bound materialization envelope; not an execution, receipt or acceptance proof. */
export type EnterpriseSourceMaterialization = Readonly<{
  schema_version: 1;
  tenant_id: string;
  governance_revision: number;
  materialization: InstanceContextSourceMaterialization;
}>;

function materializationIntegerPaths(
  response: JsonRecord,
): readonly (readonly (string | number)[])[] {
  const paths: (readonly (string | number)[])[] = [["schema_version"], ["governance_revision"]];
  const value = response.materialization;
  if (value === null || typeof value !== "object" || Array.isArray(value)) return paths;
  const materialization = value as JsonRecord;
  for (const key of ["schema_version", "transport_version", "materialized_token_count"]) {
    if (typeof materialization[key] === "number") paths.push(["materialization", key]);
  }
  for (const path of collectSourcePlanIntegerPaths(materialization.plan)) {
    paths.push(["materialization", ...path]);
  }
  return paths;
}

function parseEnterpriseMaterializationResponse(
  raw: Uint8Array,
  request: InstancePlanningRequest,
  sourceIds: readonly string[],
  tenantId: string,
  expectedGovernanceRevision: number,
  expectedBindingDigest: string,
  planningEvaluationTime: string | undefined,
): EnterpriseSourceMaterialization {
  const label = "Enterprise instance materialization response";
  let initial: unknown;
  try {
    initial = strictJsonLoads(raw, label);
  } catch (cause) {
    throw new InstanceProtocolError(label + " is not valid JSON", { cause });
  }
  let parsed: unknown;
  try {
    parsed = strictJsonLoads(raw, label, materializationIntegerPaths(protocolRecord(initial, label)));
  } catch (cause) {
    throw new InstanceProtocolError(label + " is not valid JSON", { cause });
  }
  const response = protocolRecord(parsed, label);
  protocolExactKeys(response, ["schema_version", "tenant_id", "governance_revision", "materialization"], label);
  if (protocolU64(response.schema_version, "schema_version") !== SCHEMA_VERSION) {
    throw new InstanceProtocolError(label + " schema_version is unsupported");
  }
  const responseTenant = protocolUuid(response.tenant_id, "tenant_id");
  if (responseTenant !== tenantId) {
    throw new InstanceProtocolError(label + " tenant binding does not match");
  }
  const governanceRevision = protocolU64(response.governance_revision, "governance_revision");
  if (governanceRevision !== expectedGovernanceRevision) {
    throw new InstanceProtocolError("Enterprise instance materialization governance revision does not match");
  }
  const materialization = protocolRecord(response.materialization, "materialization");
  protocolExactKeys(materialization, [
    "schema_version",
    "transport_version",
    "engine_interface_version",
    "plan",
    "materialized_digest",
    "materialized_token_count",
    "content",
  ], "Enterprise instance materialization");
  if (protocolU64(materialization.schema_version, "materialization.schema_version") !== SCHEMA_VERSION) {
    throw new InstanceProtocolError("Enterprise instance materialization schema_version is unsupported");
  }
  if (protocolU64(materialization.transport_version, "materialization.transport_version") !== TRANSPORT_VERSION) {
    throw new InstanceProtocolError("Enterprise instance materialization transport_version is unsupported");
  }
  if (materialization.engine_interface_version !== ENGINE_INTERFACE_VERSION) {
    throw new InstanceProtocolError("Enterprise instance materialization engine_interface_version is unsupported");
  }
  let plan: InstanceSourcePlan;
  try {
    plan = parseSourcePlan(canonicalBytes(materialization.plan), request);
  } catch (cause) {
    throw new InstanceProtocolError("Enterprise instance materialization source plan failed validation", { cause });
  }
  validateSourcePlanScope(plan, sourceIds);
  if (plan.binding_digest !== expectedBindingDigest) {
    throw new InstanceProtocolError("Enterprise instance materialization binding digest does not match");
  }
  if (planningEvaluationTime !== undefined) {
    const marker = plan.result.plan.context_plan_evaluation_v1;
    const observed = marker !== null && typeof marker === "object" && !Array.isArray(marker)
      ? (marker as JsonRecord).evaluation_time
      : undefined;
    if (observed !== planningEvaluationTime) {
      throw new InstanceProtocolError("Enterprise instance materialization changed the evaluation time");
    }
  }
  let materializedDigest: string;
  try {
    materializedDigest = validateDigest(materialization.materialized_digest, "materialized_digest");
  } catch (cause) {
    throw new InstanceProtocolError("Enterprise instance materialized digest is invalid", { cause });
  }
  const tokenCount = protocolU64(materialization.materialized_token_count, "materialized_token_count");
  if (tokenCount > plan.result.plan.budget_tokens) {
    throw new InstanceProtocolError("Enterprise instance materialized token metric exceeds the plan budget");
  }
  const encoded = protocolMaterializedContent(materialization.content);
  if (sha256Digest(encoded) !== materializedDigest) {
    throw new InstanceProtocolError("Enterprise instance materialized content digest does not match");
  }
  return {
    schema_version: SCHEMA_VERSION,
    tenant_id: responseTenant,
    governance_revision: governanceRevision,
    materialization: {
      schema_version: SCHEMA_VERSION,
      transport_version: TRANSPORT_VERSION,
      engine_interface_version: ENGINE_INTERFACE_VERSION,
      plan,
      materialized_digest: materializedDigest,
      materialized_token_count: tokenCount,
      content: materialization.content as string,
    },
  };
}

export type EnterpriseInstanceClientOptions = InstanceContextClientOptions;

export type ContextExecuteV2Options = Readonly<{
  planningEvaluationTime?: string;
}>;

export type ContextMaterializeSourcesOptions = Readonly<{
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

/** The suite envelope of a presenter operation: exact members and its schema version. */
function presenterEnvelope(
  raw: Uint8Array,
  label: string,
  members: readonly string[],
  schemaVersion: number,
): JsonRecord {
  let parsed: unknown;
  try {
    parsed = strictJsonLoads(raw, label);
  } catch (cause) {
    throw new InstanceProtocolError(label + " is not valid JSON", { cause });
  }
  const response = protocolRecord(parsed, label);
  protocolExactKeys(response, members, label);
  if (response.schema_version !== schemaVersion) {
    throw new InstanceProtocolError(label + " schema_version is unsupported");
  }
  return response;
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
export class EnterpriseInstanceClient {
  readonly baseUrl: string;
  readonly tenantId: string;
  readonly timeout: number;
  private readonly endpoint: URL;
  private readonly planEndpoint: URL;
  private readonly materializeEndpoint: URL;
  private readonly providerEndpoint: URL;
  private readonly outcomeEndpoint: URL;
  private readonly materializeV2Endpoint: URL;
  private readonly presentedUseEndpoint: URL;
  private readonly presenterRevokeEndpoint: URL;
  private readonly credential: string;

  constructor(
    baseUrl: string,
    credential: string,
    tenantId: string,
    options: EnterpriseInstanceClientOptions = {},
  ) {
    if (options.allowLoopbackHttp !== undefined && typeof options.allowLoopbackHttp !== "boolean") {
      throw new ConfigurationError("allowLoopbackHttp must be a boolean");
    }
    const allowLoopbackHttp = options.allowLoopbackHttp ?? false;
    this.endpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, EXECUTION_V2_PATH);
    this.planEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, CONTEXT_PLAN_PATH);
    this.materializeEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, CONTEXT_MATERIALIZE_PATH);
    this.providerEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, PROVIDER_EXECUTION_PATH);
    this.outcomeEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, "/v1/engine/context-outcome");
    this.materializeV2Endpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, "/v1/engine/context-materialize-v2");
    this.presentedUseEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, "/v1/engine/presented-use");
    this.presenterRevokeEndpoint = _validateBaseUrl(baseUrl, allowLoopbackHttp, "/v1/engine/presenter-revoke");
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
    request: InstancePlanningRequest,
    sourceIds: readonly string[],
  ): Promise<Readonly<{
    schema_version: 1;
    tenant_id: string;
    governance_revision: number;
    plan: InstanceSourcePlan;
  }>> {
    if (!(request instanceof InstancePlanningRequest)) {
      throw new ValidationError("contextPlanSources requires InstancePlanningRequest");
    }
    const normalizedIds = normalizeSourceIds(sourceIds);
    const payload = canonicalBytes({
      planning: request.toDict(),
      source_ids: [...normalizedIds],
    });
    if (payload.byteLength > MAX_ENGINE_SOURCE_PLAN_REQUEST_BYTES) {
      throw new ValidationError("Enterprise instance source planning request exceeds its byte bound");
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
   * Materialize an already-planned tenant-bound source plan over authenticated
   * HTTP. The caller supplies only the governance revision and binding digest
   * it observed during planning; source bodies stay host-owned. The returned
   * content is a bounded Engine projection, not an execution, a receipt, a
   * signature check, or proof that the host accepted the task.
   */
  async contextMaterializeSources(
    request: InstancePlanningRequest,
    sourceIds: readonly string[],
    expectedGovernanceRevision: number,
    expectedBindingDigest: string,
    options: ContextMaterializeSourcesOptions = {},
  ): Promise<EnterpriseSourceMaterialization> {
    if (!(request instanceof InstancePlanningRequest)) {
      throw new ValidationError("contextMaterializeSources requires InstancePlanningRequest");
    }
    const normalizedIds = normalizeSourceIds(sourceIds);
    const governanceRevision = inputU64(expectedGovernanceRevision, "expected_governance_revision");
    const bindingDigest = validateDigest(expectedBindingDigest, "expected_binding_digest");
    const planningEvaluationTime = options.planningEvaluationTime === undefined
      ? undefined
      : validatePlanningEvaluationTime(options.planningEvaluationTime);
    const body: Record<string, unknown> = {
      planning: request.toDict(),
      source_ids: [...normalizedIds],
      expected_governance_revision: governanceRevision,
      expected_binding_digest: bindingDigest,
    };
    if (planningEvaluationTime !== undefined) body.planning_evaluation_time = planningEvaluationTime;
    const payload = canonicalBytes(body);
    if (payload.byteLength > MAX_ENGINE_ENTERPRISE_MATERIALIZATION_REQUEST_BYTES) {
      throw new ValidationError("Enterprise instance materialization request exceeds its byte bound");
    }
    const raw = await _postJson(
      this.materializeEndpoint,
      this.credential,
      payload,
      this.timeout,
      MAX_ENGINE_ENTERPRISE_MATERIALIZATION_RESPONSE_BYTES,
      "Enterprise instance materialization",
    );
    return parseEnterpriseMaterializationResponse(
      raw,
      request,
      normalizedIds,
      this.tenantId,
      governanceRevision,
      bindingDigest,
      planningEvaluationTime,
    );
  }

  /**
   * `contextMaterializeSources` under a presenter capability. `{ ttlSeconds }` has a
   * capability issued and returned once; a held `PresenterCapability` presents under
   * it again and travels in the `x-leanctx-presenter` request header, never in the
   * body. It proves the holder of the capability and nothing else, and is usable only
   * with the API key it was issued to.
   */
  async contextMaterializeSourcesV2(
    request: InstancePlanningRequest,
    sourceIds: readonly string[],
    expectedGovernanceRevision: number,
    expectedBindingDigest: string,
    presenter: { ttlSeconds: number } | PresenterCapability,
    options: ContextMaterializeSourcesOptions = {},
  ): Promise<Readonly<{
    schema_version: 2;
    tenant_id: string;
    governance_revision: number;
    materialization: Readonly<{
      schema_version: 2;
      materialization: InstanceContextSourceMaterialization;
      presenter: InstancePresenterGrant;
    }>;
  }>> {
    if (!(request instanceof InstancePlanningRequest)) {
      throw new ValidationError("contextMaterializeSourcesV2 requires InstancePlanningRequest");
    }
    const normalizedIds = normalizeSourceIds(sourceIds);
    const governanceRevision = inputU64(expectedGovernanceRevision, "expected_governance_revision");
    const bindingDigest = validateDigest(expectedBindingDigest, "expected_binding_digest");
    const planningEvaluationTime = options.planningEvaluationTime === undefined
      ? undefined
      : validatePlanningEvaluationTime(options.planningEvaluationTime);
    const held = presenter instanceof PresenterCapability ? presenter : null;
    const ttlSeconds = held === null ? (presenter as { ttlSeconds?: unknown } | null)?.ttlSeconds : undefined;
    if (
      held === null
      && (typeof ttlSeconds !== "number" || !Number.isInteger(ttlSeconds)
        || ttlSeconds < 1 || ttlSeconds > MAX_ENGINE_PRESENTER_TTL_SECONDS)
    ) {
      throw new ValidationError("presenter must be a PresenterCapability or a lifetime of 1 to 86400 seconds");
    }
    const body: Record<string, unknown> = {
      planning: request.toDict(),
      source_ids: [...normalizedIds],
      expected_governance_revision: governanceRevision,
      expected_binding_digest: bindingDigest,
      presenter: held === null ? { mode: "issue", ttl_seconds: ttlSeconds } : { mode: "renew" },
    };
    if (planningEvaluationTime !== undefined) body.planning_evaluation_time = planningEvaluationTime;
    const payload = canonicalBytes(body);
    if (payload.byteLength > MAX_ENGINE_ENTERPRISE_MATERIALIZATION_REQUEST_BYTES) {
      throw new ValidationError("Enterprise instance materialization request exceeds its byte bound");
    }
    const raw = await _postJson(
      this.materializeV2Endpoint,
      this.credential,
      payload,
      this.timeout,
      MAX_ENGINE_ENTERPRISE_MATERIALIZATION_RESPONSE_BYTES + PRESENTER_ENVELOPE_BYTES,
      "Enterprise instance materialization",
      { capability: held?.reveal(), unsupported: PRESENTER_UNSUPPORTED },
    );
    const label = "Enterprise instance materialization v2 response";
    const response = presenterEnvelope(raw, label, ["schema_version", "tenant_id", "governance_revision", "materialization"], 2);
    const engine = protocolRecord(response.materialization, label);
    protocolExactKeys(engine, ["schema_version", "materialization", "presenter"], label);
    if (engine.schema_version !== 2) throw new InstanceProtocolError(label + " schema_version is unsupported");
    // The inner exchange is the unchanged v1 one: validate it as such.
    const v1 = parseEnterpriseMaterializationResponse(
      canonicalBytes({
        schema_version: 1,
        tenant_id: response.tenant_id,
        governance_revision: response.governance_revision,
        materialization: engine.materialization,
      }),
      request,
      normalizedIds,
      this.tenantId,
      governanceRevision,
      bindingDigest,
      planningEvaluationTime,
    );
    const grant = protocolRecord(engine.presenter, label + ".presenter");
    const reference = grant.presenter_ref;
    const expiresAt = grant.expires_at_unix;
    const secret = grant.capability;
    if (
      Object.keys(grant).some((key) => !["presenter_ref", "expires_at_unix", "capability"].includes(key))
      || typeof reference !== "string" || !/^presenter:[0-9a-f]{32}$/.test(reference)
      || typeof expiresAt !== "number" || !Number.isSafeInteger(expiresAt) || expiresAt <= 0
      || (secret !== undefined) !== (held === null)
    ) {
      throw new InstanceProtocolError("presenter grant does not match the v2 contract");
    }
    let capability: PresenterCapability | null = null;
    if (secret !== undefined) {
      try {
        capability = new PresenterCapability(secret as string);
      } catch (cause) {
        throw new InstanceProtocolError("presenter capability is malformed", { cause });
      }
      if (v1.materialization.content.includes(capability.reveal())) {
        throw new InstanceProtocolError("materialized content must not contain the presenter capability");
      }
    }
    return {
      schema_version: 2,
      tenant_id: v1.tenant_id,
      governance_revision: v1.governance_revision,
      materialization: {
        schema_version: 2,
        materialization: v1.materialization,
        presenter: { presenter_ref: reference as string, expires_at_unix: expiresAt as number, capability },
      },
    };
  }

  /**
   * Use text that was materialized under `capability`: `{ mode: "check", content }`,
   * `{ mode: "retain", content }` or `{ mode: "expand", recovery_id }`. `sourceIds`
   * are the governed sources the text was materialized from — exactly those, or the
   * use is refused. The suite checks the API key, its tenant and its current rights
   * to every source first and again before it answers; a refused capability is a
   * `PolicyAdmissionError`.
   */
  async presentedUse(
    capability: PresenterCapability,
    sourceIds: readonly string[],
    expectedGovernanceRevision: number,
    asked: PresentedUse,
  ): Promise<Readonly<{ schema_version: 1; tenant_id: string; governance_revision: number; use: PresentedUseAnswer }>> {
    if (!(capability instanceof PresenterCapability)) {
      throw new ValidationError("presentedUse requires a PresenterCapability");
    }
    const normalizedIds = normalizeSourceIds(sourceIds);
    if (normalizedIds.length === 0) throw new ValidationError("presentedUse requires at least one source id");
    const governanceRevision = inputU64(expectedGovernanceRevision, "expected_governance_revision");
    const raw = await _postJson(
      this.presentedUseEndpoint,
      this.credential,
      canonicalBytes({
        schema_version: 1,
        source_ids: [...normalizedIds],
        expected_governance_revision: governanceRevision,
        use: presentedUseRequest(asked),
      }),
      this.timeout,
      MAX_ENGINE_ENTERPRISE_MATERIALIZATION_RESPONSE_BYTES + PRESENTER_ENVELOPE_BYTES,
      "Enterprise presented use",
      { capability: capability.reveal(), unsupported: PRESENTER_UNSUPPORTED },
    );
    const label = "Enterprise presented use response";
    const response = presenterEnvelope(raw, label, ["schema_version", "tenant_id", "governance_revision", "use"], 1);
    if (protocolUuid(response.tenant_id, "tenant_id") !== this.tenantId) {
      throw new InstanceProtocolError(label + " tenant binding does not match");
    }
    if (protocolU64(response.governance_revision, "governance_revision") !== governanceRevision) {
      throw new InstanceProtocolError(label + " governance revision does not match");
    }
    let use: PresentedUseAnswer;
    try {
      use = parsePresentedUse(protocolRecord(response.use, label + ".use"), asked, capability);
    } catch (cause) {
      if (cause instanceof ValidationError) throw new InstanceProtocolError(label + " failed validation", { cause });
      throw cause;
    }
    if (
      use.usable
      && (use.source_ids.length !== normalizedIds.length || !use.source_ids.every((id) => normalizedIds.includes(id)))
    ) {
      throw new InstanceProtocolError(label + " is outside requested sources");
    }
    return { schema_version: 1, tenant_id: this.tenantId, governance_revision: governanceRevision, use };
  }

  /**
   * End `capability`: from now on it presents and reuses nothing. Only the API key it
   * was issued to can revoke it.
   */
  async revokePresenter(
    capability: PresenterCapability,
  ): Promise<Readonly<{ schema_version: 1; tenant_id: string; revocation: PresenterRevocation }>> {
    if (!(capability instanceof PresenterCapability)) {
      throw new ValidationError("revokePresenter requires a PresenterCapability");
    }
    const raw = await _postJson(
      this.presenterRevokeEndpoint,
      this.credential,
      canonicalBytes({ schema_version: 1 }),
      this.timeout,
      PRESENTER_ENVELOPE_BYTES,
      "Enterprise presenter revocation",
      { capability: capability.reveal(), unsupported: PRESENTER_UNSUPPORTED },
    );
    const label = "Enterprise presenter revocation response";
    const response = presenterEnvelope(raw, label, ["schema_version", "tenant_id", "revocation"], 1);
    if (protocolUuid(response.tenant_id, "tenant_id") !== this.tenantId) {
      throw new InstanceProtocolError(label + " tenant binding does not match");
    }
    try {
      const revocation = parsePresenterRevocation(protocolRecord(response.revocation, label + ".revocation"));
      return { schema_version: 1, tenant_id: this.tenantId, revocation };
    } catch (cause) {
      if (cause instanceof ValidationError) throw new InstanceProtocolError(label + " failed validation", { cause });
      throw cause;
    }
  }

  /**
   * Execute one concrete provider plan using host-owned admission and dispatch.
   * The response remains a provider projection with unknown acceptance.
   */
  async providerExecute(
    task: Readonly<Record<string, unknown>>,
    plan: Readonly<Record<string, unknown>>,
    request: InstancePlanningRequest,
    sourceIds: readonly string[],
    expectedGovernanceRevision: number,
    expectedBindingDigest: string,
    options: ProviderExecuteOptions,
  ): Promise<InstanceProviderExecutionResponse> {
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
        "Enterprise instance provider execution request exceeds its byte bound",
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
    taskId: string, receiptDigest: string, contextDecisionDigest: string, signals: readonly InstanceOutcomeSignal[],
  ): Promise<InstanceOutcomeResponse> {
    const payload = validateOutcomeRequest(taskId, receiptDigest, contextDecisionDigest, signals);
    const raw = await _postJson(this.outcomeEndpoint, this.credential, payload, this.timeout,
      MAX_ENGINE_OUTCOME_RESPONSE_BYTES, "Enterprise outcome");
    return parseInstanceOutcomeResponse(raw, taskId, receiptDigest, contextDecisionDigest, this.tenantId);
  }

  /**
   * Execute one declared local-native source plan and preserve the exact v2
   * receipt-document string. This does not verify signer trust or acceptance.
   */
  async contextExecuteV2(
    task: Readonly<Record<string, unknown>>,
    plan: Readonly<Record<string, unknown>>,
    request: InstancePlanningRequest,
    sourceIds: readonly string[],
    expectedGovernanceRevision: number,
    expectedBindingDigest: string,
    options: ContextExecuteV2Options = {},
  ): Promise<InstanceSourceExecutionV2> {
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
      throw new ValidationError("Enterprise instance source execution v2 request exceeds its byte bound");
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
