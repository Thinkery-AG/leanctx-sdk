/** Stable Agent Tools client for a host-owned agent loop. */

import { closeSync, constants, fchmodSync, fsyncSync, mkdtempSync, openSync, realpathSync, rmdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { spawn, spawnSync, type ChildProcessWithoutNullStreams } from "node:child_process";
import { delimiter, isAbsolute, relative, resolve, sep } from "node:path";
import {
  AgentPermissionError,
  ConfigurationError,
  InstanceCrashed,
  InstanceError,
  InstanceExecutionError,
  InstanceProtocolError,
  InstanceTimeout,
  InstanceUnavailable,
  UnsupportedCapabilityError,
  ValidationError,
} from "./errors.js";
import { canonicalBytes, strictJsonLoads } from "./protocol.js";
import { resolveInstanceBinary } from "./engine.js";

export const AGENT_TOOLS_INTERFACE_VERSION = "1.0.0" as const;
export const AGENT_TOOLS_SCHEMA_VERSION = 1 as const;
export const AGENT_TOOLS_TRANSPORT_VERSION = 1 as const;
// Paired with LeanCTX 3.12.1 (the matrix also admits 3.11.0, 3.11.3, 3.11.4, 3.11.5 and 3.12.0).
export const SUPPORTED_AGENT_TOOLS_ENGINE_VERSION = "3.12.1" as const;
// The (interface, schema, transport, engine) pairs a hello is accepted for: the
// rows of contracts/agent-tools-compatibility-v1.json, mirrored here and held
// equal to it by scripts/check_language_versions.py. A pair that is not a row
// is refused, whatever the Engine says about itself.
// agent-tools-compatibility: begin
const ADMITTED_ENGINE_PAIRS: ReadonlyArray<readonly [string, number, number, string]> = [
  ["1.0.0", 1, 1, "3.11.0"],
  ["1.0.0", 1, 1, "3.11.3"],
  ["1.0.0", 1, 1, "3.11.4"],
  ["1.0.0", 1, 1, "3.11.5"],
  ["1.0.0", 1, 1, "3.12.0"],
  ["1.0.0", 1, 1, "3.12.1"],
];
// agent-tools-compatibility: end
const HELLO_INTEGER_PATHS: readonly (readonly string[])[] = [["result", "schema_version"], ["result", "transport_version"]];
const MAX_REQUEST_BYTES = 1024 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const MAX_TEXT_BYTES = 8 * 1024 * 1024;
const PROCESS_CLEANUP_TIMEOUT_MS = 5_000;
// Shorter than the overall cleanup bound, so that the Engine itself is still
// killed inside that bound when the tree kill overruns.
const TASKKILL_TIMEOUT_MS = 3_000;

/**
 * Session containment (POSIX). "engine-starter": the process this SDK started is the
 * Engine's session starter. It runs the Engine in a process group of its own, ends that
 * group when the Engine exits, when this SDK ends the starter or when this process is
 * gone, and exits only afterwards. "unavailable": the Engine binary has no such mode
 * (or the platform is Windows); the SDK's own process-group handling is all there is.
 */
export type SessionContainment = "engine-starter" | "unavailable";
const SESSION_CONTAINMENT_ENV = "LEAN_CTX_SESSION_CONTAINMENT";
const SESSION_CONTAINMENT_ACK = Buffer.from('{"leanctx_session_containment":1}');

export enum ReadMode {
  AUTO = "auto",
  FULL = "full",
  RAW = "raw",
  SIGNATURES = "signatures",
  MAP = "map",
  DIFF = "diff",
  REFERENCE = "reference",
  TASK = "task",
  ANCHORED = "anchored",
}

export type AgentPermissionsOptions = Readonly<{ write?: boolean; execute?: boolean }>;
export class AgentPermissions {
  readonly write: boolean;
  readonly execute: boolean;
  constructor(options: AgentPermissionsOptions = {}) {
    if (typeof options.write !== "undefined" && typeof options.write !== "boolean") throw new ValidationError("AgentPermissions values must be boolean");
    if (typeof options.execute !== "undefined" && typeof options.execute !== "boolean") throw new ValidationError("AgentPermissions values must be boolean");
    this.write = options.write ?? false;
    this.execute = options.execute ?? false;
    Object.freeze(this);
  }
  get allowWrite(): boolean { return this.write; }
  get allowExecute(): boolean { return this.execute; }
  get allow_write(): boolean { return this.write; }
  get allow_exec(): boolean { return this.execute; }
}

const FORBIDDEN_ENV = new Set(["COMSPEC", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "HOME", "LD_LIBRARY_PATH", "LD_PRELOAD", "PATH", "PATHEXT", "PYTHONPATH", "RUSTC_WRAPPER", "SHELL"]);
export type ExecutionPolicyOptions = Readonly<{ maxTimeout?: number; allowedExecutables?: readonly string[]; allowedEnv?: readonly string[] }>;
export class ExecutionPolicy {
  readonly maxTimeout: number;
  readonly allowedExecutables: readonly string[];
  readonly allowedEnv: readonly string[];
  constructor(options: ExecutionPolicyOptions = {}) {
    const maxTimeout = options.maxTimeout ?? 30;
    if (typeof maxTimeout !== "number" || !Number.isFinite(maxTimeout) || maxTimeout < 0.1 || maxTimeout > 120) throw new ValidationError("max_timeout must be between 0.1 and 120 seconds");
    const executables = [...(options.allowedExecutables ?? [])];
    for (const executable of executables) if (typeof executable !== "string" || !/^[A-Za-z0-9._+-]+$/.test(executable) || executable.length === 0) throw new ValidationError("allowed_executables must contain executable basenames");
    const environment = [...(options.allowedEnv ?? [])];
    for (const name of environment) if (typeof name !== "string" || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || FORBIDDEN_ENV.has(name.toUpperCase())) throw new ValidationError("allowed_env must contain environment variable names");
    this.maxTimeout = maxTimeout;
    this.allowedExecutables = Object.freeze([...new Set(executables)].sort());
    this.allowedEnv = Object.freeze([...new Set(environment)].sort());
    Object.freeze(this);
  }
  get max_timeout(): number { return this.maxTimeout; }
  get allowed_executables(): readonly string[] { return this.allowedExecutables; }
  get allowed_env(): readonly string[] { return this.allowedEnv; }
}

export type ContentBlock = Readonly<Record<string, unknown>>;
export class ToolResult {
  readonly tool: string;
  readonly text: string;
  readonly contentBlocks: readonly ContentBlock[];
  readonly originalTokens: number;
  readonly outputTokens: number;
  readonly savedTokens: number;
  readonly mode: string | null;
  readonly changed: boolean;
  readonly shell: Readonly<Record<string, unknown>> | null;
  constructor(tool: string, value: { text: string; content_blocks: readonly Record<string, unknown>[]; original_tokens: number; output_tokens: number; saved_tokens: number; mode: string | null; changed: boolean; shell: Record<string, unknown> | null }) {
    this.tool = tool;
    this.text = value.text;
    this.contentBlocks = Object.freeze(value.content_blocks.map((item) => Object.freeze({ ...item })));
    this.originalTokens = value.original_tokens;
    this.outputTokens = value.output_tokens;
    this.savedTokens = value.saved_tokens;
    this.mode = value.mode;
    this.changed = value.changed;
    this.shell = value.shell === null ? null : Object.freeze({ ...value.shell });
    Object.freeze(this);
  }
  get savedRatio(): number { return this.originalTokens === 0 ? 0 : Math.min(this.savedTokens, this.originalTokens) / this.originalTokens; }
  get content_blocks(): readonly ContentBlock[] { return this.contentBlocks; }
  get original_tokens(): number { return this.originalTokens; }
  get output_tokens(): number { return this.outputTokens; }
  get saved_tokens(): number { return this.savedTokens; }
  get saved_ratio(): number { return this.savedRatio; }
}

export class AgentMetrics {
  readonly toolCalls: number;
  readonly originalTokens: number;
  readonly outputTokens: number;
  readonly savedTokens: number;
  constructor(toolCalls = 0, originalTokens = 0, outputTokens = 0, savedTokens = 0) { this.toolCalls = toolCalls; this.originalTokens = originalTokens; this.outputTokens = outputTokens; this.savedTokens = savedTokens; Object.freeze(this); }
  get savedRatio(): number { return this.originalTokens === 0 ? 0 : Math.min(this.savedTokens, this.originalTokens) / this.originalTokens; }
  get tool_calls(): number { return this.toolCalls; }
  get original_tokens(): number { return this.originalTokens; }
  get output_tokens(): number { return this.outputTokens; }
  get saved_tokens(): number { return this.savedTokens; }
  get saved_ratio(): number { return this.savedRatio; }
}

export type AgentContextOptions = Readonly<{
  task?: string;
  permissions?: AgentPermissions | AgentPermissionsOptions;
  executionPolicy?: ExecutionPolicy | ExecutionPolicyOptions;
  /** LeanCTX instance binary; defaults to `lean-ctx`. */
  instanceBinary?: string;
  /** @deprecated since 1.3.0, use instanceBinary */
  engineBinary?: string;
  timeout?: number;
  /**
   * Seconds the instance has for its greeting. Defaults to `timeout`; the host
   * sets it when starting an instance and waiting for one answer have
   * different budgets. Calls, reads, cancel and cleanup keep their own.
   */
  handshakeTimeout?: number;
  gitlabSource?: GitLabSource;
}>;

export type GitLabSource = Readonly<{
  host: string;
  project: number;
  namespace: string;
  glab: string;
  configDir?: string;
}>;

function normalizeGitLabSource(value: unknown): GitLabSource | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ValidationError("gitlabSource must be an object");
  const source = { ...(value as Record<string, unknown>) };
  const allowed = new Set(["host", "project", "namespace", "glab", "configDir"]);
  const required = ["host", "project", "namespace", "glab"];
  if (Object.keys(source).some((key) => !allowed.has(key)) || required.some((key) => !Object.prototype.hasOwnProperty.call(source, key))) throw new ValidationError("gitlabSource fields are invalid");
  const text = (item: unknown, name: string): string => {
    if (typeof item !== "string" || item.length === 0 || /[\u0000-\u001f\u007f-\u009f]/u.test(item)) throw new ValidationError(`GitLabSource ${name} must be a non-empty string without control characters`);
    return item;
  };
  const host = text(source.host, "host");
  const namespace = text(source.namespace, "namespace");
  const glab = text(source.glab, "glab");
  const project = source.project;
  if (typeof project !== "number" || !Number.isSafeInteger(project) || project < 1) throw new ValidationError("GitLabSource project must be a safe positive integer");
  if (!isAbsolute(glab)) throw new ValidationError("GitLabSource glab must be an absolute path");
  const configDir = source.configDir === undefined ? undefined : text(source.configDir, "configDir");
  if (configDir !== undefined && !isAbsolute(configDir)) throw new ValidationError("GitLabSource configDir must be an absolute path");
  const snapshot: { host: string; project: number; namespace: string; glab: string; configDir?: string } = { host, project, namespace, glab };
  if (configDir !== undefined) snapshot.configDir = configDir;
  return Object.freeze(snapshot);
}

