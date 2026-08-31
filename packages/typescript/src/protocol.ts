/**
 * Clean-room Product values and strict Engine Interface v1 records.
 *
 * This module deliberately contains no subprocess or host integration logic.
 */

import { createHash } from "node:crypto";
import { isAbsolute, normalize, relative, resolve, sep } from "node:path";
import {
  EngineExecutionError,
  ValidationError,
} from "./errors.js";

export const SCHEMA_VERSION = 1 as const;
export const TRANSPORT_VERSION = 1 as const;
export const ENGINE_INTERFACE_VERSION = "1.0.0" as const;

export const MAX_REQUEST_BYTES = 64 * 1024;
export const MAX_PATH_BYTES = 4096;
export const MAX_REF_BYTES = 512;
export const MAX_TASK_BYTES = 16 * 1024;
export const MAX_TEXT_BYTES = 8 * 1024 * 1024;
export const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
export const MAX_STDERR_BYTES = 64 * 1024;
export const MAX_REFS = 32;
export const MAX_MEASUREMENTS = 32;

export enum FailureCode {
  POLICY_REJECTED = "policy_rejected",
  SOURCE_UNAVAILABLE = "source_unavailable",
  SOURCE_INTEGRITY_MISMATCH = "source_integrity_mismatch",
  RESOURCE_LIMIT = "resource_limit",
  UNSUPPORTED_OPERATION = "unsupported_operation",
  INTERNAL = "internal",
}

export enum SessionState {
  CREATED = "created",
  PLANNED = "planned",
  EXECUTING = "executing",
  COMPLETED = "completed",
  ABORTED = "aborted",
  CLOSED = "closed",
}

export enum EngineStatus {
  SUCCEEDED = "succeeded",
  DEGRADED = "degraded",
  REJECTED = "rejected",
  FAILED = "failed",
}

export enum HostOutcome {
  UNKNOWN = "unknown",
  ACCEPTED = "accepted",
  REJECTED = "rejected",
  COMPLETED = "completed",
  FAILED = "failed",
  ABORTED = "aborted",
}

export enum Integrity {
  SEALED = "sealed",
  UNSEALED = "unsealed",
}

export enum Freshness {
  REUSE = "reuse",
  REFRESH = "refresh",
}

type JsonPrimitive = null | boolean | number | string;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
const OUTPUT_REF_RE = /^output:[0-9a-f]{64}$/;
const PLAN_REF_RE = /^plan:sha256:[0-9a-f]{64}$/;
const ASCII_NAME_RE = /^[a-z0-9_]+$/;
const PRINTABLE_ASCII_RE = /^[ -~]+$/;
const SEMVER_RE = /^[0-9]+\.[0-9]+\.[0-9]+$/;

function utf8(value: unknown, fieldName: string): Buffer {
  if (typeof value !== "string") {
    throw new ValidationError(`${fieldName} must be a string`);
  }
  // Buffer silently replaces lone UTF-16 surrogates; reject them so hashes and
  // wire validation never depend on a replacement policy.
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) {
        throw new ValidationError(`${fieldName} is not valid UTF-8`);
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new ValidationError(`${fieldName} is not valid UTF-8`);
    }
  }
  return Buffer.from(value, "utf8");
}

function text(
  value: unknown,
  fieldName: string,
  maximum: number,
  controls = true,
): string {
  const encoded = utf8(value, fieldName);
  if (encoded.byteLength === 0) {
    throw new ValidationError(`${fieldName} must not be empty`);
  }
  if (encoded.byteLength > maximum) {
    throw new ValidationError(`${fieldName} exceeds ${maximum} UTF-8 bytes`);
  }
  const stringValue = value as string;
  if (stringValue.includes("\0")) {
    throw new ValidationError(`${fieldName} contains NUL`);
  }
  if (controls && [...stringValue].some((character) => character.charCodeAt(0) < 0x20)) {
    throw new ValidationError(`${fieldName} contains a control character`);
  }
  return stringValue;
}

