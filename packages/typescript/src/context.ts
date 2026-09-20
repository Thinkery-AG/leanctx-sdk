// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/** Guarded authenticated Engine context-read client. */

import { request as httpRequest, type ClientRequest, type IncomingMessage, type RequestOptions } from "node:http";
import { request as httpsRequest } from "node:https";
import { isIP } from "node:net";
import {
  ConfigurationError,
  EngineProtocolError,
  EngineRejected,
  EngineTimeout,
  EngineUnavailable,
  PolicyAdmissionError,
  ValidationError,
} from "./errors.js";
import {
  canonicalBytes,
  MAX_PATH_BYTES,
  MAX_REQUEST_BYTES,
  MAX_RESPONSE_BYTES,
  MAX_TEXT_BYTES,
  strictJsonLoads,
  validateDigest,
  validateRef,
} from "./protocol.js";

const CONTEXT_READ_PATH = "/v1/tools/call";
const DEFAULT_TIMEOUT_SECONDS = 30;
const MAX_TIMEOUT_SECONDS = 120;
const MAX_CREDENTIAL_BYTES = 4096;
const MAX_URL_BYTES = 4096;

type JsonRecord = Record<string, unknown>;

export type EngineContextClientOptions = Readonly<{
  timeout?: number;
  allowLoopbackHttp?: boolean;
}>;

export type EngineContextReadResult = Readonly<{
  text: string;
  canonicalReceipt: Readonly<JsonRecord>;
  rawResponse: Readonly<JsonRecord>;
}>;

function record(value: unknown, label: string): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new EngineProtocolError(`${label} must be an object`);
  }
  return value as JsonRecord;
}

function validUtf8(value: string, label: string): number {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!Number.isFinite(next) || next < 0xdc00 || next > 0xdfff) {
        throw new ValidationError(`${label} is not valid UTF-8`);
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new ValidationError(`${label} is not valid UTF-8`);
    }
  }
  return Buffer.byteLength(value, "utf8");
}

export function _validateCredential(value: unknown): string {
  if (typeof value !== "string") {
    throw new ConfigurationError("credential must be a non-empty visible ASCII string");
  }
  let bytes: number;
  try {
    bytes = validUtf8(value, "credential");
  } catch (error) {
    throw new ConfigurationError("credential must be a non-empty visible ASCII string", { cause: error });
  }
  if (bytes === 0 || bytes > MAX_CREDENTIAL_BYTES || !/^[\x21-\x7e]+$/.test(value)) {
    throw new ConfigurationError("credential must be a non-empty visible ASCII string");
  }
  return value;
}

export function _validateTimeout(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0.1 || value > MAX_TIMEOUT_SECONDS) {
    throw new ConfigurationError("timeout must be between 0.1 and 120 seconds");
  }
  return value;
}

function isLoopbackLiteral(host: string): boolean {
  const normalized = host.startsWith("[") && host.endsWith("]") ? host.slice(1, -1) : host;
  if (isIP(normalized) === 6) return normalized === "::1";
  if (isIP(normalized) !== 4) return false;
  const first = Number(normalized.split(".")[0]);
  return first === 127;
}

