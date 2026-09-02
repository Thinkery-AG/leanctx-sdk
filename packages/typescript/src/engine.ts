/** Strict subprocess client for Engine Interface v1. */

import { accessSync, closeSync, constants, fchmodSync, fsyncSync, openSync, realpathSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { mkdtemp, rmdir } from "node:fs/promises";
import { delimiter, isAbsolute, normalize, relative, resolve, sep } from "node:path";
import { spawn, type ChildProcess } from "node:child_process";
import {
  ArtifactIntegrityError,
  CompatibilityError,
  ConfigurationError,
  EngineExecutionError,
  EngineProtocolError,
  EngineRejected,
  EngineTimeout,
  EngineUnavailable,
  PolicyAdmissionError,
  SourceUnavailableError,
  UnsupportedEngineError,
  ValidationError,
} from "./errors.js";
import {
  canonicalBytes,
  ContextFailure,
  ContextMeasurement,
  ContextPlan,
  ContextReceiptLink,
  ContextSource,
  ContextView,
  EngineStatus,
  FailureCode,
  MAX_MEASUREMENTS,
  MAX_PATH_BYTES,
  MAX_REFS,
  MAX_REF_BYTES,
  MAX_REQUEST_BYTES,
  MAX_RESPONSE_BYTES,
  MAX_STDERR_BYTES,
  MAX_TEXT_BYTES,
  RecoveredSource,
  SCHEMA_VERSION,
  strictJsonLoads,
  TRANSPORT_VERSION,
  ENGINE_INTERFACE_VERSION,
  validateDigest,
  validateOutputRef,
  validateRef,
} from "./protocol.js";

export interface EngineClient {
  contextView(plan: ContextPlan): Promise<ContextView> | ContextView;
  recover(
    projectRoot: string,
    path: string,
    recoveryRef: string,
    sourceRef: string,
    sourceDigest: string,
  ): Promise<RecoveredSource> | RecoveredSource;
}

export type ParsedResponse = Readonly<{
  view: Readonly<{ text: string; outputRef: string | null; outputDigest: string | null }>;
  records: Readonly<{
    invocation: Record<string, unknown>;
    observation: Record<string, unknown>;
  }> | null;
  recovery: Readonly<{ recoveryRef: string; sourceRef: string; sourceDigest: string }>;
}>;

const TOP_LEVEL_KEYS = new Set(["schema_version", "transport_version", "engine_interface_version", "view", "invocation", "observation", "recovery"]);
const VIEW_KEYS = new Set(["text", "output_ref", "output_digest"]);
const RECOVERY_KEYS = new Set(["recovery_ref", "source_ref", "source_digest"]);
const INVOCATION_KEYS = new Set(["schema_version", "invocation_id", "engine", "operation", "input_ref", "input_digest", "source_refs", "policy_admission"]);
const ENGINE_KEYS = new Set(["engine_id", "engine_version"]);
const OPERATION_KEYS = new Set(["capability_id", "capability_version"]);
const POLICY_KEYS = new Set(["policy_ref", "decision"]);
const OBSERVATION_KEYS = new Set(["schema_version", "invocation_id", "status", "output_ref", "output_digest", "source_lineage", "measurements", "failure", "receipt_link"]);
const OBSERVATION_REQUIRED_KEYS = new Set(["schema_version", "invocation_id", "status", "source_lineage", "measurements"]);
const MEASUREMENT_KEYS = new Set(["name", "unit", "classification", "value"]);
const FAILURE_KEYS = new Set(["code", "retryable_by_host", "recovery_ref"]);
const RECEIPT_LINK_KEYS = new Set(["schema_version", "receipt_id", "receipt_ref", "receipt_digest", "invocation_id"]);
const FAILURE_CODES = new Set(Object.values(FailureCode));
const SEMVER_RE = /^[0-9]+\.[0-9]+\.[0-9]+$/;

function protocol(message: string, cause?: unknown): EngineProtocolError {
  return new EngineProtocolError(message, cause === undefined ? undefined : { cause });
}

function integer(value: unknown, fieldName: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) throw protocol(`${fieldName} must be an integer`);
  return value;
}