export function validateRef(value: unknown, fieldName = "ref"): string {
  const encoded = utf8(value, fieldName);
  const stringValue = value as string;
  if (
    encoded.byteLength === 0 ||
    encoded.byteLength > MAX_REF_BYTES ||
    !PRINTABLE_ASCII_RE.test(stringValue)
  ) {
    throw new ValidationError(`${fieldName} must be 1..${MAX_REF_BYTES} printable ASCII bytes`);
  }
  return stringValue;
}

export function validateDigest(value: unknown, fieldName = "digest"): string {
  const stringValue = validateRef(value, fieldName);
  if (!DIGEST_RE.test(stringValue)) {
    throw new ValidationError(`${fieldName} must be sha256:<64 lowercase hex>`);
  }
  return stringValue;
}

export function validateOutputRef(value: unknown, fieldName = "output_ref"): string {
  const stringValue = validateRef(value, fieldName);
  if (!OUTPUT_REF_RE.test(stringValue)) {
    throw new ValidationError(`${fieldName} must be output:<64 lowercase hex>`);
  }
  return stringValue;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function canonicalValue(value: unknown, stack: Set<unknown>): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    // Cross-language SDK hashes use the portable JSON integer domain.  JS
    // cannot distinguish 1 from 1.0, while Python serializes them differently;
    // rejecting non-integers and negative zero keeps every accepted value
    // byte-identical across runtimes.
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) throw new ValidationError("canonical JSON numbers must be safe integers");
    return value;
  }
  if (typeof value === "bigint" || typeof value === "undefined" || typeof value === "function" || typeof value === "symbol") {
    throw new ValidationError("value is not canonical JSON data");
  }
  if (stack.has(value)) {
    throw new ValidationError("value is not canonical JSON data");
  }
  stack.add(value);
  try {
    if (Array.isArray(value)) {
      return value.map((item) => canonicalValue(item, stack));
    }
    if (!isPlainObject(value)) {
      throw new ValidationError("value is not canonical JSON data");
    }
    const result: { [key: string]: JsonValue } = {};
    for (const key of Object.keys(value).sort(compareUnicodeCodePoints)) {
      utf8(key, "canonical JSON key");
      result[key] = canonicalValue(value[key], stack);
    }
    return result;
  } finally {
    stack.delete(value);
  }
}

function compareUnicodeCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) as number);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) as number);
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    const difference = (leftPoints[index] as number) - (rightPoints[index] as number);
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}

/** Return the canonical JSON representation used for Product hashes. */
export function canonicalJson(value: unknown): string {
  const normalized = canonicalValue(value, new Set());
  return JSON.stringify(normalized);
}

export function canonicalBytes(value: unknown): Buffer {
  return Buffer.from(canonicalJson(value), "utf8");
}

export function sha256Digest(data: Uint8Array | string): string {
  return `sha256:${createHash("sha256").update(data).digest("hex")}`;
}

/**
 * Strict JSON parser that rejects duplicate object keys, non-finite constants,
 * malformed UTF-8, and trailing input. JSON.parse alone cannot detect keys.
 */
export function strictJsonLoads(input: Uint8Array | string, label = "JSON"): unknown {
  let source: string;
  try {
    if (typeof input === "string") {
      utf8(input, label);
      source = input;
    } else {
      source = new TextDecoder("utf-8", { fatal: true }).decode(input);
    }
  } catch (error) {
    throw new ValidationError(`invalid ${label}`, { cause: error });
  }
  const parser = new JsonParser(source, label);
  const result = parser.parse();
  if (!isPlainObject(result)) {
    throw new ValidationError(`${label} must be a JSON object`);
  }
  return result;
}

class JsonParser {
  private index = 0;

  constructor(private readonly source: string, private readonly label: string) {}

  parse(): unknown {
    this.skipWhitespace();
    const value = this.parseValue();
    this.skipWhitespace();
    if (this.index !== this.source.length) this.fail("trailing data");
    return value;
  }

  private parseValue(): unknown {
    const character = this.source[this.index];
    if (character === undefined) this.fail("unexpected end of input");
    if (character === "{") return this.parseObject();
    if (character === "[") return this.parseArray();
    if (character === '"') return this.parseString();
    if (character === "t" && this.take("true")) return true;
    if (character === "f" && this.take("false")) return false;
    if (character === "n" && this.take("null")) return null;
    if (character === "-" || (character >= "0" && character <= "9")) return this.parseNumber();
    this.fail("invalid value");
  }

