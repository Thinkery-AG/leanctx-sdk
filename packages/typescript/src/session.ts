import { randomUUID } from "node:crypto";
import {
  ArtifactIntegrityError,
  EngineError,
  EngineExecutionError,
  EngineProtocolError,
  EngineRejected,
  EngineTimeout,
  EngineUnavailable,
  RecoveryUnavailableError,
  SessionStateError,
  ValidationError,
} from "./errors.js";
import { SubprocessEngineClient, type EngineClient } from "./engine.js";
import {
  canonicalBytes,
  ContextPlan,
  ContextSource,
  ContextView,
  HostOutcome,
  Integrity,
  RecoveredSource,
  SessionState,
} from "./protocol.js";
import { ContextReceipt } from "./receipt.js";

export type ContextSessionOptions = Readonly<{
  projectRoot?: string;
  sessionId?: string;
  taskId?: string;
  failOpen?: boolean;
  engine?: EngineClient;
}>;

function runtimeId(prefix: string): string { return `${prefix}-${randomUUID().replaceAll("-", "")}`; }

export class ContextSession {
  readonly task: string;
  readonly projectRoot: string | undefined;
  readonly sessionId: string;
  readonly taskId: string;
  readonly failOpen: boolean;
  readonly engine: EngineClient;
  private stateValue: SessionState = SessionState.CREATED;
  private planValue: ContextPlan | null = null;
  private viewValue: ContextView | null = null;
  private receiptValue: ContextReceipt | null = null;
  private prepared = false;
  private degradationsValue: string[] = [];

  constructor(task: string, options: ContextSessionOptions = {}) {
    if (typeof task !== "string" || Buffer.byteLength(task, "utf8") === 0 || Buffer.byteLength(task, "utf8") > 16 * 1024 || task.includes("\0")) throw new ValidationError("task must be a bounded string");
    if (typeof options.failOpen !== "undefined" && typeof options.failOpen !== "boolean") throw new ValidationError("fail_open must be boolean");
    this.task = task;
    this.projectRoot = options.projectRoot;
    this.sessionId = options.sessionId ?? runtimeId("session");
    this.taskId = options.taskId ?? runtimeId("task");
    this.failOpen = options.failOpen ?? false;
    this.engine = options.engine ?? new SubprocessEngineClient();
  }

  get state(): SessionState { return this.stateValue; }
  get project_root(): string | undefined { return this.projectRoot; }
  get session_id(): string { return this.sessionId; }
  get task_id(): string { return this.taskId; }
  get current_plan(): ContextPlan | null { return this.currentPlan; }
  get currentPlan(): ContextPlan | null { return this.planValue; }
  get view(): ContextView | null { return this.viewValue; }
  get receipt(): ContextReceipt | null { return this.receiptValue; }
  get degradations(): readonly string[] { return Object.freeze([...this.degradationsValue]); }

  plan(source: ContextSource, options: { mode?: string; freshness?: string } = {}): ContextPlan {
    this.ensureNotTerminal();
    const candidate = new ContextPlan(this.sessionId, this.taskId, this.task, source, options);
    if (this.planValue !== null) {
      if (this.planValue.planId !== candidate.planId) throw new SessionStateError("a session cannot replace its Product intent");
      return this.planValue;
    }
    this.planValue = candidate;
    this.stateValue = SessionState.PLANNED;
    return candidate;
  }

  planFor(source: ContextSource, options: { mode?: string; freshness?: string } = {}): ContextPlan { return this.plan(source, options); }
  planIntent(source: ContextSource, options: { mode?: string; freshness?: string } = {}): ContextPlan { return this.plan(source, options); }

  async prepare(source?: ContextSource, options: { mode?: string; freshness?: string } = {}): Promise<ContextView | null> {
    if (this.stateValue === SessionState.COMPLETED || this.stateValue === SessionState.ABORTED || this.stateValue === SessionState.CLOSED) throw new SessionStateError("prepare is not legal after terminal completion");
    if (this.prepared) return this.viewValue;
    if (this.planValue === null) {
      if (source === undefined) throw new SessionStateError("prepare requires a source before planning");
      this.plan(source, options);
    } else if (source !== undefined) this.plan(source, options);
    if (this.planValue === null) throw new SessionStateError("prepare could not establish a plan");
    this.stateValue = SessionState.EXECUTING;
    try {
      this.viewValue = await this.engine.contextView(this.planValue);
      this.prepared = true;
      if (this.viewValue.status === "degraded") this.addDegradation("engine:degraded");
      return this.viewValue;
    } catch (error) {
      if ((error instanceof EngineUnavailable || error instanceof EngineTimeout) && this.failOpen) {
        this.addDegradation(`engine:${error.code}`);
        this.prepared = true;
        this.viewValue = null;
        return null;
      }
      if (error instanceof EngineError) this.abortEngineFailure(error);
      else this.abortEngineFailure(error);
      throw error;
    }
  }