function stringValue(value: unknown, fieldName: string, maximum = MAX_REF_BYTES): string {
  if (typeof value !== "string" || Buffer.byteLength(value, "utf8") > maximum || value.includes("\0")) throw protocol(`${fieldName} violates its bound`);
  return value;
}

function optionalDigest(value: unknown, fieldName: string): string | null {
  if (value === null) return null;
  try { return validateDigest(value, fieldName); } catch (error) { throw protocol(String(error)); }
}

function optionalOutputRef(value: unknown, fieldName: string): string | null {
  if (value === null) return null;
  try { return validateOutputRef(value, fieldName); } catch (error) { throw protocol(String(error)); }
}

function requiredRef(value: unknown, fieldName: string): string {
  try { return validateRef(value, fieldName); } catch (error) { throw protocol(String(error)); }
}

function validatePair(outputRef: string | null, outputDigest: string | null, label: string): void {
  if (outputRef !== null && outputDigest === null) throw protocol(`${label} output reference requires a digest`);
  if (outputRef !== null && outputDigest !== null && outputRef !== `output:${outputDigest.slice("sha256:".length)}`) throw protocol(`${label} output reference does not match digest`);
}

function parseMeasurement(value: unknown): ContextMeasurement {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw protocol("measurement must be an object");
  try {
    const item = value as Record<string, unknown>;
    if (Object.keys(item).length !== MEASUREMENT_KEYS.size || Object.keys(item).some((key) => !MEASUREMENT_KEYS.has(key))) throw new ValidationError("measurement fields do not match the v1 contract");
    return new ContextMeasurement(item.name as string, item.unit as string, item.classification as string, item.value as number | null);
  } catch (error) { throw protocol(String(error)); }
}

function parseFailure(value: unknown): ContextFailure | null {
  if (value === null) return null;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw protocol("failure must be an object or null");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== FAILURE_KEYS.size || Object.keys(item).some((key) => !FAILURE_KEYS.has(key))) throw protocol("failure fields do not match the v1 contract");
  if (typeof item.code !== "string" || !FAILURE_CODES.has(item.code as FailureCode)) throw protocol("unknown Engine failure code");
  if (typeof item.retryable_by_host !== "boolean") throw protocol("failure.retryable_by_host must be boolean");
  const recoveryRef = item.recovery_ref === null ? null : requiredRef(item.recovery_ref, "failure.recovery_ref");
  try { return new ContextFailure(item.code, item.retryable_by_host, recoveryRef); } catch (error) { throw protocol(String(error)); }
}

function parseReceiptLink(value: unknown, invocationId: string): ContextReceiptLink | null {
  if (value === null) return null;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw protocol("receipt_link must be an object or null");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== RECEIPT_LINK_KEYS.size || Object.keys(item).some((key) => !RECEIPT_LINK_KEYS.has(key))) throw protocol("receipt_link fields do not match the v1 contract");
  const digest = optionalDigest(item.receipt_digest, "receipt_link.receipt_digest");
  if (digest === null) throw protocol("receipt_link.receipt_digest is required");
  const ref = requiredRef(item.receipt_ref, "receipt_link.receipt_ref");
  if (ref !== `receipt:${digest}`) throw protocol("receipt_link.receipt_ref does not match digest");
  if (item.invocation_id !== invocationId) throw protocol("receipt_link invocation binding mismatch");
  try { return new ContextReceiptLink(integer(item.schema_version, "receipt_link.schema_version"), requiredRef(item.receipt_id, "receipt_link.receipt_id"), ref, digest, stringValue(item.invocation_id, "receipt_link.invocation_id")); } catch (error) { throw protocol(String(error)); }
}