  private parseObject(): Record<string, unknown> {
    this.index += 1;
    const result: Record<string, unknown> = {};
    const keys = new Set<string>();
    this.skipWhitespace();
    if (this.source[this.index] === "}") {
      this.index += 1;
      return result;
    }
    while (true) {
      this.skipWhitespace();
      if (this.source[this.index] !== '"') this.fail("object key must be a string");
      const key = this.parseString();
      if (keys.has(key)) this.fail(`duplicate key: ${key}`);
      keys.add(key);
      this.skipWhitespace();
      if (this.source[this.index] !== ":") this.fail("object key missing colon");
      this.index += 1;
      this.skipWhitespace();
      result[key] = this.parseValue();
      this.skipWhitespace();
      const separator = this.source[this.index];
      if (separator === "}") {
        this.index += 1;
        return result;
      }
      if (separator !== ",") this.fail("object missing comma");
      this.index += 1;
    }
  }

  private parseArray(): unknown[] {
    this.index += 1;
    const result: unknown[] = [];
    this.skipWhitespace();
    if (this.source[this.index] === "]") {
      this.index += 1;
      return result;
    }
    while (true) {
      this.skipWhitespace();
      result.push(this.parseValue());
      this.skipWhitespace();
      const separator = this.source[this.index];
      if (separator === "]") {
        this.index += 1;
        return result;
      }
      if (separator !== ",") this.fail("array missing comma");
      this.index += 1;
    }
  }

  private parseString(): string {
    this.index += 1;
    let result = "";
    while (this.index < this.source.length) {
      const character = this.source[this.index++];
      if (character === undefined) this.fail("unterminated string");
      if (character === '"') return result;
      if (character === "\\") {
        const escape = this.source[this.index++];
        if (escape === undefined) this.fail("unterminated string escape");
        const escapes: Record<string, string> = {
          '"': '"',
          "\\": "\\",
          "/": "/",
          b: "\b",
          f: "\f",
          n: "\n",
          r: "\r",
          t: "\t",
        };
        if (escape === "u") {
          const hex = this.source.slice(this.index, this.index + 4);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) this.fail("invalid unicode escape");
          result += String.fromCharCode(Number.parseInt(hex, 16));
          this.index += 4;
        } else if (escape in escapes) {
          result += escapes[escape];
        } else {
          this.fail("invalid string escape");
        }
      } else {
        if (character.charCodeAt(0) < 0x20) this.fail("control character in string");
        result += character;
      }
    }
    this.fail("unterminated string");
  }

  private parseNumber(): number {
    const match = this.source.slice(this.index).match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
    if (!match) this.fail("invalid number");
    const value = Number(match[0]);
    if (!Number.isFinite(value)) this.fail("number is not finite");
    this.index += match[0].length;
    return value;
  }

  private take(value: string): boolean {
    if (this.source.slice(this.index, this.index + value.length) !== value) return false;
    this.index += value.length;
    return true;
  }

  private skipWhitespace(): void {
    while (this.index < this.source.length) {
      const character = this.source[this.index];
      if (character === undefined || !/[ \t\r\n]/.test(character)) break;
      this.index += 1;
    }
  }

  private fail(message: string): never {
    throw new ValidationError(`invalid ${this.label}: ${message}`);
  }
}

export function exactKeys(
  value: unknown,
  expected: ReadonlySet<string> | readonly string[],
  label: string,
): asserts value is Record<string, unknown> {
  if (!isPlainObject(value)) throw new ValidationError(`${label} fields do not match the v1 contract`);
  const expectedSet = expected instanceof Set ? expected : new Set(expected);
  const actual = Object.keys(value);
  if (actual.length !== expectedSet.size || actual.some((key) => !expectedSet.has(key))) {
    throw new ValidationError(`${label} fields do not match the v1 contract`);
  }
}

function freezeValue<T>(value: T): T {
  if (value && typeof value === "object") {
    for (const nested of Object.values(value as Record<string, unknown>)) {
      freezeValue(nested);
    }
    Object.freeze(value);
  }
  return value;
}