  complete(hostResult: unknown = null, options: { outcome?: HostOutcome | string; usage?: Record<string, unknown> | null } = {}): ContextReceipt {
    const outcome = options.outcome ?? HostOutcome.UNKNOWN;
    if (this.stateValue === SessionState.COMPLETED) {
      if (this.receiptValue === null || !this.sameCompletion(this.receiptValue, outcome, options.usage)) throw new SessionStateError("conflicting repeated complete");
      return this.receiptValue;
    }
    if (this.stateValue === SessionState.ABORTED || this.stateValue === SessionState.CLOSED) throw new SessionStateError("complete is not legal after abort/close");
    if (this.stateValue !== SessionState.EXECUTING) throw new SessionStateError("complete requires an executing session");
    if (![HostOutcome.UNKNOWN, HostOutcome.ACCEPTED, HostOutcome.REJECTED, HostOutcome.COMPLETED, HostOutcome.FAILED].includes(outcome as HostOutcome)) throw new ValidationError("complete outcome must be an explicit non-aborted host outcome");
    this.receiptValue = this.makeReceipt(outcome, hostResult, options.usage, null, null);
    this.stateValue = SessionState.COMPLETED;
    return this.receiptValue;
  }

  abort(error: unknown): ContextReceipt {
    if (!(error instanceof Error)) throw new ValidationError("abort requires an Error");
    if (this.stateValue === SessionState.ABORTED) {
      if (!this.receiptValue) throw new SessionStateError("aborted session has no receipt");
      return this.receiptValue;
    }
    if (this.stateValue === SessionState.CLOSED) {
      if (this.receiptValue?.outcome === HostOutcome.ABORTED) return this.receiptValue;
      throw new SessionStateError("closed session has no abort receipt");
    }
    if (this.stateValue === SessionState.COMPLETED) throw new SessionStateError("cannot abort a completed session");
    const typeName = `${error.constructor.name}`;
    this.receiptValue = this.makeReceipt(HostOutcome.ABORTED, null, null, typeName, error);
    this.stateValue = SessionState.ABORTED;
    return this.receiptValue;
  }

  async recover(view?: ContextView): Promise<RecoveredSource> {
    if (![SessionState.EXECUTING, SessionState.COMPLETED, SessionState.ABORTED].includes(this.stateValue)) throw new SessionStateError("recover requires an executing or terminal session");
    const selected = view ?? this.viewValue;
    if (!selected || !this.planValue) throw new RecoveryUnavailableError("no validated view is available for recovery");
    if (selected !== this.viewValue) {
      if (!this.viewValue || JSON.stringify(selected.recoveryBinding()) !== JSON.stringify(this.viewValue.recoveryBinding())) throw new RecoveryUnavailableError("recovery view is not bound to this session");
    }
    if (!selected.recoveryRef) throw new RecoveryUnavailableError("view has no recovery binding");
    const result = await this.engine.recover(this.planValue.source.projectRoot, this.planValue.source.relativePath, selected.recoveryRef, selected.sourceRef, selected.sourceDigest);
    if (!(result instanceof RecoveredSource)) throw new ArtifactIntegrityError("Engine client returned an invalid recovery value");
    if (result.recoveryRef !== selected.recoveryRef || result.sourceRef !== selected.sourceRef || result.sourceDigest !== selected.sourceDigest) throw new ArtifactIntegrityError("recovery binding differs from the validated view");
    return result;
  }

  close(): void {
    if (this.stateValue === SessionState.CLOSED) return;
    if (this.stateValue !== SessionState.COMPLETED && this.stateValue !== SessionState.ABORTED) throw new SessionStateError("close requires a terminal receipt");
    this.stateValue = SessionState.CLOSED;
  }

  private ensureNotTerminal(): void {
    if ([SessionState.COMPLETED, SessionState.ABORTED, SessionState.CLOSED].includes(this.stateValue)) throw new SessionStateError("planning is not legal after terminal completion");
  }
  private addDegradation(value: string): void { if (!this.degradationsValue.includes(value)) this.degradationsValue.push(value); }
  private abortEngineFailure(error: unknown): void {
    if (error instanceof EngineExecutionError && error.view instanceof ContextView) this.viewValue = error.view;
    const code = error instanceof EngineError ? error.code : "engine_error";
    this.addDegradation(`engine:${code}`);
    this.receiptValue = this.makeReceipt(HostOutcome.ABORTED, null, null, null, null);
    this.stateValue = SessionState.ABORTED;
  }
  private makeReceipt(outcome: string, hostResult: unknown, usage: Record<string, unknown> | null | undefined, hostExceptionType: string | null, hostException: unknown): ContextReceipt {
    const integrity = this.viewValue?.integrityStatus ?? Integrity.UNSEALED;
    return new ContextReceipt({ sessionId: this.sessionId, taskId: this.taskId, planId: this.planValue?.planId ?? null, view: this.viewValue, outcome, integrityStatus: integrity, degradations: this.degradationsValue, usage: usage ?? null, hostExceptionType, hostResult, hostException });
  }
  private sameCompletion(receipt: ContextReceipt, outcome: string, usage: Record<string, unknown> | null | undefined): boolean {
    try { return receipt.outcome === outcome && JSON.stringify(canonicalBytes(receipt.usage)) === JSON.stringify(canonicalBytes(usage ?? null)); } catch { return false; }
  }
}