function parseInvocation(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw protocol("invocation must be an object");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== INVOCATION_KEYS.size || Object.keys(item).some((key) => !INVOCATION_KEYS.has(key))) throw protocol("invocation fields do not match the v1 contract");
  if (!item.engine || typeof item.engine !== "object" || Array.isArray(item.engine)) throw protocol("invocation.engine must be an object");
  if (!item.operation || typeof item.operation !== "object" || Array.isArray(item.operation)) throw protocol("invocation.operation must be an object");
  if (!item.policy_admission || typeof item.policy_admission !== "object" || Array.isArray(item.policy_admission)) throw protocol("invocation.policy_admission must be an object");
  const engine = item.engine as Record<string, unknown>;
  const operation = item.operation as Record<string, unknown>;
  const policy = item.policy_admission as Record<string, unknown>;
  for (const [record, keys, label] of [[engine, ENGINE_KEYS, "invocation.engine"], [operation, OPERATION_KEYS, "invocation.operation"], [policy, POLICY_KEYS, "invocation.policy_admission"]] as const) {
    if (Object.keys(record).length !== keys.size || Object.keys(record).some((key) => !keys.has(key))) throw protocol(`${label} fields do not match the v1 contract`);
  }
  const engineId = stringValue(engine.engine_id, "invocation.engine.engine_id");
  const engineVersion = stringValue(engine.engine_version, "invocation.engine.engine_version");
  if (engineId !== "lean-ctx-local" || !SEMVER_RE.test(engineVersion) || Number.parseInt(engineVersion, 10) !== 3) throw new UnsupportedEngineError("unsupported Engine identity");
  const capabilityId = stringValue(operation.capability_id, "invocation.operation.capability_id");
  const capabilityVersion = stringValue(operation.capability_version, "invocation.operation.capability_version");
  if (capabilityId !== "capability://leanctx/context-optimization" || capabilityVersion !== "1.0.0") throw new UnsupportedEngineError("unsupported Engine capability");
  const decision = policy.decision;
  if (decision !== "admitted" && decision !== "rejected") throw protocol("unknown policy decision");
  const invocationId = stringValue(item.invocation_id, "invocation.invocation_id");
  const inputRef = requiredRef(item.input_ref, "invocation.input_ref");
  const inputDigest = validateDigest(item.input_digest, "invocation.input_digest");
  if (!Array.isArray(item.source_refs) || item.source_refs.length === 0 || item.source_refs.length > MAX_REFS) throw protocol("invocation.source_refs exceeds its bound");
  const sourceRefs = item.source_refs.map((entry) => requiredRef(entry, "invocation.source_refs"));
  if (new Set(sourceRefs).size !== sourceRefs.length) throw protocol("invocation.source_refs contains duplicates");
  if (!sourceRefs.includes(inputRef)) throw protocol("invocation input_ref is not in source_refs");
  return {
    schema_version: SCHEMA_VERSION,
    invocation_id: invocationId,
    engine: { engine_id: engineId, engine_version: engineVersion },
    operation: { capability_id: capabilityId, capability_version: capabilityVersion },
    input_ref: inputRef,
    input_digest: inputDigest,
    source_refs: sourceRefs,
    policy_admission: { policy_ref: requiredRef(policy.policy_ref, "invocation.policy_admission.policy_ref"), decision },
  };
}

function parseObservation(value: unknown, invocationId: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw protocol("observation must be an object");
  const item = value as Record<string, unknown>;
  const keys = Object.keys(item);
  if (keys.some((key) => !OBSERVATION_KEYS.has(key)) || ![...OBSERVATION_REQUIRED_KEYS].every((key) => key in item)) throw protocol("observation fields do not match the v1 contract");
  const observedId = stringValue(item.invocation_id, "observation.invocation_id");
  if (observedId !== invocationId) throw protocol("observation invocation binding mismatch");
  if (integer(item.schema_version, "observation.schema_version") !== SCHEMA_VERSION) throw protocol("unsupported observation schema version");
  if (!Object.values(EngineStatus).includes(item.status as EngineStatus)) throw protocol("unknown observation status");
  const status = item.status as string;
  const outputRef = optionalOutputRef(item.output_ref === undefined ? null : item.output_ref, "observation.output_ref");
  const outputDigest = optionalDigest(item.output_digest === undefined ? null : item.output_digest, "observation.output_digest");
  validatePair(outputRef, outputDigest, "observation");
  if (!Array.isArray(item.source_lineage) || item.source_lineage.length === 0 || item.source_lineage.length > MAX_REFS) throw protocol("observation.source_lineage exceeds its bound");
  const lineage = item.source_lineage.map((entry) => requiredRef(entry, "observation.source_lineage"));
  if (new Set(lineage).size !== lineage.length) throw protocol("observation.source_lineage contains duplicates");
  if (!Array.isArray(item.measurements) || item.measurements.length > MAX_MEASUREMENTS) throw protocol("observation.measurements exceeds its bound");
  const measurements = item.measurements.map(parseMeasurement);
  const failure = parseFailure(item.failure === undefined ? null : item.failure);
  const receiptLink = parseReceiptLink(item.receipt_link === undefined ? null : item.receipt_link, invocationId);
  if ((status === "succeeded" || status === "degraded") && failure !== null) throw protocol("successful/degraded observation cannot contain failure");
  if ((status === "failed" || status === "rejected") && failure === null) throw protocol("failed/rejected observation requires failure");
  if (status === "succeeded" && receiptLink === null) throw protocol("succeeded observation requires receipt_link");
  return { schema_version: SCHEMA_VERSION, invocation_id: observedId, status, output_ref: outputRef, output_digest: outputDigest, source_lineage: lineage, measurements, failure, receipt_link: receiptLink };
}