function plain(value: unknown): JsonValue {
  if (value instanceof ContextSource || value instanceof ContextPlan || value instanceof ContextMeasurement || value instanceof ContextFailure || value instanceof ContextReceiptLink || value instanceof RecoveredSource || value instanceof ContextView) {
    return plain(value.toDict());
  }
  if (value instanceof Map) {
    const result: Record<string, JsonValue> = {};
    for (const [key, item] of value.entries()) result[String(key)] = plain(item);
    return result;
  }
  if (Array.isArray(value)) return value.map((item) => plain(item));
  if (value && typeof value === "object") {
    const result: Record<string, JsonValue> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) result[key] = plain(item);
    return result;
  }
  if (typeof value === "bigint" || typeof value === "undefined" || typeof value === "function" || typeof value === "symbol") {
    throw new ValidationError("value is not deterministic JSON data");
  }
  return value as JsonPrimitive;
}

function contained(candidate: string, root: string): boolean {
  const rel = relative(root, candidate);
  return rel === "" || (rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel));
}

export type ContextSourceOptions = Readonly<{
  projectRoot?: string;
  mediaType?: string;
  sourceRef?: string;
  sourceDigest?: string;
}>;

export class ContextSource {
  readonly path: string;
  readonly projectRoot: string;
  readonly mediaType: string;
  readonly sourceRef: string | undefined;
  readonly sourceDigest: string | undefined;

  constructor(pathValue: string, options: ContextSourceOptions = {}) {
    const suppliedPath = text(pathValue, "path", MAX_PATH_BYTES);
    const root = resolve(options.projectRoot ?? process.cwd());
    if (Buffer.byteLength(root, "utf8") > MAX_PATH_BYTES) throw new ValidationError("project_root exceeds the path bound");
    const candidate = resolve(root, suppliedPath);
    if (!contained(candidate, root)) throw new ValidationError("source path escapes project_root");
    const storedPath = isAbsolute(suppliedPath) ? normalize(candidate) : normalize(suppliedPath).split(sep).join("/");
    if (Buffer.byteLength(candidate, "utf8") > MAX_PATH_BYTES) throw new ValidationError("path exceeds the path bound");
    const mediaType = text(options.mediaType ?? "text/plain", "media_type", MAX_REF_BYTES);
    const sourceRef = options.sourceRef === undefined ? undefined : validateRef(options.sourceRef, "source_ref");
    const sourceDigest = options.sourceDigest === undefined ? undefined : validateDigest(options.sourceDigest, "source_digest");
    this.path = storedPath;
    this.projectRoot = root;
    this.mediaType = mediaType;
    this.sourceRef = sourceRef;
    this.sourceDigest = sourceDigest;
    Object.freeze(this);
  }

  get relativePath(): string {
    const absolutePath = resolve(this.projectRoot, this.path);
    if (!contained(absolutePath, this.projectRoot)) throw new ValidationError("source containment cannot be proven");
    const value = relative(this.projectRoot, absolutePath).split(sep).join("/");
    if (!value || value === "." || value === ".." || value.startsWith("../") || /[\0\u0001-\u001f]/.test(value)) {
      throw new ValidationError("source path must be a rooted relative file path");
    }
    return value;
  }

  get relative_path(): string { return this.relativePath; }
  get project_root(): string { return this.projectRoot; }
  get media_type(): string { return this.mediaType; }
  get source_ref(): string | undefined { return this.sourceRef; }
  get source_digest(): string | undefined { return this.sourceDigest; }

  descriptor(): Record<string, JsonValue> {
    const result: Record<string, JsonValue> = { path: this.relativePath, media_type: this.mediaType };
    if (this.sourceRef !== undefined) result.source_ref = this.sourceRef;
    if (this.sourceDigest !== undefined) result.source_digest = this.sourceDigest;
    return result;
  }

  toDict(): Record<string, JsonValue> {
    return { ...this.descriptor(), project_root: this.projectRoot };
  }

  to_dict(): Record<string, JsonValue> { return this.toDict(); }
}

export type ContextPlanOptions = Readonly<{
  mode?: string;
  freshness?: Freshness | string;
}>;

