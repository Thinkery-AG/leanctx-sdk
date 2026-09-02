import {
  ArtifactIntegrityError,
  ValidationError,
} from "./errors.js";
import {
  canonicalBytes,
  ContextSource,
  ContextView,
  HostOutcome,
  Integrity,
  JsonValue,
  planRef,
  SCHEMA_VERSION,
} from "./protocol.js";

export type ContextReceiptOptions = Readonly<{
  sessionId: string;
  taskId: string;
  planId: string | null;
  view: ContextView | null;
  outcome: HostOutcome | string;
  integrityStatus: Integrity | string;
  degradations?: readonly string[];
  usage?: Record<string, unknown> | null;
  hostExceptionType?: string | null;
  hostResult?: unknown;
  hostException?: unknown;
  schemaVersion?: number;
}>;

function safeUsage(value: Record<string, unknown> | null | undefined): Readonly<Record<string, unknown>> | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) throw new ValidationError("usage must be a mapping");
  const copy = structuredClone(value);
  try { canonicalBytes(copy); } catch (error) { throw new ValidationError("usage must be deterministic JSON data", { cause: error }); }
  return deepFreeze(copy);
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === "object") {
    for (const nested of Object.values(value as Record<string, unknown>)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

export class ContextReceipt {
  readonly sessionId: string;
  readonly taskId: string;
  readonly planId: string | null;
  readonly view: ContextView | null;
  readonly outcome: string;
  readonly integrityStatus: string;
  readonly degradations: readonly string[];
  readonly usage: Readonly<Record<string, unknown>> | null;
  readonly hostExceptionType: string | null;
  readonly hostResult: unknown;
  readonly hostException: unknown;
  readonly schemaVersion: number;

  constructor(options: ContextReceiptOptions);
  constructor(sessionId: string, taskId: string, planId: string | null, view: ContextView | null, outcome: string, integrityStatus: string, degradations?: readonly string[], usage?: Record<string, unknown> | null, hostExceptionType?: string | null, hostResult?: unknown, hostException?: unknown);
  constructor(first: ContextReceiptOptions | string, ...rest: unknown[]) {
    const options: ContextReceiptOptions = typeof first === "string"
      ? { sessionId: first, taskId: rest[0] as string, planId: rest[1] as string | null, view: rest[2] as ContextView | null, outcome: rest[3] as string, integrityStatus: rest[4] as string, degradations: rest[5] as readonly string[] | undefined, usage: rest[6] as Record<string, unknown> | null | undefined, hostExceptionType: rest[7] as string | null | undefined, hostResult: rest[8], hostException: rest[9] }
      : first;
    const sessionId = options.sessionId;
    const taskId = options.taskId;
    if (typeof sessionId !== "string" || !sessionId || Buffer.byteLength(sessionId) > 512) throw new ValidationError("session_id is invalid");
    if (typeof taskId !== "string" || !taskId || Buffer.byteLength(taskId) > 512) throw new ValidationError("task_id is invalid");
    if (options.planId !== null) planRef(options.planId);
    if (options.view !== null && !(options.view instanceof ContextView)) throw new ValidationError("receipt view must be ContextView");
    if (!Object.values(HostOutcome).includes(options.outcome as HostOutcome)) throw new ValidationError("invalid host outcome");
    if (!Object.values(Integrity).includes(options.integrityStatus as Integrity)) throw new ValidationError("invalid integrity status");
    if (options.hostExceptionType !== null && options.hostExceptionType !== undefined) {
      if (!options.hostExceptionType || options.hostExceptionType.includes(":") || options.hostExceptionType.includes("\n") || Buffer.byteLength(options.hostExceptionType) > 512) throw new ValidationError("host_exception_type must be a safe type name");
    }
    if (options.hostException !== undefined && options.hostException !== null && options.outcome !== HostOutcome.ABORTED) throw new ValidationError("host_exception requires an aborted outcome");
    if (options.hostException !== undefined && options.hostException !== null && options.hostExceptionType !== null && options.hostExceptionType !== undefined) {
      const expectedType = options.hostException instanceof Error ? options.hostException.constructor.name : null;
      if (expectedType !== null && options.hostExceptionType !== expectedType) throw new ValidationError("host_exception_type does not match host_exception");
    }
    const schemaVersion = options.schemaVersion ?? SCHEMA_VERSION;
    if (schemaVersion !== SCHEMA_VERSION || !Number.isInteger(schemaVersion)) throw new ValidationError("receipt schema_version must be 1");
    const degradations = [...(options.degradations ?? [])];
    if (degradations.some((item) => typeof item !== "string" || !item)) throw new ValidationError("degradations must be non-empty strings");
    if (options.integrityStatus === Integrity.SEALED && (options.view === null || !options.view.verify())) throw new ValidationError("sealed receipt requires verified Engine evidence");
    this.sessionId = sessionId;
    this.taskId = taskId;
    this.planId = options.planId;
    this.view = options.view;
    this.outcome = options.outcome;
    this.integrityStatus = options.integrityStatus;
    this.degradations = Object.freeze(degradations);
    this.usage = safeUsage(options.usage);
    this.hostExceptionType = options.hostExceptionType ?? null;
    this.hostResult = options.hostResult;
    this.hostException = options.hostException ?? null;
    this.schemaVersion = schemaVersion;
    Object.freeze(this);
  }

  get sealed(): boolean { return this.integrityStatus === Integrity.SEALED; }
  get session_id(): string { return this.sessionId; }
  get task_id(): string { return this.taskId; }
  get plan_id(): string | null { return this.planId; }
  get integrity_status(): string { return this.integrityStatus; }
  get host_exception_type(): string | null { return this.hostExceptionType; }
  get status(): string | null { return this.view?.status ?? null; }
  get source(): ContextSource | null { return this.view?.source ?? null; }
  get invocation(): Readonly<Record<string, unknown>> | null { return this.view?.invocation ?? null; }
  get observation(): Readonly<Record<string, unknown>> | null { return this.view?.observation ?? null; }
  get receiptLink() { return this.view?.receiptLink ?? null; }
  get recoveryRef(): string | null { return this.view?.recoveryRef ?? null; }
  get outputDigest(): string | null { return this.view?.outputDigest ?? null; }
  get exception(): unknown { return this.hostException; }
  verify(): boolean { return this.sealed && this.view !== null && this.view.verify(); }
  requireVerified(): void { if (!this.verify()) throw new ArtifactIntegrityError("receipt evidence is not sealed"); }
  require_verified(): void { this.requireVerified(); }

  toDict(): Record<string, JsonValue> {
    const viewProjection = this.view?.toDict();
    return {
      schema_version: this.schemaVersion,
      session_id: this.sessionId,
      task_id: this.taskId,
      plan_id: this.planId,
      outcome: this.outcome,
      integrity_status: this.integrityStatus,
      degradations: [...this.degradations],
      usage: this.usage === null ? null : structuredClone(this.usage) as JsonValue,
      host_exception_type: this.hostExceptionType,
      status: this.status,
      source: this.source?.toDict() ?? null,
      invocation: viewProjection?.invocation ?? null,
      observation: viewProjection?.observation ?? null,
      receipt_link: this.receiptLink?.toDict() ?? null,
      recovery_ref: this.recoveryRef,
      output_digest: this.outputDigest,
    };
  }
  to_dict(): Record<string, JsonValue> { return this.toDict(); }
}