function parseView(value: unknown): ParsedResponse["view"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw protocol("view must be an object");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== VIEW_KEYS.size || Object.keys(item).some((key) => !VIEW_KEYS.has(key))) throw protocol("view fields do not match the v1 contract");
  const viewText = stringValue(item.text, "view.text", MAX_TEXT_BYTES);
  const outputRef = optionalOutputRef(item.output_ref, "view.output_ref");
  const outputDigest = optionalDigest(item.output_digest, "view.output_digest");
  validatePair(outputRef, outputDigest, "view");
  if (outputDigest !== null && sha256Digest(viewText) !== outputDigest) throw protocol("view output digest mismatch");
  return { text: viewText, outputRef, outputDigest };
}

function parseRecovery(value: unknown): ParsedResponse["recovery"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw protocol("recovery must be an object");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== RECOVERY_KEYS.size || Object.keys(item).some((key) => !RECOVERY_KEYS.has(key))) throw protocol("recovery fields do not match the v1 contract");
  const digest = optionalDigest(item.source_digest, "recovery.source_digest");
  if (digest === null) throw protocol("recovery.source_digest is required");
  return { recoveryRef: requiredRef(item.recovery_ref, "recovery.recovery_ref"), sourceRef: requiredRef(item.source_ref, "recovery.source_ref"), sourceDigest: digest };
}

export function parseResponse(raw: Uint8Array | string): ParsedResponse {
  const bytes = typeof raw === "string" ? Buffer.from(raw, "utf8") : Buffer.from(raw);
  if (bytes.byteLength > MAX_RESPONSE_BYTES) throw protocol("Engine response exceeds the bound");
  let decoded: unknown;
  try { decoded = strictJsonLoads(bytes, "Engine response"); } catch (error) { throw protocol(String(error)); }
  if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) throw protocol("Engine response must be an object");
  const item = decoded as Record<string, unknown>;
  if (Object.keys(item).length !== TOP_LEVEL_KEYS.size || Object.keys(item).some((key) => !TOP_LEVEL_KEYS.has(key))) throw protocol("Engine response fields do not match the v1 contract");
  if (integer(item.schema_version, "response.schema_version") !== SCHEMA_VERSION) throw new CompatibilityError("unsupported schema version");
  if (integer(item.transport_version, "response.transport_version") !== TRANSPORT_VERSION) throw new CompatibilityError("unsupported transport version");
  if (item.engine_interface_version !== ENGINE_INTERFACE_VERSION) throw new CompatibilityError("unsupported Engine Interface version");
  const view = parseView(item.view);
  const recovery = parseRecovery(item.recovery);
  if (item.invocation === null || item.observation === null) {
    if (item.invocation !== null || item.observation !== null) throw protocol("invocation and observation must both be null or present");
    return { view, records: null, recovery };
  }
  const invocation = parseInvocation(item.invocation);
  const observation = parseObservation(item.observation, invocation.invocation_id as string);
  const lineage = observation.source_lineage as string[];
  const sourceRefs = invocation.source_refs as string[];
  if (lineage.length !== sourceRefs.length || lineage.some((entry, index) => entry !== sourceRefs[index])) throw protocol("observation source lineage does not match invocation");
  if (observation.output_ref !== view.outputRef || observation.output_digest !== view.outputDigest) throw protocol("view and observation output binding mismatch");
  return { view, records: { invocation, observation }, recovery };
}