export class ContextPlan {
  readonly sessionId: string;
  readonly taskId: string;
  readonly task: string;
  readonly source: ContextSource;
  readonly mode: string;
  readonly freshness: string;
  readonly planId: string;

  constructor(
    sessionId: string,
    taskId: string,
    taskValue: string,
    source: ContextSource,
    options: ContextPlanOptions = {},
  ) {
    this.sessionId = text(sessionId, "session_id", MAX_REF_BYTES);
    this.taskId = text(taskId, "task_id", MAX_REF_BYTES);
    this.task = text(taskValue, "task", MAX_TASK_BYTES, false);
    if (!(source instanceof ContextSource)) throw new ValidationError("source must be ContextSource");
    this.source = source;
    this.mode = options.mode ?? "aggressive";
    this.freshness = options.freshness ?? Freshness.REUSE;
    if (this.mode !== "aggressive") throw new ValidationError("mode must be aggressive in Engine Interface v1");
    if (this.freshness !== Freshness.REUSE && this.freshness !== Freshness.REFRESH) throw new ValidationError("freshness must be reuse or refresh");
    this.planId = sha256Digest(canonicalBytes(this.toIntent())).replace(/^sha256:/, "plan:sha256:");
    Object.freeze(this);
  }

  toIntent(): Record<string, JsonValue> {
    return {
      intent_version: 1,
      session_id: this.sessionId,
      task_id: this.taskId,
      task: this.task,
      source: this.source.descriptor(),
      mode: this.mode,
      freshness: this.freshness,
    };
  }

  toDict(): Record<string, JsonValue> {
    return { ...this.toIntent(), plan_id: this.planId };
  }

  get session_id(): string { return this.sessionId; }
  get task_id(): string { return this.taskId; }
  get plan_id(): string { return this.planId; }
  to_intent(): Record<string, JsonValue> { return this.toIntent(); }
  to_dict(): Record<string, JsonValue> { return this.toDict(); }
}

export class ContextMeasurement {
  readonly name: string;
  readonly unit: string;
  readonly classification: "measured" | "estimated" | "unavailable";
  readonly value: number | null;

  constructor(name: string, unit: string, classification: string, value: number | null) {
    if (typeof name !== "string" || !ASCII_NAME_RE.test(name)) throw new ValidationError("measurement name must be lowercase ASCII");
    if (typeof unit !== "string" || !ASCII_NAME_RE.test(unit)) throw new ValidationError("measurement unit must be lowercase ASCII");
    if (classification !== "measured" && classification !== "estimated" && classification !== "unavailable") throw new ValidationError("invalid measurement classification");
    if (classification === "unavailable") {
      if (value !== null) throw new ValidationError("unavailable measurement value must be null");
    } else if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
      throw new ValidationError("measurement value must be a non-negative integer");
    }
    this.name = name;
    this.unit = unit;
    this.classification = classification;
    this.value = value;
    Object.freeze(this);
  }

  toDict(): Record<string, JsonValue> {
    return { name: this.name, unit: this.unit, classification: this.classification, value: this.value };
  }
  to_dict(): Record<string, JsonValue> { return this.toDict(); }
}

export class ContextFailure {
  readonly code: FailureCode;
  readonly retryableByHost: boolean;
  readonly recoveryRef: string | null;

  constructor(code: FailureCode | string, retryableByHost: boolean, recoveryRef: string | null) {
    const normalized = String(code);
    if (!Object.values(FailureCode).includes(normalized as FailureCode)) throw new ValidationError("invalid failure code");
    if (typeof retryableByHost !== "boolean") throw new ValidationError("retryable_by_host must be boolean");
    if (recoveryRef !== null) validateRef(recoveryRef, "recovery_ref");
    this.code = normalized as FailureCode;
    this.retryableByHost = retryableByHost;
    this.recoveryRef = recoveryRef;
    Object.freeze(this);
  }

  toDict(): Record<string, JsonValue> {
    return { code: this.code, retryable_by_host: this.retryableByHost, recovery_ref: this.recoveryRef };
  }
  get retryable_by_host(): boolean { return this.retryableByHost; }
  get recovery_ref(): string | null { return this.recoveryRef; }
  to_dict(): Record<string, JsonValue> { return this.toDict(); }
}

