/** Stable Agent Tools client for a host-owned agent loop. */

import { closeSync, constants, fchmodSync, fsyncSync, mkdtempSync, openSync, realpathSync, rmdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { delimiter, isAbsolute, resolve, sep } from "node:path";
import {
  AgentPermissionError,
  ConfigurationError,
  EngineCrashed,
  EngineError,
  EngineExecutionError,
  EngineProtocolError,
  EngineTimeout,
  EngineUnavailable,
  UnsupportedCapabilityError,
  ValidationError,
} from "./errors.js";
import { canonicalBytes, strictJsonLoads } from "./protocol.js";

export const AGENT_TOOLS_INTERFACE_VERSION = "1.0.0" as const;
export const AGENT_TOOLS_SCHEMA_VERSION = 1 as const;
export const AGENT_TOOLS_TRANSPORT_VERSION = 1 as const;
export const SUPPORTED_AGENT_TOOLS_ENGINE_VERSION = "3.10.1" as const;
const MAX_REQUEST_BYTES = 1024 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const MAX_TEXT_BYTES = 8 * 1024 * 1024;

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

const FORBIDDEN_ENV = new Set(["COMSPEC", "DYLD_INSERT_LIBRARIES", "HOME", "LD_PRELOAD", "PATH", "PATHEXT", "PYTHONPATH", "RUSTC_WRAPPER", "SHELL"]);
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
  engineBinary?: string;
  timeout?: number;
}>;

type Pending = { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void };