function gitLabSourcePolicy(source: GitLabSource): Record<string, unknown> {
  const policy: Record<string, unknown> = { host: source.host, project: source.project, namespace: source.namespace, glab: source.glab };
  if (source.configDir !== undefined) policy.config_dir = source.configDir;
  return policy;
}

type Pending = { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void };

const READ_TOOLS = new Set(["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"]);
const WRITE_TOOLS = new Set(["ctx_edit", "ctx_fill", "ctx_patch"]);
const EXEC_TOOLS = new Set(["ctx_shell"]);

/**
 * Whether the group has a member that is not a zombie. Only observes, and does so
 * synchronously so that the caller's ownership of the group number cannot change
 * meanwhile. `null` when the process table could not be read.
 */
function groupHasLiveMembers(pgid: number): boolean | null {
  const listing = spawnSync("ps", ["-A", "-o", "pgid=", "-o", "stat="], { encoding: "utf8", timeout: 2_000, stdio: ["ignore", "pipe", "ignore"] });
  if (listing.error !== undefined || listing.status !== 0 || typeof listing.stdout !== "string") return null;
  return listing.stdout.split("\n").some((line) => {
    const fields = line.trim().split(/\s+/);
    return fields.length === 2 && fields[0] === String(pgid) && !(fields[1] as string).startsWith("Z");
  });
}