export class ContextReceiptLink {
  readonly schemaVersion: number;
  readonly receiptId: string;
  readonly receiptRef: string;
  readonly receiptDigest: string;
  readonly invocationId: string;

  constructor(schemaVersion: number, receiptId: string, receiptRef: string, receiptDigest: string, invocationId: string) {
    if (schemaVersion !== SCHEMA_VERSION || !Number.isInteger(schemaVersion)) throw new ValidationError("receipt link schema_version must be 1");
    this.schemaVersion = schemaVersion;
    this.receiptId = validateRef(receiptId, "receipt_id");
    this.receiptRef = validateRef(receiptRef, "receipt_ref");
    this.receiptDigest = validateDigest(receiptDigest, "receipt_digest");
    this.invocationId = text(invocationId, "invocation_id", MAX_REF_BYTES);
    if (this.receiptRef !== `receipt:${this.receiptDigest}`) throw new ValidationError("receipt_ref does not match receipt_digest");
    Object.freeze(this);
  }

  toDict(): Record<string, JsonValue> {
    return { schema_version: this.schemaVersion, receipt_id: this.receiptId, receipt_ref: this.receiptRef, receipt_digest: this.receiptDigest, invocation_id: this.invocationId };
  }
  get schema_version(): number { return this.schemaVersion; }
  get receipt_id(): string { return this.receiptId; }
  get receipt_ref(): string { return this.receiptRef; }
  get receipt_digest(): string { return this.receiptDigest; }
  get invocation_id(): string { return this.invocationId; }
  to_dict(): Record<string, JsonValue> { return this.toDict(); }
}

export class RecoveredSource {
  readonly text: string;
  readonly sourceRef: string;
  readonly sourceDigest: string;
  readonly recoveryRef: string;

  constructor(textValue: string, sourceRef: string, sourceDigest: string, recoveryRef: string) {
    this.text = text(textValue, "recovered text", MAX_TEXT_BYTES, false);
    this.sourceRef = validateRef(sourceRef, "source_ref");
    this.sourceDigest = validateDigest(sourceDigest, "source_digest");
    this.recoveryRef = validateRef(recoveryRef, "recovery_ref");
    if (sha256Digest(Buffer.from(this.text, "utf8")) !== this.sourceDigest) throw new ValidationError("recovered text digest does not match source_digest");
    Object.freeze(this);
  }

  toDict(): Record<string, JsonValue> {
    return { text: this.text, source_ref: this.sourceRef, source_digest: this.sourceDigest, recovery_ref: this.recoveryRef };
  }
  get source_ref(): string { return this.sourceRef; }
  get source_digest(): string { return this.sourceDigest; }
  get recovery_ref(): string { return this.recoveryRef; }
  to_dict(): Record<string, JsonValue> { return this.toDict(); }
}

export type ContextViewOptions = Readonly<{
  source: ContextSource;
  text: string | null;
  outputRef: string | null;
  outputDigest: string | null;
  sourceRef: string;
  sourceDigest: string;
  recoveryRef: string | null;
  status: EngineStatus | string;
  measurements?: readonly ContextMeasurement[];
  failure?: ContextFailure | null;
  receiptLink?: ContextReceiptLink | null;
  invocation?: Record<string, unknown>;
  observation?: Record<string, unknown>;
  schemaVersion?: number;
  transportVersion?: number;
  engineInterfaceVersion?: string;
}>;

export class ContextView {
  readonly source: ContextSource;
  readonly text: string | null;
  readonly outputRef: string | null;
  readonly outputDigest: string | null;
  readonly sourceRef: string;
  readonly sourceDigest: string;
  readonly recoveryRef: string | null;
  readonly status: string;
  readonly measurements: readonly ContextMeasurement[];
  readonly failure: ContextFailure | null;
  readonly receiptLink: ContextReceiptLink | null;
  readonly invocation: Readonly<Record<string, unknown>>;
  readonly observation: Readonly<Record<string, unknown>>;
  readonly schemaVersion: number;
  readonly transportVersion: number;
  readonly engineInterfaceVersion: string;