export function _validateBaseUrl(
  value: unknown,
  allowLoopbackHttp: boolean,
  endpointPath = CONTEXT_READ_PATH,
): URL {
  if (typeof value !== "string" || value.length === 0) {
    throw new ConfigurationError("base_url must be a bounded absolute URL");
  }
  let bytes: number;
  try {
    bytes = validUtf8(value, "base_url");
  } catch (error) {
    throw new ConfigurationError("base_url must be a bounded absolute URL", { cause: error });
  }
  if (bytes > MAX_URL_BYTES) {
    throw new ConfigurationError("base_url must be a bounded absolute URL");
  }
  if ([...value].some((character) => /\s|\p{Cc}/u.test(character))) {
    throw new ConfigurationError("base_url must not contain whitespace or controls");
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch (error) {
    throw new ConfigurationError("base_url is not a valid URL", { cause: error });
  }
  if (parsed.username !== "" || parsed.password !== "") {
    throw new ConfigurationError("base_url must not contain userinfo");
  }
  if (parsed.search !== "" || parsed.hash !== "" || !["", "/"].includes(parsed.pathname)) {
    throw new ConfigurationError("base_url may contain only an optional root path");
  }
  const scheme = parsed.protocol.toLowerCase();
  if (scheme !== "https:" && scheme !== "http:") {
    throw new ConfigurationError("base_url must use HTTPS");
  }
  if (scheme === "http:" && (!allowLoopbackHttp || !isLoopbackLiteral(parsed.hostname))) {
    throw new ConfigurationError("loopback HTTP requires a literal loopback IP");
  }
  const port = parsed.port === "" ? (scheme === "https:" ? 443 : 80) : Number(parsed.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new ConfigurationError("base_url port is outside its bounds");
  }
  parsed.pathname = endpointPath;
  parsed.search = "";
  parsed.hash = "";
  return parsed;
}

function validatePath(value: unknown): string {
  if (typeof value !== "string") {
    throw new ValidationError("context-read path must be a non-empty string");
  }
  const bytes = validUtf8(value, "context-read path");
  if (bytes === 0 || bytes > MAX_PATH_BYTES) {
    throw new ValidationError("context-read path exceeds its byte bound");
  }
  if ([...value].some((character) => /\p{Cc}/u.test(character))) {
    throw new ValidationError("context-read path contains a control character");
  }
  return value;
}

function parseReceipt(value: unknown): Readonly<JsonRecord> {
  const receipt = record(value, "Engine context-read canonical receipt");
  const required = ["schema_version", "receipt_id", "receipt_ref", "receipt_digest", "outcome", "delivery"];
  const allowed = new Set([...required, "context_decision_ref", "outcome_observation_ref"]);
  if (Object.keys(receipt).some((key) => !allowed.has(key))) {
    throw new EngineProtocolError("context-read canonical receipt fields are unsupported");
  }
  for (const key of required) {
    if (!(key in receipt)) throw new EngineProtocolError(`context-read canonical receipt is missing ${key}`);
  }
  if (typeof receipt.schema_version !== "number" || !Number.isSafeInteger(receipt.schema_version) || receipt.schema_version !== 1) {
    throw new EngineProtocolError("context-read canonical receipt schema_version is unsupported");
  }
  let receiptRef: string;
  let receiptDigest: string;
  try {
    validateRef(receipt.receipt_id, "receipt_id");
    receiptRef = validateRef(receipt.receipt_ref, "receipt_ref");
    receiptDigest = validateDigest(receipt.receipt_digest, "receipt_digest");
  } catch (error) {
    throw new EngineProtocolError("context-read canonical receipt identity is invalid", { cause: error });
  }
  if (receiptRef !== `id:${receiptDigest}`) {
    throw new EngineProtocolError("context-read canonical receipt reference is not digest-bound");
  }
  if (receipt.outcome !== "unknown") {
    throw new EngineProtocolError("context-read canonical receipt outcome is unsupported");
  }
  if (receipt.delivery !== "native_engine_view") {
    throw new EngineProtocolError("context-read canonical receipt delivery is unsupported");
  }
  for (const key of ["context_decision_ref", "outcome_observation_ref"]) {
    if (key in receipt) {
      try {
        validateRef(receipt[key], key);
      } catch (error) {
        throw new EngineProtocolError(`context-read canonical receipt ${key} is invalid`, { cause: error });
      }
    }
  }
  return receipt;
}

export function parseContextReadResponse(raw: Uint8Array | string): EngineContextReadResult {
  let parsed: unknown;
  try {
    parsed = strictJsonLoads(raw, "Engine context-read response", [
      ["result", "_meta", "canonical_receipt", "schema_version"],
    ]);
  } catch (error) {
    throw new EngineProtocolError("Engine context-read response is not valid JSON", { cause: error });
  }
  const wrapper = record(parsed, "Engine context-read response wrapper");
  if (Object.keys(wrapper).length !== 1 || !("result" in wrapper)) {
    throw new EngineProtocolError("Engine context-read response wrapper is invalid");
  }
  const result = record(wrapper.result, "Engine context-read result");
  const isError = result.isError;
  if (isError !== undefined && isError !== null && typeof isError !== "boolean") {
    throw new EngineProtocolError("Engine context-read isError must be boolean");
  }
  if (isError === true) throw new EngineProtocolError("Engine context-read returned an error result");
  if (!Array.isArray(result.content) || result.content.length !== 1) {
    throw new EngineProtocolError("Engine context-read content must contain one item");
  }
  const content = record(result.content[0], "Engine context-read content");
  if (content.type !== "text") throw new EngineProtocolError("Engine context-read content must be text");
  if (typeof content.text !== "string") throw new EngineProtocolError("Engine context-read response text is not a string");
  let textBytes: number;
  try {
    textBytes = validUtf8(content.text, "Engine context-read response text");
  } catch (error) {
    throw new EngineProtocolError("Engine context-read response text is not valid UTF-8", { cause: error });
  }
  if (textBytes > MAX_TEXT_BYTES) throw new EngineProtocolError("Engine context-read response text exceeds its byte bound");
  const metadata = record(result._meta, "Engine context-read metadata");
  const canonicalReceipt = parseReceipt(metadata.canonical_receipt);
  return {
    text: content.text,
    canonicalReceipt,
    rawResponse: wrapper,
  };
}

function statusError(status: number, operation: string): Error | null {
  if (status >= 300 && status < 400) return new EngineProtocolError(`${operation} redirects are not followed`);
  if (status === 401) return new EngineRejected(`${operation} authentication was rejected`);
  if (status === 403) return new PolicyAdmissionError(`${operation} policy rejected the request`);
  if (status >= 500) return new EngineUnavailable(`${operation} returned a server error`);
  if (status < 200 || status >= 300) return new EngineRejected(`${operation} rejected the request`);
  return null;
}

function readResponse(
  response: IncomingMessage,
  deadline: number,
  maxBytes: number,
  operation: string,
): Promise<Buffer> {
  return new Promise((resolvePromise, rejectPromise) => {
    const chunks: Buffer[] = [];
    let total = 0;
    let settled = false;
    const finishError = (error: Error): void => {
      if (settled) return;
      settled = true;
      response.destroy();
      rejectPromise(error);
    };
    const finish = (value: Buffer): void => {
      if (settled) return;
      settled = true;
      resolvePromise(value);
    };
    response.on("data", (chunk: Buffer | string) => {
      if (settled) return;
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      total += bytes.byteLength;
      if (total > maxBytes) {
        finishError(new EngineProtocolError(`${operation} response exceeds its byte bound`));
        return;
      }
      chunks.push(bytes);
    });
    response.on("end", () => finish(Buffer.concat(chunks)));
    response.on("error", (error) => {
      if (Date.now() >= deadline) finishError(new EngineTimeout(`${operation} response exceeded its deadline`));
      else finishError(new EngineUnavailable(`${operation} response could not be read`, { cause: error }));
    });
  });
}

export function _postJson(
  url: URL,
  credential: string,
  payload: Buffer,
  timeoutSeconds: number,
  maxBytes = MAX_RESPONSE_BYTES,
  operation = "Engine context-read",
): Promise<Buffer> {
  return new Promise((resolvePromise, rejectPromise) => {
    const deadline = Date.now() + timeoutSeconds * 1000;
    let settled = false;
    let response: IncomingMessage | undefined;
    let request: ClientRequest | undefined;
    let timer: NodeJS.Timeout | undefined;
    const cleanup = (): void => {
      if (timer !== undefined) clearTimeout(timer);
    };
    const finishError = (error: Error): void => {
      if (settled) return;
      settled = true;
      cleanup();
      request?.destroy();
      response?.destroy();
      rejectPromise(error);
    };
    const finish = (value: Buffer): void => {
      if (settled) return;
      settled = true;
      cleanup();
      resolvePromise(value);
    };
    const remaining = (): number => Math.max(1, deadline - Date.now());
    const hostname = url.hostname.startsWith("[") && url.hostname.endsWith("]")
      ? url.hostname.slice(1, -1)
      : url.hostname;
    const requestOptions: RequestOptions = {
      protocol: url.protocol,
      hostname,
      port: url.port === "" ? undefined : Number(url.port),
      path: url.pathname,
      method: "POST",
      agent: false,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "Content-Length": String(payload.byteLength),
        Authorization: `Bearer ${credential}`,
        Connection: "close",
      },
    };
    const requestFunction: typeof httpRequest = url.protocol === "https:" ? (httpsRequest as typeof httpRequest) : httpRequest;
    request = requestFunction(requestOptions, (incoming) => {
      response = incoming;
      const error = statusError(incoming.statusCode ?? 0, operation);
      if (error !== null) {
        finishError(error);
        return;
      }
      const contentLength = incoming.headers["content-length"];
      if (contentLength !== undefined) {
        const declared = Number(contentLength);
        if (!Number.isSafeInteger(declared) || declared < 0 || declared > maxBytes) {
          finishError(new EngineProtocolError(`${operation} response exceeds its byte bound`));
          return;
        }
      }
      readResponse(incoming, deadline, maxBytes, operation).then(finish, finishError);
    });
    timer = setTimeout(() => finishError(new EngineTimeout(`${operation} request exceeded its deadline`)), timeoutSeconds * 1000);
    timer.unref?.();
    request.setTimeout(remaining(), () => finishError(new EngineTimeout(`${operation} request exceeded its deadline`)));
    request.on("error", (error: Error) => {
      if (settled) return;
      if (Date.now() >= deadline) finishError(new EngineTimeout(`${operation} request exceeded its deadline`));
      else finishError(new EngineUnavailable(`${operation} request failed`, { cause: error }));
    });
    request.end(payload);
  });
}