export function safeRelativePath(pathValue: string): string {
  if (typeof pathValue !== "string" || !pathValue || Buffer.byteLength(pathValue, "utf8") > MAX_PATH_BYTES || pathValue.includes("\0") || isAbsolute(pathValue) || [...pathValue].some((char) => char.charCodeAt(0) < 0x20)) throw new EngineProtocolError("path must be a rooted relative path");
  const normalized = normalize(pathValue).split(sep).join("/");
  if (normalized === "." || normalized === ".." || normalized.startsWith("../")) throw new EngineProtocolError("path escapes project root");
  return normalized;
}

function sha256Digest(data: Uint8Array | string): string {
  return `sha256:${createHash("sha256").update(data).digest("hex")}`;
}

function failureFromView(observation: Record<string, unknown>): ContextFailure | null {
  return observation.failure instanceof ContextFailure ? observation.failure : null;
}

export type SubprocessEngineClientOptions = Readonly<{
  engineBinary?: string;
  timeout?: number;
}>;

export class SubprocessEngineClient implements EngineClient {
  readonly engineBinary: string;
  readonly timeout: number;

  constructor(options: SubprocessEngineClientOptions = {}) {
    const timeout = options.timeout ?? 30;
    if (typeof timeout !== "number" || !Number.isFinite(timeout) || timeout < 0.1 || timeout > 120) throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
    this.engineBinary = options.engineBinary ?? "lean-ctx";
    this.timeout = timeout;
  }

  async contextView(plan: ContextPlan): Promise<ContextView> {
    if (!(plan instanceof ContextPlan)) throw new ValidationError("context_view requires ContextPlan");
    const source = plan.source;
    const request = { schema_version: SCHEMA_VERSION, transport_version: TRANSPORT_VERSION, engine_interface_version: ENGINE_INTERFACE_VERSION, path: source.relativePath, mode: plan.mode };
    const response = await this.invoke("context-view", source.projectRoot, request);
    if (response.records === null) throw protocol("context-view response omitted invocation/observation");
    const { invocation } = response.records;
    if (!(invocation.source_refs as string[]).includes(response.recovery.sourceRef)) throw protocol("recovery source_ref is not admitted by invocation");
    if (source.sourceRef !== undefined && source.sourceRef !== response.recovery.sourceRef) throw protocol("Engine source_ref differs from requested binding");
    if (source.sourceDigest !== undefined && source.sourceDigest !== response.recovery.sourceDigest) throw protocol("Engine source_digest differs from requested binding");
    const result = this.buildView(source, response);
    if (result.status === EngineStatus.REJECTED) {
      const failure = result.failure;
      const options = failure === null ? { view: result } : { failure, view: result };
      if (failure?.code === FailureCode.POLICY_REJECTED) throw new PolicyAdmissionError("Engine rejected request: policy_rejected", options);
      if (failure?.code === FailureCode.SOURCE_UNAVAILABLE) throw new SourceUnavailableError("Engine rejected request: source_unavailable", options);
      throw new EngineRejected(`Engine rejected request: ${failure?.code ?? "rejected"}`, options);
    }
    if (result.status === EngineStatus.FAILED) {
      const failure = result.failure;
      if (failure?.code === FailureCode.UNSUPPORTED_OPERATION) throw new UnsupportedEngineError("Engine execution failed: unsupported_operation");
      const options = failure === null ? { view: result } : { failure, view: result };
      if (failure?.code === FailureCode.SOURCE_INTEGRITY_MISMATCH) throw new ArtifactIntegrityError("Engine execution failed: source_integrity_mismatch", options);
      if (failure?.code === FailureCode.SOURCE_UNAVAILABLE) throw new SourceUnavailableError("Engine execution failed: source_unavailable", options);
      throw new EngineExecutionError(`Engine execution failed: ${failure?.code ?? "failed"}`, options);
    }
    return result;
  }