  constructor(options: ContextViewOptions);
  constructor(
    source: ContextSource,
    text: string | null,
    outputRef: string | null,
    outputDigest: string | null,
    sourceRef: string,
    sourceDigest: string,
    recoveryRef: string | null,
    status: EngineStatus | string,
    measurements?: readonly ContextMeasurement[],
    failure?: ContextFailure | null,
    receiptLink?: ContextReceiptLink | null,
    invocation?: Record<string, unknown>,
    observation?: Record<string, unknown>,
  );
  constructor(first: ContextViewOptions | ContextSource, ...rest: unknown[]) {
    const options: ContextViewOptions = first instanceof ContextSource
      ? {
        source: first,
        text: rest[0] as string | null,
        outputRef: rest[1] as string | null,
        outputDigest: rest[2] as string | null,
        sourceRef: rest[3] as string,
        sourceDigest: rest[4] as string,
        recoveryRef: rest[5] as string | null,
        status: rest[6] as string,
        measurements: rest[7] as readonly ContextMeasurement[] | undefined,
        failure: rest[8] as ContextFailure | null | undefined,
        receiptLink: rest[9] as ContextReceiptLink | null | undefined,
        invocation: rest[10] as Record<string, unknown> | undefined,
        observation: rest[11] as Record<string, unknown> | undefined,
      }
      : first;
    if (!(options.source instanceof ContextSource)) throw new ValidationError("view source must be ContextSource");
    if (options.text !== null) {
      utf8(options.text, "view text");
      if (Buffer.byteLength(options.text, "utf8") > MAX_TEXT_BYTES) throw new ValidationError("view text exceeds the bound");
    }
    if (options.outputRef !== null) validateOutputRef(options.outputRef);
    if (options.outputDigest !== null) validateDigest(options.outputDigest, "output_digest");
    if ((options.outputRef === null) !== (options.outputDigest === null)) throw new ValidationError("output_ref and output_digest must be paired");
    if (options.outputDigest !== null && options.text !== null) {
      if (sha256Digest(options.text) !== options.outputDigest) throw new ValidationError("view output digest mismatch");
      if (options.outputRef !== `output:${options.outputDigest.slice("sha256:".length)}`) throw new ValidationError("view output reference mismatch");
    }
    const sourceRef = validateRef(options.sourceRef, "source_ref");
    const sourceDigest = validateDigest(options.sourceDigest, "source_digest");
    if (options.recoveryRef !== null) validateRef(options.recoveryRef, "recovery_ref");
    if (!Object.values(EngineStatus).includes(options.status as EngineStatus)) throw new ValidationError("invalid Engine observation status");
    const measurements = Object.freeze([...(options.measurements ?? [])]);
    if (measurements.length > MAX_MEASUREMENTS || measurements.some((item) => !(item instanceof ContextMeasurement))) throw new ValidationError("invalid measurements");
    if (options.failure !== undefined && options.failure !== null && !(options.failure instanceof ContextFailure)) throw new ValidationError("failure must be ContextFailure");
    if (options.receiptLink !== undefined && options.receiptLink !== null && !(options.receiptLink instanceof ContextReceiptLink)) throw new ValidationError("receipt_link must be ContextReceiptLink");
    const invocation = freezeValue({ ...(options.invocation ?? {}) });
    const observation = freezeValue({ ...(options.observation ?? {}) });
    const schemaVersion = options.schemaVersion ?? SCHEMA_VERSION;
    const transportVersion = options.transportVersion ?? TRANSPORT_VERSION;
    const engineInterfaceVersion = options.engineInterfaceVersion ?? ENGINE_INTERFACE_VERSION;
    if (schemaVersion !== SCHEMA_VERSION || !Number.isInteger(schemaVersion)) throw new ValidationError("view schema_version must be 1");
    if (transportVersion !== TRANSPORT_VERSION || !Number.isInteger(transportVersion)) throw new ValidationError("view transport_version must be integer 1");
    if (engineInterfaceVersion !== ENGINE_INTERFACE_VERSION) throw new ValidationError("unsupported Engine Interface version");
    this.source = options.source;
    this.text = options.text;
    this.outputRef = options.outputRef;
    this.outputDigest = options.outputDigest;
    this.sourceRef = sourceRef;
    this.sourceDigest = sourceDigest;
    this.recoveryRef = options.recoveryRef;
    this.status = options.status;
    this.measurements = measurements;
    this.failure = options.failure ?? null;
    this.receiptLink = options.receiptLink ?? null;
    this.invocation = invocation;
    this.observation = observation;
    this.schemaVersion = schemaVersion;
    this.transportVersion = transportVersion;
    this.engineInterfaceVersion = engineInterfaceVersion;
    Object.freeze(this);
  }