function abortError(): Error {
  const error = new Error("Agent Tools request aborted");
  error.name = "AbortError";
  return error;
}

export class AgentContext {
  readonly projectRoot: string;
  readonly task: string;
  readonly permissions: AgentPermissions;
  readonly executionPolicy: ExecutionPolicy;
  readonly instanceBinary: string;
  /** @deprecated since 1.3.0, use instanceBinary */
  get engineBinary(): string { return this.instanceBinary; }
  readonly timeout: number;
  readonly handshakeTimeout: number;
  private readonly gitlabSource: GitLabSource | undefined;
  private process: ChildProcessWithoutNullStreams | null = null;
  private readonly pending = new Map<string, Pending>();
  private readonly stderrChunks: Buffer[] = [];
  private stderrBytes = 0;
  private inputBuffer = Buffer.alloc(0);
  private nextId = 0;
  private closed = false;
  private processClosed = false;
  private helloAccepted = false;
  private firstLineSeen = false;
  private sessionContainmentValue: SessionContainment = "unavailable";
  private reapPromise: Promise<void> | null = null;
  private policyPath: string | null = null;
  private capabilitiesValue: readonly string[] = [];
  private metricsValue = new AgentMetrics();
  private readonly readyPromise: Promise<this>;

  constructor(projectRoot: string, options: AgentContextOptions = {}) {
    if (typeof projectRoot !== "string" || !projectRoot || projectRoot.includes("\0")) throw new ConfigurationError("project_root must be a directory");
    let root: string;
    try {
      root = realpathSync(resolve(projectRoot));
      if (!statSync(root).isDirectory()) throw new Error();
    } catch { throw new ConfigurationError("project_root must be a directory"); }
    const task = options.task ?? "";
    if (typeof task !== "string" || Buffer.byteLength(task) > 16 * 1024 || task.includes("\0")) throw new ValidationError("task must be a bounded string");
    const permissions = options.permissions instanceof AgentPermissions ? options.permissions : new AgentPermissions(options.permissions);
    const executionPolicy = options.executionPolicy instanceof ExecutionPolicy ? options.executionPolicy : new ExecutionPolicy(options.executionPolicy);
    if (permissions.execute && executionPolicy.allowedExecutables.length === 0) throw new ConfigurationError("execute permission requires at least one allowed executable");
    const timeout = options.timeout ?? 30;
    if (typeof timeout !== "number" || !Number.isFinite(timeout) || timeout < 0.1 || timeout > 120) throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
    const handshakeTimeout = options.handshakeTimeout ?? timeout;
    if (typeof handshakeTimeout !== "number" || !Number.isFinite(handshakeTimeout) || handshakeTimeout < 0.1 || handshakeTimeout > 120) throw new ConfigurationError("handshakeTimeout must be between 0.1 and 120 seconds");
    this.gitlabSource = normalizeGitLabSource(options.gitlabSource);
    this.projectRoot = root;
    this.task = task;
    this.permissions = permissions;
    this.executionPolicy = executionPolicy;
    this.instanceBinary = resolveInstanceBinary(options);
    this.timeout = timeout;
    this.handshakeTimeout = handshakeTimeout;
    try {
      const policyDirectory = mkdtempSync(resolve(root, ".leanctx-agent-"));
      this.policyPath = resolve(policyDirectory, "policy.json");
      const fd = openSync(this.policyPath, "wx", 0o600);
      const policy: Record<string, unknown> = { allow_exec: permissions.execute, allow_write: permissions.write, allowed_env: executionPolicy.allowedEnv, allowed_executables: executionPolicy.allowedExecutables, max_timeout_ms: Math.trunc(executionPolicy.maxTimeout * 1000), schema_version: AGENT_TOOLS_SCHEMA_VERSION };
      if (this.gitlabSource !== undefined) policy.selected_gitlab = gitLabSourcePolicy(this.gitlabSource);
      const payload = Buffer.from(JSON.stringify(policy));
      try { fchmodSync(fd, 0o600); writeFileSync(fd, payload); fsyncSync(fd); } finally { closeSync(fd); }
      const binary = this.resolveBinary();
      const env: Record<string, string> = { LANG: "C", LC_ALL: "C", TZ: "UTC", PYTHONHASHSEED: "0" };
      // Host-selected Engine stores/privacy only; shell execution keeps its own policy.
      for (const name of ["LEAN_CTX_CONFIG_DIR", "LEAN_CTX_DATA_DIR", "LEAN_CTX_STATE_DIR", "LEAN_CTX_CACHE_DIR", "DO_NOT_TRACK"]) { const value = process.env[name]; if (typeof value === "string") env[name] = value; }
      // Executable resolution needs a search path; forward it only under the execute permission.
      if (permissions.execute) for (const name of ["PATH", "TMPDIR", "TEMP", "TMP"]) { const value = process.env[name]; if (typeof value === "string") env[name] = value; }
      // Ask the Engine to contain its session: on POSIX it runs as its own session starter,
      // on Windows it joins a kill-on-close job. An Engine that has the mode acknowledges on
      // its first output line; one that has not ignores the variable.
      env[SESSION_CONTAINMENT_ENV] = "1";
      const child = spawn(binary, ["engine", "tool-session", "--project-root", this.projectRoot, "--policy-file", this.policyPath], { cwd: this.projectRoot, env, shell: false, detached: process.platform !== "win32", stdio: ["pipe", "pipe", "pipe"] });
      this.process = child;
      // A write to an Engine that is gone or was just ended fails asynchronously (EPIPE) and
      // arrives as an `error` event on the stream, not as an exception at `write()`. Without
      // a listener Node raises it as an uncaught exception in the host process. While the
      // context is open it is terminal for the pending calls; after termination it is the
      // expected echo of ending the Engine mid-write and carries no further information.
      child.stdin.on("error", (error) => {
        if (this.closed) return;
        this.failPending(new InstanceCrashed(this.crashMessage(), { cause: error }));
        void this.terminate().catch(() => {});
      });
      child.stdout.on("data", (chunk: Buffer) => this.onStdout(chunk));
      child.stderr.on("data", (chunk: Buffer) => { this.stderrBytes += chunk.byteLength; if (this.stderrBytes <= 64 * 1024) this.stderrChunks.push(Buffer.from(chunk)); });
      child.once("error", (error) => {
        const failure = this.helloAccepted ? new InstanceCrashed("Agent Tools instance exited", { cause: error }) : new InstanceUnavailable("Agent Tools instance could not be started", { cause: error });
        this.failPending(failure);
        void this.terminate().catch(() => {});
      });
      child.once("close", () => { this.processClosed = true; if (!this.closed) this.failPending(new InstanceCrashed(this.crashMessage())); });
      // An Engine that exits, or closes stdout while it or a descendant keeps running,
      // is terminal at once: pending calls fail typed and the whole tree is ended.
      const engineGone = (): void => {
        if (this.closed) return;
        this.failPending(new InstanceCrashed(this.crashMessage()));
        void this.terminate().catch(() => {});
      };
      // Nothing is signalled on `exit`. Node reaps a child before it emits `exit`, and
      // from then on the child's pid no longer pins its process group: the number may
      // belong to another group. Descendants an exited Engine left behind are therefore
      // not reachable safely from here (see terminate()); stdout `end` is the terminal
      // signal for pending calls.
      child.stdout.once("end", engineGone);
    } catch (error) {
      this.removePolicy();
      void this.terminate().catch(() => {});
      throw error;
    }
    this.readyPromise = this.start().catch((error) => {
      this.failPending(error instanceof Error ? error : new InstanceProtocolError("Agent Tools startup failed"));
      void this.terminate().catch(() => {});
      throw error;
    });
    void this.readyPromise.catch(() => undefined);
  }