const READ_TOOLS = new Set(["ctx_compose", "ctx_glob", "ctx_read", "ctx_search", "ctx_symbol", "ctx_tree"]);
const WRITE_TOOLS = new Set(["ctx_edit", "ctx_fill", "ctx_patch"]);
const EXEC_TOOLS = new Set(["ctx_shell"]);

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
  readonly engineBinary: string;
  readonly timeout: number;
  private process: ChildProcessWithoutNullStreams | null = null;
  private readonly pending = new Map<string, Pending>();
  private readonly stderrChunks: Buffer[] = [];
  private stderrBytes = 0;
  private inputBuffer = Buffer.alloc(0);
  private nextId = 0;
  private closed = false;
  private helloAccepted = false;
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
    this.projectRoot = root;
    this.task = task;
    this.permissions = permissions;
    this.executionPolicy = executionPolicy;
    this.engineBinary = options.engineBinary ?? "lean-ctx";
    this.timeout = timeout;
    try {
      const policyDirectory = mkdtempSync(resolve(root, ".leanctx-agent-"));
      this.policyPath = resolve(policyDirectory, "policy.json");
      const fd = openSync(this.policyPath, "wx", 0o600);
      const payload = Buffer.from(JSON.stringify({ allow_exec: permissions.execute, allow_write: permissions.write, allowed_env: executionPolicy.allowedEnv, allowed_executables: executionPolicy.allowedExecutables, max_timeout_ms: Math.trunc(executionPolicy.maxTimeout * 1000), schema_version: AGENT_TOOLS_SCHEMA_VERSION }));
      try { fchmodSync(fd, 0o600); writeFileSync(fd, payload); fsyncSync(fd); } finally { closeSync(fd); }
      const binary = this.resolveBinary();
      const child = spawn(binary, ["engine", "tool-session", "--project-root", this.projectRoot, "--policy-file", this.policyPath], { cwd: this.projectRoot, env: { LANG: "C", LC_ALL: "C", TZ: "UTC", PYTHONHASHSEED: "0" }, shell: false, detached: process.platform !== "win32", stdio: ["pipe", "pipe", "pipe"] });
      this.process = child;
      child.stdout.on("data", (chunk: Buffer) => this.onStdout(chunk));
      child.stderr.on("data", (chunk: Buffer) => { this.stderrBytes += chunk.byteLength; if (this.stderrBytes <= 64 * 1024) this.stderrChunks.push(Buffer.from(chunk)); });
      child.once("error", (error) => {
        const failure = this.helloAccepted ? new EngineCrashed("Agent Tools Engine exited", { cause: error }) : new EngineUnavailable("Agent Tools Engine could not be started", { cause: error });
        this.failPending(failure);
        void this.terminate();
      });
      child.once("close", () => { if (!this.closed) this.failPending(new EngineCrashed(this.crashMessage())); });
    } catch (error) {
      this.removePolicy();
      void this.terminate();
      throw error;
    }
    this.readyPromise = this.start().catch((error) => {
      this.failPending(error instanceof Error ? error : new EngineProtocolError("Agent Tools startup failed"));
      void this.terminate();
      throw error;
    });
    void this.readyPromise.catch(() => undefined);
  }

  static async open(projectRoot: string, options: AgentContextOptions = {}): Promise<AgentContext> { const context = new AgentContext(projectRoot, options); await context.ready(); return context; }
  async ready(): Promise<this> { return this.readyPromise; }
  get capabilities(): readonly string[] { return this.capabilitiesValue; }
  get metrics(): AgentMetrics { return this.metricsValue; }

  private resolveBinary(): string {
    let binary = this.engineBinary;
    if (!isAbsolute(binary) && !binary.includes("/") && !binary.includes("\\")) {
      const candidates = (process.env.PATH ?? "").split(delimiter).filter((entry) => entry.length > 0).map((entry) => resolve(entry, binary));
      const resolvedBinary = candidates.find((candidate) => { try { return statSync(candidate).isFile(); } catch { return false; } });
      if (resolvedBinary === undefined) throw new EngineUnavailable("configured Engine binary is unavailable");
      binary = resolvedBinary;
    } else binary = resolve(binary);
    try {
      if (!statSync(binary).isFile()) throw new Error();
      // Canonicalize before spawning, preventing mutable PATH/relative aliases.
      binary = realpathSync(binary);
      if ((statSync(binary).mode & constants.S_IXUSR) === 0) throw new Error();
    } catch { throw new EngineUnavailable("configured Engine binary is unavailable"); }
    return binary;
  }

  private async start(): Promise<this> {
    const result = await this.exchangeRaw({ op: "hello", schema_version: AGENT_TOOLS_SCHEMA_VERSION, transport_version: AGENT_TOOLS_TRANSPORT_VERSION, agent_tools_interface_version: AGENT_TOOLS_INTERFACE_VERSION, sdk_version: "1.1.0" }, true);
    this.acceptHello(result);
    this.helloAccepted = true;
    this.removePolicy();
    return this;
  }

  private onStdout(chunk: Buffer): void {
    if (this.closed) return;
    this.inputBuffer = Buffer.concat([this.inputBuffer, chunk]);
    if (this.inputBuffer.byteLength > MAX_RESPONSE_BYTES + 1) { this.protocolViolation(new EngineProtocolError("Agent Tools response exceeds its bound")); return; }
    while (true) {
      const newline = this.inputBuffer.indexOf(0x0a);
      if (newline < 0) break;
      const line = this.inputBuffer.subarray(0, newline);
      this.inputBuffer = this.inputBuffer.subarray(newline + 1);
      if (line.byteLength > MAX_RESPONSE_BYTES) { this.protocolViolation(new EngineProtocolError("Agent Tools response exceeds its bound")); return; }
      let response: unknown;
      try { response = strictJsonLoads(line, "Agent Tools response"); } catch { this.protocolViolation(new EngineProtocolError("Agent Tools response is invalid JSON")); return; }
      this.dispatch(response);
    }
  }

  private dispatch(value: unknown): void {
    if (!value || typeof value !== "object" || Array.isArray(value)) { this.protocolViolation(new EngineProtocolError("Agent Tools response envelope is invalid")); return; }
    const item = value as Record<string, unknown>;
    if (typeof item.id !== "string" || typeof item.ok !== "boolean") { this.protocolViolation(new EngineProtocolError("Agent Tools response envelope is invalid")); return; }
    const waiter = this.pending.get(item.id); if (!waiter) { this.protocolViolation(new EngineProtocolError("Agent Tools response id is unexpected")); return; }
    if (item.ok) {
      if (Object.keys(item).length !== 3 || !item.result || typeof item.result !== "object" || Array.isArray(item.result)) {
        const error = new EngineProtocolError("Agent Tools response omitted result");
        waiter.reject(error); this.pending.delete(item.id); this.protocolViolation(error); return;
      }
      this.pending.delete(item.id);
      waiter.resolve(item.result as Record<string, unknown>);
    } else {
      if (Object.keys(item).length !== 3 || !item.error || typeof item.error !== "object" || Array.isArray(item.error)) {
        const error = new EngineProtocolError("Agent Tools error envelope is invalid");
        waiter.reject(error); this.pending.delete(item.id); this.protocolViolation(error); return;
      }
      const error = item.error as Record<string, unknown>;
      if (Object.keys(error).length !== 2 || typeof error.code !== "string" || typeof error.message !== "string") {
        const violation = new EngineProtocolError("Agent Tools error envelope is invalid");
        waiter.reject(violation); this.pending.delete(item.id); this.protocolViolation(violation); return;
      }
      this.pending.delete(item.id);
      waiter.reject(this.errorFromWire(error.code, error.message));
    }
  }

  private exchangeRaw(request: Record<string, unknown>, bypassReady = false, responseTimeout = this.timeout, signal?: AbortSignal): Promise<Record<string, unknown>> {
    if (!bypassReady) return this.readyPromise.then(() => this.exchangeRaw(request, true, responseTimeout, signal));
    const child = this.process;
    if (this.closed || child === null || child.exitCode !== null) return Promise.reject(new EngineCrashed(this.crashMessage()));
    if (signal?.aborted) return Promise.reject(abortError());
    const id = String(++this.nextId);
    const envelope = { ...request, id };
    const encoded = Buffer.from(`${JSON.stringify(envelope)}\n`);
    if (encoded.byteLength > MAX_REQUEST_BYTES) return Promise.reject(new EngineProtocolError("Agent Tools request exceeds its bound"));
    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => { if (!this.pending.has(id)) return; this.pending.delete(id); void this.terminate(); rejectPromise(new EngineTimeout("Agent Tools response exceeded its deadline")); }, responseTimeout * 1000);
      const onAbort = () => { if (!this.pending.has(id)) return; this.pending.delete(id); void this.terminate(); rejectPromise(abortError()); };
      signal?.addEventListener("abort", onAbort, { once: true });
      const cleanup = () => { clearTimeout(timer); signal?.removeEventListener("abort", onAbort); };
      this.pending.set(id, { resolve: (value) => { cleanup(); resolvePromise(value); }, reject: (error) => { cleanup(); rejectPromise(error); } });
      try { child.stdin.write(encoded); } catch (error) { cleanup(); this.pending.delete(id); rejectPromise(new EngineCrashed(this.crashMessage(), { cause: error })); }
    });
  }

  private acceptHello(value: Record<string, unknown>): void {
    const expected = ["agent_tools_interface_version", "allow_exec", "allow_write", "capabilities", "engine_version", "schema_version", "transport_version"];
    if (Object.keys(value).length !== expected.length || expected.some((key) => !(key in value)) || value.agent_tools_interface_version !== AGENT_TOOLS_INTERFACE_VERSION || value.schema_version !== AGENT_TOOLS_SCHEMA_VERSION || value.transport_version !== AGENT_TOOLS_TRANSPORT_VERSION || value.engine_version !== SUPPORTED_AGENT_TOOLS_ENGINE_VERSION || value.allow_write !== this.permissions.write || value.allow_exec !== this.permissions.execute) throw new EngineProtocolError("Agent Tools hello is incompatible");
    if (!Array.isArray(value.capabilities) || value.capabilities.some((item) => typeof item !== "string")) throw new EngineProtocolError("Agent Tools capabilities are invalid");
    const capabilities = value.capabilities as string[];
    if (JSON.stringify(capabilities) !== JSON.stringify([...new Set(capabilities)].sort())) throw new EngineProtocolError("Agent Tools capabilities are not canonical");
    const expectedCapabilities = new Set(READ_TOOLS); if (this.permissions.write) for (const item of WRITE_TOOLS) expectedCapabilities.add(item); if (this.permissions.execute) for (const item of EXEC_TOOLS) expectedCapabilities.add(item);
    if (capabilities.length !== expectedCapabilities.size || capabilities.some((item) => !expectedCapabilities.has(item))) throw new EngineProtocolError("Agent Tools capabilities do not match policy");
    this.capabilitiesValue = Object.freeze([...capabilities]);
  }

  private errorFromWire(code: string, message: string): Error {
    if (code === "permission_denied") return new AgentPermissionError(message);
    if (code === "unsupported_capability") return new UnsupportedCapabilityError(message);
    if (code === "invalid_request" || code === "invalid_state" || code === "unsupported_interface") return new EngineProtocolError(message);
    return new EngineExecutionError(message);
  }
  private failPending(error: Error): void { for (const pending of this.pending.values()) pending.reject(error); this.pending.clear(); }
  /** Protocol violations are terminal: never continue on ambiguous state. */
  private protocolViolation(error: EngineProtocolError): void { this.failPending(error); void this.terminate(); }
  private crashMessage(): string { const detail = Buffer.concat(this.stderrChunks).toString("utf8").trim(); return detail ? `Agent Tools Engine exited: ${detail.slice(0, 4096)}` : "Agent Tools Engine exited"; }
  private removePolicy(): void { if (this.policyPath) { try { unlinkSync(this.policyPath); } catch { /* best effort */ } try { rmdirSync(resolve(this.policyPath, "..")); } catch { /* best effort */ } this.policyPath = null; } }
  private terminate(): Promise<void> {
    if (this.reapPromise) return this.reapPromise;
    this.closed = true;
    this.failPending(new EngineCrashed("AgentContext terminated"));
    const child = this.process;
    this.reapPromise = new Promise((resolvePromise) => {
      if (child === null || child.exitCode !== null || child.signalCode !== null) {
        this.removePolicy();
        resolvePromise();
        return;
      }
      child.once("close", () => { this.removePolicy(); resolvePromise(); });
      const pid = child.pid;
      try {
        if (pid && process.platform !== "win32") process.kill(-pid, "SIGKILL");
        else child.kill("SIGKILL");
      } catch {
        try { child.kill("SIGKILL"); } catch { /* already exited */ }
      }
    });
    return this.reapPromise;
  }
  private async reap(): Promise<void> { if (this.reapPromise) await this.reapPromise; else this.removePolicy(); }

  async call(tool: string, argumentsValue: Record<string, unknown> = {}): Promise<ToolResult> {
    if (typeof tool !== "string" || !tool) throw new ValidationError("tool must be a non-empty string");
    if (EXEC_TOOLS.has(tool)) throw new AgentPermissionError("execution tools must use run()");
    if (WRITE_TOOLS.has(tool) && !this.permissions.write) throw new AgentPermissionError("write permission is disabled");
    return this.callTool(tool, argumentsValue);
  }
  private async callTool(tool: string, argumentsValue: Record<string, unknown>, responseTimeout = this.timeout, signal?: AbortSignal): Promise<ToolResult> {
    if (!this.capabilitiesValue.includes(tool)) throw new UnsupportedCapabilityError(`Engine did not negotiate capability: ${tool}`);
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
      if (error instanceof EngineProtocolError) this.protocolViolation(error);
      throw error;
    }
    this.metricsValue = new AgentMetrics(this.metricsValue.toolCalls + 1, this.metricsValue.originalTokens + parsed.originalTokens, this.metricsValue.outputTokens + parsed.outputTokens, this.metricsValue.savedTokens + parsed.savedTokens);
    return parsed;
  }
  private parseToolResult(tool: string, value: Record<string, unknown>): ToolResult {
    const expected = ["text", "content_blocks", "original_tokens", "output_tokens", "saved_tokens", "mode", "changed", "shell"];
    if (Object.keys(value).length !== expected.length || expected.some((key) => !(key in value))) throw new EngineProtocolError("Agent Tools result fields are invalid");
    if (typeof value.text !== "string" || Buffer.byteLength(value.text) > MAX_TEXT_BYTES || (value.mode !== null && typeof value.mode !== "string")) throw new EngineProtocolError("Agent Tools text or mode is invalid");
    const numbers = [value.original_tokens, value.output_tokens, value.saved_tokens]; if (numbers.some((item) => typeof item !== "number" || !Number.isSafeInteger(item) || item < 0) || (value.output_tokens as number) + (value.saved_tokens as number) !== value.original_tokens) throw new EngineProtocolError("Agent Tools token metrics are invalid");
    if (typeof value.changed !== "boolean" || (value.shell !== null && (!value.shell || typeof value.shell !== "object" || Array.isArray(value.shell)))) throw new EngineProtocolError("Agent Tools status metadata is invalid");
    if (!Array.isArray(value.content_blocks) || value.content_blocks.some((item) => !item || typeof item !== "object" || Array.isArray(item))) throw new EngineProtocolError("Agent Tools content blocks are invalid");
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
    const cwd = options.cwd ?? "."; const absoluteCwd = resolve(this.projectRoot, cwd); if (absoluteCwd !== this.projectRoot && !absoluteCwd.startsWith(`${this.projectRoot}${sep}`)) return Promise.reject(new AgentPermissionError("cwd escapes project root"));
    const environment = options.env ?? {}; for (const [key, value] of Object.entries(environment)) { if (typeof value !== "string") return Promise.reject(new ValidationError("env must be a string mapping")); if (!this.executionPolicy.allowedEnv.includes(key)) return Promise.reject(new AgentPermissionError("environment variable is not allowed: " + key)); }
    return this.callTool("ctx_shell", { argv: [...argv], cwd, env: { ...environment }, timeout_ms: Math.trunc(timeout * 1000) }, Math.max(this.timeout, timeout + 2), options.signal);
  }
  async close(): Promise<void> { if (this.closed) { await this.reap(); return; } try { await this.exchangeRaw({ op: "close" }); } catch { /* terminal close remains best effort */ } await this.terminate(); }
  async cancel(): Promise<void> { await this.terminate(); }
  async reconnect(): Promise<AgentContext> { await this.close(); return AgentContext.open(this.projectRoot, { task: this.task, permissions: this.permissions, executionPolicy: this.executionPolicy, engineBinary: this.engineBinary, timeout: this.timeout }); }
}

export class AsyncAgentContext {
  private context: AgentContext | null = null;
  constructor(private readonly projectRoot: string, private readonly options: AgentContextOptions = {}) {}
  async open(): Promise<this> { if (!this.context) this.context = await AgentContext.open(this.projectRoot, this.options); return this; }
  private get current(): AgentContext { if (!this.context) throw new EngineUnavailable("AsyncAgentContext is not open"); return this.context; }
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