  get integrityStatus(): Integrity {
    return this.verify() ? Integrity.SEALED : Integrity.UNSEALED;
  }

  get integrity_status(): Integrity { return this.integrityStatus; }
  get output_ref(): string | null { return this.outputRef; }
  get output_digest(): string | null { return this.outputDigest; }
  get source_ref(): string { return this.sourceRef; }
  get source_digest(): string { return this.sourceDigest; }
  get recovery_ref(): string | null { return this.recoveryRef; }
  get invocation_id(): string | null { return this.invocationId; }

  get inputRef(): string | null {
    const value = this.invocation.input_ref;
    return typeof value === "string" ? value : null;
  }

  get invocationId(): string | null {
    const value = this.invocation.invocation_id;
    return typeof value === "string" ? value : null;
  }

  get engineVersion(): string | null {
    const engine = this.invocation.engine;
    const value = isPlainObject(engine) ? engine.engine_version : undefined;
    return typeof value === "string" ? value : null;
  }

  get capabilityVersion(): string | null {
    const operation = this.invocation.operation;
    const value = isPlainObject(operation) ? operation.capability_version : undefined;
    return typeof value === "string" ? value : null;
  }

  requireText(): string {
    if (this.text === null) throw new EngineExecutionError("Engine view has no text", { view: this });
    return this.text;
  }

  require_text(): string { return this.requireText(); }

  recoveryBinding(): Record<string, string> {
    if (this.recoveryRef === null) throw new ValidationError("view has no recovery binding");
    return { recovery_ref: this.recoveryRef, source_ref: this.sourceRef, source_digest: this.sourceDigest };
  }

  recovery_binding(): Record<string, string> { return this.recoveryBinding(); }

  verify(): boolean {
    try {
      if (this.status !== EngineStatus.SUCCEEDED && this.status !== EngineStatus.DEGRADED) return false;
      if (this.recoveryRef === null || this.outputRef === null || this.outputDigest === null || this.text === null) return false;
      const sourceRefs = this.invocation.source_refs;
      if (!Array.isArray(sourceRefs) || !sourceRefs.includes(this.sourceRef)) return false;
      if (this.observation.invocation_id !== this.invocationId || this.observation.output_digest !== this.outputDigest || this.observation.output_ref !== this.outputRef) return false;
      if (this.receiptLink === null || this.receiptLink.invocationId !== this.invocationId) return false;
      return true;
    } catch {
      return false;
    }
  }

  toDict(): Record<string, JsonValue> {
    return {
      schema_version: this.schemaVersion,
      transport_version: this.transportVersion,
      engine_interface_version: this.engineInterfaceVersion,
      source: this.source.toDict(),
      text: this.text,
      output_ref: this.outputRef,
      output_digest: this.outputDigest,
      source_ref: this.sourceRef,
      source_digest: this.sourceDigest,
      recovery_ref: this.recoveryRef,
      status: this.status,
      measurements: this.measurements.map((item) => item.toDict()),
      failure: this.failure?.toDict() ?? null,
      receipt_link: this.receiptLink?.toDict() ?? null,
      invocation: plain(this.invocation),
      observation: plain(this.observation),
    };
  }
  to_dict(): Record<string, JsonValue> { return this.toDict(); }
}

export function planRef(value: string): string {
  if (!PLAN_REF_RE.test(value)) throw new ValidationError("plan_id must be a deterministic plan reference");
  return value;
}

export function isSemver(value: unknown): value is string {
  return typeof value === "string" && SEMVER_RE.test(value);
}

export { _plainCompat };
function _plainCompat(value: unknown): JsonValue {
  return plain(value);
}