  static async open(projectRoot: string, options: AgentContextOptions = {}): Promise<AgentContext> { const context = new AgentContext(projectRoot, options); await context.ready(); return context; }
  async ready(): Promise<this> { return this.readyPromise; }
  get capabilities(): readonly string[] { return this.capabilitiesValue; }
  /**
   * How the Engine's process tree is contained. Final once `ready()` has settled.
   * "unavailable" is reduced containment: the SDK then ends the Engine's process group
   * only while the Engine itself is still unreaped, and cannot end what an Engine that
   * exited first has left behind.
   */
  get sessionContainment(): SessionContainment { return this.sessionContainmentValue; }
  get metrics(): AgentMetrics { return this.metricsValue; }

  private resolveBinary(): string {
    let binary = this.instanceBinary;
    if (!isAbsolute(binary) && !binary.includes("/") && !binary.includes("\\")) {
      const candidates = (process.env.PATH ?? "").split(delimiter).filter((entry) => entry.length > 0).map((entry) => resolve(entry, binary));
      const resolvedBinary = candidates.find((candidate) => { try { return statSync(candidate).isFile(); } catch { return false; } });
      if (resolvedBinary === undefined) throw new InstanceUnavailable("configured instance binary is unavailable");
      binary = resolvedBinary;
    } else binary = resolve(binary);
    try {
      if (!statSync(binary).isFile()) throw new Error();
      // Canonicalize before spawning, preventing mutable PATH/relative aliases.
      binary = realpathSync(binary);
      if ((statSync(binary).mode & constants.S_IXUSR) === 0) throw new Error();
    } catch { throw new InstanceUnavailable("configured instance binary is unavailable"); }
    return binary;
  }

  private async start(): Promise<this> {
    const result = await this.exchangeRaw({ op: "hello", schema_version: AGENT_TOOLS_SCHEMA_VERSION, transport_version: AGENT_TOOLS_TRANSPORT_VERSION, agent_tools_interface_version: AGENT_TOOLS_INTERFACE_VERSION, sdk_version: "1.3.0" }, true, this.handshakeTimeout);
    this.acceptHello(result);
    this.helloAccepted = true;
    this.removePolicy();
    return this;
  }

  private onStdout(chunk: Buffer): void {
    if (this.closed) return;
    this.inputBuffer = Buffer.concat([this.inputBuffer, chunk]);
    if (this.inputBuffer.byteLength > MAX_RESPONSE_BYTES + 1) { this.protocolViolation(new InstanceProtocolError("Agent Tools response exceeds its bound")); return; }
    while (true) {
      const newline = this.inputBuffer.indexOf(0x0a);
      if (newline < 0) break;
      const line = this.inputBuffer.subarray(0, newline);
      this.inputBuffer = this.inputBuffer.subarray(newline + 1);
      if (line.byteLength > MAX_RESPONSE_BYTES) { this.protocolViolation(new InstanceProtocolError("Agent Tools response exceeds its bound")); return; }
      // Only the very first line can be the session starter's acknowledgement: it is
      // written before the Engine exists. Anything else there means no starter.
      if (!this.firstLineSeen) {
        this.firstLineSeen = true;
        if (line.equals(SESSION_CONTAINMENT_ACK)) { this.sessionContainmentValue = "engine-starter"; continue; }
      }
      let response: unknown;
      // The hello's two versions must be written as JSON integers: 1.0 and 1e0 are refused here, before Number makes them 1. No other response has these two result fields.
      try { response = strictJsonLoads(line, "Agent Tools response", HELLO_INTEGER_PATHS); } catch { this.protocolViolation(new InstanceProtocolError("Agent Tools response is invalid JSON")); return; }
      this.dispatch(response);
    }
  }