  async recover(projectRoot: string, pathValue: string, recoveryRef: string, sourceRef: string, sourceDigest: string): Promise<RecoveredSource> {
    const root = this.validateRoot(projectRoot);
    const path = safeRelativePath(pathValue);
    const checkedRecovery = requiredRef(recoveryRef, "recovery_ref");
    const checkedSource = requiredRef(sourceRef, "source_ref");
    let checkedDigest: string;
    try { checkedDigest = validateDigest(sourceDigest, "source_digest"); } catch (error) { throw protocol(String(error)); }
    const request = { schema_version: SCHEMA_VERSION, transport_version: TRANSPORT_VERSION, engine_interface_version: ENGINE_INTERFACE_VERSION, path, recovery_ref: checkedRecovery, source_ref: checkedSource, source_digest: checkedDigest };
    const response = await this.invoke("recover", root, request);
    if (response.records !== null) throw protocol("recover response must have null invocation/observation");
    if (response.recovery.recoveryRef !== checkedRecovery || response.recovery.sourceRef !== checkedSource || response.recovery.sourceDigest !== checkedDigest) throw new ArtifactIntegrityError("recover response binding mismatch");
    if (response.view.outputDigest !== checkedDigest) throw new ArtifactIntegrityError("recover output digest does not match source digest");
    const expectedRef = `output:${checkedDigest.slice("sha256:".length)}`;
    if (response.view.outputRef !== null && response.view.outputRef !== expectedRef) throw new ArtifactIntegrityError("recover output reference does not match source digest");
    try { return new RecoveredSource(response.view.text, checkedSource, checkedDigest, checkedRecovery); } catch (error) { throw protocol(String(error)); }
  }

  private validateRoot(projectRoot: string): string {
    if (typeof projectRoot !== "string" || !projectRoot || projectRoot.includes("\0") || Buffer.byteLength(projectRoot, "utf8") > MAX_PATH_BYTES) throw new SourceUnavailableError("project_root is unavailable");
    const root = resolve(projectRoot);
    try { if (!statSync(root).isDirectory()) throw new Error(); } catch { throw new SourceUnavailableError("project_root is unavailable"); }
    return root;
  }

  private resolveBinary(): string {
    let candidate = this.engineBinary;
    if (!isAbsolute(candidate) && !candidate.includes("/") && !candidate.includes("\\")) {
      const searchPath = (process.env.PATH?.split(delimiter) ?? []).filter((entry) => entry.length > 0);
      const resolved = searchPath.map((entry) => resolve(entry, candidate)).find((entry) => { try { return statSync(entry).isFile(); } catch { return false; } });
      if (resolved === undefined) throw new EngineUnavailable("configured Engine binary is unavailable");
      candidate = resolved;
    } else candidate = resolve(candidate);
    try {
      if (!statSync(candidate).isFile()) throw new Error();
      accessSync(candidate, constants.X_OK);
    } catch { throw new EngineUnavailable("configured Engine binary is unavailable"); }
    return realpathSync(candidate);
  }

  private async invoke(operation: string, projectRoot: string, request: Record<string, unknown>): Promise<ParsedResponse> {
    const root = this.validateRoot(projectRoot);
    const payload = canonicalBytes(request);
    if (payload.byteLength > MAX_REQUEST_BYTES) throw new EngineProtocolError("Engine request exceeds the bound");
    let requestPath: string | undefined;
    try {
      const directory = await mkdtemp(resolve(root, ".leanctx-sdk-"));
      requestPath = resolve(directory, "request.json");
      const fd = openSync(requestPath, "wx", 0o600);
      try { fchmodSync(fd, 0o600); writeFileSync(fd, payload); fsyncSync(fd); } finally { closeSync(fd); }
      return parseResponse(await this.run(operation, root, requestPath));
    } finally {
      if (requestPath) {
        try { unlinkSync(requestPath); } catch { /* best effort */ }
        try { await rmdir(resolve(requestPath, "..")); } catch { /* best effort */ }
      }
    }
  }