export class EngineContextClient {
  readonly baseUrl: string;
  readonly timeout: number;
  private readonly endpoint: URL;
  private readonly credential: string;

  constructor(baseUrl: string, credential: string, options: EngineContextClientOptions = {}) {
    if (typeof options.allowLoopbackHttp !== "undefined" && typeof options.allowLoopbackHttp !== "boolean") {
      throw new ConfigurationError("allowLoopbackHttp must be a boolean");
    }
    this.endpoint = _validateBaseUrl(baseUrl, options.allowLoopbackHttp ?? false);
    this.credential = _validateCredential(credential);
    this.timeout = _validateTimeout(options.timeout ?? DEFAULT_TIMEOUT_SECONDS);
    this.baseUrl = baseUrl;
  }

  async contextRead(path: string): Promise<EngineContextReadResult> {
    const checkedPath = validatePath(path);
    const payload = canonicalBytes({
      arguments: { engine_interface: "v1", mode: "aggressive", path: checkedPath },
      name: "ctx_read",
    });
    if (payload.byteLength > MAX_REQUEST_BYTES) {
      throw new EngineProtocolError("Engine context-read request exceeds its byte bound");
    }
    const raw = await _postJson(this.endpoint, this.credential, payload, this.timeout);
    return parseContextReadResponse(raw);
  }
}

export const _parse_context_read_response = parseContextReadResponse;