  private dispatch(value: unknown): void {
    if (!value || typeof value !== "object" || Array.isArray(value)) { this.protocolViolation(new InstanceProtocolError("Agent Tools response envelope is invalid")); return; }
    const item = value as Record<string, unknown>;
    if (typeof item.id !== "string" || typeof item.ok !== "boolean") { this.protocolViolation(new InstanceProtocolError("Agent Tools response envelope is invalid")); return; }
    const waiter = this.pending.get(item.id); if (!waiter) { this.protocolViolation(new InstanceProtocolError("Agent Tools response id is unexpected")); return; }
    if (item.ok) {
      if (Object.keys(item).length !== 3 || !item.result || typeof item.result !== "object" || Array.isArray(item.result)) {
        const error = new InstanceProtocolError("Agent Tools response omitted result");
        waiter.reject(error); this.pending.delete(item.id); this.protocolViolation(error); return;
      }
      this.pending.delete(item.id);
      waiter.resolve(item.result as Record<string, unknown>);
    } else {
      if (Object.keys(item).length !== 3 || !item.error || typeof item.error !== "object" || Array.isArray(item.error)) {
        const error = new InstanceProtocolError("Agent Tools error envelope is invalid");
        waiter.reject(error); this.pending.delete(item.id); this.protocolViolation(error); return;
      }
      const error = item.error as Record<string, unknown>;
      if (Object.keys(error).length !== 2 || typeof error.code !== "string" || typeof error.message !== "string") {
        const violation = new InstanceProtocolError("Agent Tools error envelope is invalid");
        waiter.reject(violation); this.pending.delete(item.id); this.protocolViolation(violation); return;
      }
      this.pending.delete(item.id);
      waiter.reject(this.errorFromWire(error.code, error.message));
    }
  }