  private async run(operation: string, projectRoot: string, requestPath: string): Promise<Buffer> {
    const binary = this.resolveBinary();
    const argv = [binary, "engine", operation, "--project-root", projectRoot, "--json-file", requestPath];
    const env = { LC_ALL: "C", LANG: "C", TZ: "UTC", PYTHONHASHSEED: "0" };
    let child: ChildProcess;
    try {
      child = spawn(binary, argv.slice(1), { cwd: projectRoot, env, shell: false, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"] });
    } catch (error) { throw new EngineUnavailable("Engine process could not be started", { cause: error }); }
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    return await new Promise<Buffer>((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        terminateProcess(child);
        rejectPromise(new EngineTimeout("Engine process exceeded its deadline"));
      }, this.timeout * 1000);
      child.stdout?.on("data", (chunk: Buffer) => {
        if (settled) return;
        stdoutBytes += chunk.byteLength;
        if (stdoutBytes > MAX_RESPONSE_BYTES) {
          settled = true;
          clearTimeout(timer);
          terminateProcess(child);
          rejectPromise(new EngineProtocolError("Engine process output exceeds its bound"));
        } else stdout.push(Buffer.from(chunk));
      });
      child.stderr?.on("data", (chunk: Buffer) => {
        if (settled) return;
        stderrBytes += chunk.byteLength;
        if (stderrBytes > MAX_STDERR_BYTES) {
          settled = true;
          clearTimeout(timer);
          terminateProcess(child);
          rejectPromise(new EngineProtocolError("Engine process output exceeds its bound"));
        } else stderr.push(Buffer.from(chunk));
      });
      child.once("error", (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        rejectPromise(new EngineUnavailable("Engine process could not be started", { cause: error }));
      });
      child.once("close", (code) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (code !== 0) {
          const failureCode = stderrCode(Buffer.concat(stderr));
          if (["unsafe_root", "source_outside_root", "source_symlink", "policy_rejected"].includes(failureCode ?? "")) {
            rejectPromise(new PolicyAdmissionError(`Engine rejected request: ${failureCode}`));
          } else if (failureCode === "source_unavailable") {
            rejectPromise(new SourceUnavailableError("Engine source is unavailable"));
          } else if (failureCode === "unsupported_mode") {
            rejectPromise(new UnsupportedEngineError("Engine operation is unsupported"));
          } else {
            rejectPromise(new EngineExecutionError(`Engine process failed: ${failureCode ?? "nonzero_exit"}`));
          }
          return;
        }
        resolvePromise(Buffer.concat(stdout));
      });
    });
  }

  private buildView(source: ContextSource, parsed: ParsedResponse): ContextView {
    if (parsed.records === null) throw protocol("context-view response omitted invocation/observation");
    const { invocation, observation } = parsed.records;
    return new ContextView({
      source,
      text: parsed.view.text,
      outputRef: parsed.view.outputRef,
      outputDigest: parsed.view.outputDigest,
      sourceRef: parsed.recovery.sourceRef,
      sourceDigest: parsed.recovery.sourceDigest,
      recoveryRef: parsed.recovery.recoveryRef,
      status: observation.status as string,
      measurements: observation.measurements as ContextMeasurement[],
      failure: observation.failure as ContextFailure | null,
      receiptLink: observation.receipt_link as ContextReceiptLink | null,
      invocation,
      observation,
    });
  }
}

function stderrCode(stderr: Buffer): string | null {
  const match = /(?:^|\n)engine:\s*([a-z0-9_]+)/.exec(stderr.toString("utf8"));
  return match?.[1] ?? null;
}

function terminateProcess(child: ChildProcess): void {
  const pid = child.pid;
  if (!pid) { try { child.kill("SIGKILL"); } catch { /* already gone */ } return; }
  try {
    if (process.platform === "win32") child.kill();
    else process.kill(-pid, "SIGKILL");
  } catch {
    try { child.kill("SIGKILL"); } catch { /* already gone */ }
  }
}

export const _parse_response = parseResponse;