  private exchangeRaw(request: Record<string, unknown>, bypassReady = false, responseTimeout = this.timeout, signal?: AbortSignal): Promise<Record<string, unknown>> {
    if (!bypassReady) return this.readyPromise.then(() => this.exchangeRaw(request, true, responseTimeout, signal));
    const child = this.process;
    if (this.closed || child === null || child.exitCode !== null) return Promise.reject(new InstanceCrashed(this.crashMessage()));
    if (signal?.aborted) return Promise.reject(abortError());
    const id = String(++this.nextId);
    const envelope = { ...request, id };
    const encoded = Buffer.from(`${JSON.stringify(envelope)}\n`);
    if (encoded.byteLength > MAX_REQUEST_BYTES) return Promise.reject(new InstanceProtocolError("Agent Tools request exceeds its bound"));
    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => { if (!this.pending.has(id)) return; this.pending.delete(id); void this.terminate().catch(() => {}); rejectPromise(new InstanceTimeout("Agent Tools response exceeded its deadline")); }, responseTimeout * 1000);
      const onAbort = () => { if (!this.pending.has(id)) return; this.pending.delete(id); void this.terminate().catch(() => {}); rejectPromise(abortError()); };
      signal?.addEventListener("abort", onAbort, { once: true });
      const cleanup = () => { clearTimeout(timer); signal?.removeEventListener("abort", onAbort); };
      this.pending.set(id, { resolve: (value) => { cleanup(); resolvePromise(value); }, reject: (error) => { cleanup(); rejectPromise(error); } });
      try { child.stdin.write(encoded); } catch (error) { cleanup(); this.pending.delete(id); rejectPromise(new InstanceCrashed(this.crashMessage(), { cause: error })); }
    });
  }

  private acceptHello(value: Record<string, unknown>): void {
    const expected = ["agent_tools_interface_version", "allow_exec", "allow_write", "capabilities", "engine_version", "schema_version", "transport_version"];
    if (Object.keys(value).length !== expected.length || expected.some((key) => !(key in value)) || value.agent_tools_interface_version !== AGENT_TOOLS_INTERFACE_VERSION || value.schema_version !== AGENT_TOOLS_SCHEMA_VERSION || value.transport_version !== AGENT_TOOLS_TRANSPORT_VERSION || value.allow_write !== this.permissions.write || value.allow_exec !== this.permissions.execute) throw new InstanceProtocolError("Agent Tools hello is incompatible");
    const engineVersion = value.engine_version;
    if (typeof engineVersion !== "string" || !ADMITTED_ENGINE_PAIRS.some(([iface, schema, transport, engine]) => iface === AGENT_TOOLS_INTERFACE_VERSION && schema === AGENT_TOOLS_SCHEMA_VERSION && transport === AGENT_TOOLS_TRANSPORT_VERSION && engine === engineVersion)) throw new InstanceProtocolError("Agent Tools instance pair is unsupported");
    if (!Array.isArray(value.capabilities) || value.capabilities.some((item) => typeof item !== "string")) throw new InstanceProtocolError("Agent Tools capabilities are invalid");
    const capabilities = value.capabilities as string[];
    if (JSON.stringify(capabilities) !== JSON.stringify([...new Set(capabilities)].sort())) throw new InstanceProtocolError("Agent Tools capabilities are not canonical");
    const expectedCapabilities = new Set(READ_TOOLS); if (this.permissions.write) for (const item of WRITE_TOOLS) expectedCapabilities.add(item); if (this.permissions.execute) for (const item of EXEC_TOOLS) expectedCapabilities.add(item);
    if (this.gitlabSource !== undefined) expectedCapabilities.add("ctx_provider");
    if (capabilities.length !== expectedCapabilities.size || capabilities.some((item) => !expectedCapabilities.has(item))) throw new InstanceProtocolError("Agent Tools capabilities do not match policy");
    this.capabilitiesValue = Object.freeze([...capabilities]);
  }

  private errorFromWire(code: string, message: string): Error {
    if (code === "permission_denied") return new AgentPermissionError(message);
    if (code === "unsupported_capability") return new UnsupportedCapabilityError(message);
    if (code === "invalid_request" || code === "invalid_state" || code === "unsupported_interface") return new InstanceProtocolError(message);
    return new InstanceExecutionError(message);
  }
  private failPending(error: Error): void { for (const pending of this.pending.values()) pending.reject(error); this.pending.clear(); }
  /** Protocol violations are terminal: never continue on ambiguous state. */
  private protocolViolation(error: InstanceProtocolError): void { this.failPending(error); void this.terminate().catch(() => {}); }
  private crashMessage(): string { const detail = Buffer.concat(this.stderrChunks).toString("utf8").trim(); return detail ? `Agent Tools instance exited: ${detail.slice(0, 4096)}` : "Agent Tools instance exited"; }
  /**
   * Removes exactly the policy file and the directory this context created for it.
   * Idempotent. Returns false, and keeps the path for the next attempt, when either
   * still exists afterwards.
   */
  private removePolicy(): boolean {
    if (!this.policyPath) return true;
    const absent = (remove: () => void): boolean => {
      try { remove(); return true; } catch (error) { return (error as NodeJS.ErrnoException).code === "ENOENT"; }
    };
    const policyPath = this.policyPath;
    const fileGone = absent(() => unlinkSync(policyPath));
    const directoryGone = absent(() => rmdirSync(resolve(policyPath, "..")));
    if (!fileGone || !directoryGone) return false;
    this.policyPath = null;
    return true;
  }
  private static policyFailure(): InstanceExecutionError { return new InstanceExecutionError("Agent Tools policy could not be removed"); }
  private terminate(): Promise<void> {
    if (this.reapPromise) return this.reapPromise;
    this.closed = true;
    this.failPending(new InstanceCrashed("AgentContext terminated"));
    const child = this.process;
    this.reapPromise = new Promise((resolvePromise, rejectPromise) => {
      if (child === null || this.processClosed) {
        if (this.removePolicy()) resolvePromise(); else rejectPromise(AgentContext.policyFailure());
        return;
      }
      let cleanupError: InstanceExecutionError | null = null;
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = (error: InstanceExecutionError | null): void => {
        if (settled) return;
        settled = true;
        if (timer !== undefined) clearTimeout(timer);
        child.removeListener("close", onClose);
        // A cleanup failure never passes as a clean termination.
        const policyRemoved = this.removePolicy();
        if (error) rejectPromise(error); else if (!policyRemoved) rejectPromise(AgentContext.policyFailure()); else resolvePromise();
      };
      const pid = child.pid;
      const processGroup = pid !== undefined && process.platform !== "win32";
      const onClose = (): void => {
        // The leader is reaped by now: nothing is signalled here, not even to confirm.
        // On Windows the outcome of the tree kill is part of the verdict: wait for it.
        if (treeKillPending) { closedDuringTreeKill = true; return; }
        finish(cleanupError);
      };
      let treeKillPending = false;
      let closedDuringTreeKill = false;
      child.once("close", onClose);
      timer = setTimeout(() => finish(cleanupError ?? new InstanceExecutionError("Agent Tools process tree did not close within 5000 ms")), PROCESS_CLEANUP_TIMEOUT_MS);
      const signalLeaderAsFallback = (): void => {
        if (!processGroup || this.processClosed || child.exitCode !== null || child.signalCode !== null) return;
        try { child.kill("SIGKILL"); } catch { /* preserve the process-group failure */ }
      };
      const killLeader = (): void => {
        // Ownership rule. The group is addressed by the leader's pid, which is ours only
        // while the leader is unreaped. Node reaps on its event loop and then sets
        // exitCode or signalCode before any of our code runs again; so if both are still
        // null here, the leader has not been reaped, and it cannot be reaped while this
        // synchronous block runs. Every group signal is sent inside such a block. If the
        // leader has already been reaped, nothing is signalled: what it left in its group
        // is not reachable safely, and the cleanup then ends as unconfirmed at its
        // deadline unless everything closes by itself.
        if (child.exitCode !== null || child.signalCode !== null) return;
        try {
          const signalled = processGroup ? process.kill(-(pid as number), "SIGKILL") : child.kill("SIGKILL");
          if (!signalled) {
            cleanupError = new InstanceExecutionError("Agent Tools process tree could not be terminated");
            signalLeaderAsFallback();
          }
        } catch (error) {
          const code = (error as NodeJS.ErrnoException).code;
          // ESRCH means the exact group or child target is already absent; still wait for close.
          // Darwin reports EPERM for a group whose only member is the exited, not yet
          // reaped leader; that is settled by looking at the process table, in this same
          // synchronous block, never by signalling again later.
          if (code !== "ESRCH" && !(code === "EPERM" && processGroup && groupHasLiveMembers(pid as number) === false)) {
            cleanupError = new InstanceExecutionError("Agent Tools process tree could not be terminated");
            signalLeaderAsFallback();
          }
        }
      };
      if (pid === undefined || process.platform !== "win32") { killLeader(); return; }
      // Windows: `taskkill /T` ends the descendants reachable through live parent links.
      // A descendant whose parent already exited is not reachable; that needs a Job Object.
      // The helper runs asynchronously under its own timer. When the timer ends it is
      // killed and not waited for: a helper that overran, failed or could not be started
      // is a failed tree kill. Exit status 128 means the Engine was already gone.
      treeKillPending = true;
      let treeTimer: ReturnType<typeof setTimeout> | undefined;
      const afterTreeKill = (failed: boolean): void => {
        if (!treeKillPending) return;
        treeKillPending = false;
        if (treeTimer !== undefined) clearTimeout(treeTimer);
        if (failed) cleanupError = new InstanceExecutionError("Agent Tools process tree termination could not be verified");
        if (closedDuringTreeKill) { finish(cleanupError); return; }
        if (child.exitCode === null && child.signalCode === null) killLeader();
      };
      let helper: ReturnType<typeof spawn>;
      try { helper = spawn("taskkill", ["/F", "/T", "/PID", String(pid)], { stdio: "ignore", windowsHide: true }); }
      catch { afterTreeKill(true); return; }
      treeTimer = setTimeout(() => { try { helper.kill(); } catch { /* reported below */ } afterTreeKill(true); }, TASKKILL_TIMEOUT_MS);
      helper.once("error", () => afterTreeKill(true));
      helper.once("exit", (code) => afterTreeKill(code !== 0 && code !== 128));
    });
    return this.reapPromise;
  }
  private async reap(): Promise<void> { if (this.reapPromise) await this.reapPromise; else if (!this.removePolicy()) throw AgentContext.policyFailure(); }

  async call(tool: string, argumentsValue: Record<string, unknown> = {}): Promise<ToolResult> {
    if (typeof tool !== "string" || !tool) throw new ValidationError("tool must be a non-empty string");
    if (EXEC_TOOLS.has(tool)) throw new AgentPermissionError("execution tools must use run()");
    if (WRITE_TOOLS.has(tool) && !this.permissions.write) throw new AgentPermissionError("write permission is disabled");
    return this.callTool(tool, argumentsValue);
  }
  private async callTool(tool: string, argumentsValue: Record<string, unknown>, responseTimeout = this.timeout, signal?: AbortSignal): Promise<ToolResult> {
    if (!this.capabilitiesValue.includes(tool)) throw new UnsupportedCapabilityError(`LeanCTX instance did not negotiate capability: ${tool}`);
    if (!argumentsValue || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) throw new ValidationError("arguments must be a string-keyed mapping");
    try {
      const canonical = canonicalBytes(argumentsValue);
      if (canonical.byteLength > MAX_REQUEST_BYTES) throw new ValidationError("arguments exceed the request bound");
    } catch (error) {
      if (error instanceof ValidationError) throw error;
      throw new ValidationError("arguments must be deterministic JSON data", { cause: error });
    }
    const result = await this.exchangeRaw({ op: "call", tool, arguments: { ...argumentsValue } }, false, responseTimeout, signal);
    let parsed: ToolResult;
    try { parsed = this.parseToolResult(tool, result); }
    catch (error) {
      if (error instanceof InstanceProtocolError) this.protocolViolation(error);
      throw error;
    }
    this.metricsValue = new AgentMetrics(this.metricsValue.toolCalls + 1, this.metricsValue.originalTokens + parsed.originalTokens, this.metricsValue.outputTokens + parsed.outputTokens, this.metricsValue.savedTokens + parsed.savedTokens);
    return parsed;
  }
  private parseToolResult(tool: string, value: Record<string, unknown>): ToolResult {
    const expected = ["text", "content_blocks", "original_tokens", "output_tokens", "saved_tokens", "mode", "changed", "shell"];
    if (Object.keys(value).length !== expected.length || expected.some((key) => !(key in value))) throw new InstanceProtocolError("Agent Tools result fields are invalid");
    if (typeof value.text !== "string" || Buffer.byteLength(value.text) > MAX_TEXT_BYTES || (value.mode !== null && typeof value.mode !== "string")) throw new InstanceProtocolError("Agent Tools text or mode is invalid");
    const numbers = [value.original_tokens, value.output_tokens, value.saved_tokens]; if (numbers.some((item) => typeof item !== "number" || !Number.isSafeInteger(item) || item < 0) || (value.output_tokens as number) + (value.saved_tokens as number) !== value.original_tokens) throw new InstanceProtocolError("Agent Tools token metrics are invalid");
    if (typeof value.changed !== "boolean" || (value.shell !== null && (!value.shell || typeof value.shell !== "object" || Array.isArray(value.shell)))) throw new InstanceProtocolError("Agent Tools status metadata is invalid");
    if (!Array.isArray(value.content_blocks) || value.content_blocks.some((item) => !item || typeof item !== "object" || Array.isArray(item))) throw new InstanceProtocolError("Agent Tools content blocks are invalid");
    return new ToolResult(tool, { text: value.text, content_blocks: value.content_blocks as Record<string, unknown>[], original_tokens: value.original_tokens as number, output_tokens: value.output_tokens as number, saved_tokens: value.saved_tokens as number, mode: value.mode as string | null, changed: value.changed, shell: value.shell as Record<string, unknown> | null });
  }

  /** Read a source; an optional signal aborts and terminates this context. */
  read(path: string, mode: ReadMode | string = ReadMode.AUTO, fresh = false, signal?: AbortSignal): Promise<ToolResult> { return this.callTool("ctx_read", { path, mode: String(mode), fresh }, this.timeout, signal); }
  search(pattern: string, options: { path?: string; maxResults?: number; include?: string } = {}): Promise<ToolResult> { const args: Record<string, unknown> = { path: options.path ?? ".", pattern, max_results: options.maxResults ?? 50 }; if (options.include !== undefined) args.include = options.include; return this.callTool("ctx_search", args); }
  glob(pattern: string, options: { path?: string; maxResults?: number } = {}): Promise<ToolResult> { return this.callTool("ctx_glob", { path: options.path ?? ".", pattern, max_results: options.maxResults ?? 200 }); }
  tree(path = ".", options: { depth?: number; showHidden?: boolean } = {}): Promise<ToolResult> { return this.callTool("ctx_tree", { path, depth: options.depth ?? 3, show_hidden: options.showHidden ?? false }); }
  compose(task = this.task, options: { path?: string } = {}): Promise<ToolResult> { return this.callTool("ctx_compose", { path: options.path ?? ".", task }); }
  symbol(name: string): Promise<ToolResult> { return this.callTool("ctx_symbol", { name }); }
  patch(options: { path: string; op: string; [key: string]: unknown }): Promise<ToolResult> { if (!this.permissions.write) return Promise.reject(new AgentPermissionError("write permission is disabled")); const { path, op, ...rest } = options; return this.callTool("ctx_patch", { ...rest, path, op }); }
  createFile(path: string, text: string): Promise<ToolResult> { return this.patch({ path, op: "create", new_text: text }); }
  replaceUnique(path: string, oldText: string, newText: string): Promise<ToolResult> { return this.patch({ path, op: "replace_unique", old_text: oldText, new_text: newText }); }
  create_file(path: string, text: string): Promise<ToolResult> { return this.createFile(path, text); }
  replace_unique(path: string, oldText: string, newText: string): Promise<ToolResult> { return this.replaceUnique(path, oldText, newText); }
  /** Execute one structured argv request; `signal` aborts and tears down this context. */
  run(argv: readonly string[], options: { cwd?: string; env?: Record<string, string>; timeout?: number; signal?: AbortSignal } = {}): Promise<ToolResult> {
    if (!this.permissions.execute) return Promise.reject(new AgentPermissionError("execute permission is disabled"));
    if (!Array.isArray(argv) || argv.length === 0 || argv.some((item) => typeof item !== "string" || !item)) return Promise.reject(new ValidationError("argv must be a non-empty sequence of strings"));
    const executable = argv[0]; if (executable.includes("/") || executable.includes("\\") || !this.executionPolicy.allowedExecutables.includes(executable)) return Promise.reject(new AgentPermissionError("executable is not allowed: " + executable));
    const timeout = options.timeout ?? this.executionPolicy.maxTimeout; if (typeof timeout !== "number" || !Number.isFinite(timeout)) return Promise.reject(new ValidationError("timeout must be numeric")); if (timeout < 0.1 || timeout > this.executionPolicy.maxTimeout) return Promise.reject(new ValidationError("timeout exceeds ExecutionPolicy"));
    const cwd = options.cwd ?? ".";
    let checkedCwd: string;
    try {
      const realCwd = realpathSync(resolve(this.projectRoot, cwd));
      if (!statSync(realCwd).isDirectory() || (realCwd !== this.projectRoot && !realCwd.startsWith(`${this.projectRoot}${sep}`))) return Promise.reject(new AgentPermissionError("cwd escapes project root"));
      checkedCwd = relative(this.projectRoot, realCwd).split(sep).join("/") || ".";
    } catch { return Promise.reject(new AgentPermissionError("cwd escapes project root")); }
    const environment = options.env ?? {}; for (const [key, value] of Object.entries(environment)) { if (typeof value !== "string") return Promise.reject(new ValidationError("env must be a string mapping")); if (!this.executionPolicy.allowedEnv.includes(key)) return Promise.reject(new AgentPermissionError("environment variable is not allowed: " + key)); }
    return this.callTool("ctx_shell", { argv: [...argv], cwd: checkedCwd, env: { ...environment }, timeout_ms: Math.trunc(timeout * 1000) }, Math.max(this.timeout, timeout + 2), options.signal);
  }
  async close(): Promise<void> { if (this.closed) { await this.reap(); return; } try { await this.exchangeRaw({ op: "close" }); } catch { /* terminal close remains best effort */ } await this.terminate(); }
  async cancel(): Promise<void> { await this.terminate(); }
  async reconnect(): Promise<AgentContext> { await this.close(); return AgentContext.open(this.projectRoot, { task: this.task, permissions: this.permissions, executionPolicy: this.executionPolicy, instanceBinary: this.instanceBinary, timeout: this.timeout, handshakeTimeout: this.handshakeTimeout, ...(this.gitlabSource === undefined ? {} : { gitlabSource: this.gitlabSource }) }); }
}

export class AsyncAgentContext {
  private context: AgentContext | null = null;
  private readonly projectRoot: string;
  private readonly options: AgentContextOptions;
  constructor(projectRoot: string, options: AgentContextOptions = {}) {
    this.projectRoot = projectRoot;
    const gitlabSource = normalizeGitLabSource(options.gitlabSource);
    this.options = Object.freeze({ ...options, ...(gitlabSource === undefined ? {} : { gitlabSource }) });
  }
  async open(): Promise<this> { if (!this.context) this.context = await AgentContext.open(this.projectRoot, this.options); return this; }
  private get current(): AgentContext { if (!this.context) throw new InstanceUnavailable("AsyncAgentContext is not open"); return this.context; }
  get capabilities(): readonly string[] { return this.current.capabilities; }
  get metrics(): AgentMetrics { return this.current.metrics; }
  async call(tool: string, args?: Record<string, unknown>): Promise<ToolResult> { return this.current.call(tool, args); }
  async read(path: string, mode: ReadMode | string = ReadMode.AUTO, fresh = false, signal?: AbortSignal): Promise<ToolResult> { return this.current.read(path, mode, fresh, signal); }
  async search(pattern: string, options?: { path?: string; maxResults?: number; include?: string }): Promise<ToolResult> { return this.current.search(pattern, options); }
  async glob(pattern: string, options?: { path?: string; maxResults?: number }): Promise<ToolResult> { return this.current.glob(pattern, options); }
  async tree(path = ".", options?: { depth?: number; showHidden?: boolean }): Promise<ToolResult> { return this.current.tree(path, options); }
  async compose(task = this.current.task, options?: { path?: string }): Promise<ToolResult> { return this.current.compose(task, options); }
  async symbol(name: string): Promise<ToolResult> { return this.current.symbol(name); }
  async patch(options: { path: string; op: string; [key: string]: unknown }): Promise<ToolResult> { return this.current.patch(options); }
  async createFile(path: string, text: string): Promise<ToolResult> { return this.current.createFile(path, text); }
  async replaceUnique(path: string, oldText: string, newText: string): Promise<ToolResult> { return this.current.replaceUnique(path, oldText, newText); }
  async create_file(path: string, text: string): Promise<ToolResult> { return this.current.createFile(path, text); }
  async replace_unique(path: string, oldText: string, newText: string): Promise<ToolResult> { return this.current.replaceUnique(path, oldText, newText); }
  async run(argv: readonly string[], options?: { cwd?: string; env?: Record<string, string>; timeout?: number; signal?: AbortSignal }): Promise<ToolResult> { return this.current.run(argv, options); }
  async cancel(): Promise<void> { if (this.context) await this.context.cancel(); }
  async reconnect(): Promise<this> { if (this.context) this.context = await this.context.reconnect(); else await this.open(); return this; }
  async close(): Promise<void> { if (this.context) await this.context.close(); }
}
