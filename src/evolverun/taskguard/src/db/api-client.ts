/**
 * Community default `ApiClient` — implements the unified {@link IApiClient} contract
 * (defined in `./api-client/types.ts`). Makes real HTTP requests to the API's
 * `/api/internal/*` endpoints.
 *
 * Conditional Ed25519 signing:
 *   - When privateKeyB64 is configured, each request is signed with Ed25519:
 *     - Message = `${timestamp}.${JSON.stringify(body)}` (body = "" for GET/DELETE)
 *     - Signature = crypto.sign(null, Buffer.from(message), privateKey)
 *     - Headers: X-Signature (base64), X-Timestamp (epoch ms)
 *   - When privateKeyB64 is empty/undefined, requests are sent unsigned.
 *     This matches the server-side signatureMiddleware behavior: when
 *     EVOLVETRACE_INTERNAL_PUBLIC_KEY_B64 is not set, the middleware is a no-op
 *     and accepts all requests regardless of signing.
 *
 * Per the "interface in open source, implementation in each" pattern, this is the
 * community implementation; the enterprise (OCB) `CorpApiClient` implements the
 * same interface in the enterprise repo.
 */
import crypto from "node:crypto";
import https from "node:https";
import http from "node:http";
import type { IApiClient, ApiResponse } from "./api-client/types.js";

// ── Types ──

export type ApiClientConfig = {
  baseUrl?: string;
  privateKeyB64?: string;
  iamtoken?: string;
  iamtokenProvider?: () => Promise<string | undefined>;
  timeout?: number;
  maxRetries?: number;
  [k: string]: any;
};

/**
 * Re-export the unified `ApiResponse` envelope from the interface module so
 * that `ApiClient`'s method return types are exactly the interface type.
 * (Kept as a re-export for backward compatibility with consumers that import
 * `ApiResponse` from "@avernet/taskguard/db/api-client".)
 */
export type { ApiResponse } from "./api-client/types.js";

// ── Request type ──

type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

// ── Signature helpers ──

/**
 * Sign a request body with Ed25519.
 * Message format: `${timestamp}.${JSON.stringify(body)}`
 * For GET/DELETE (no body), message is `${timestamp}.`
 */
function signRequest(
  privateKeyB64: string,
  timestamp: number,
  body: any,
): { signature: string; timestamp: number } {
  const bodyStr = typeof body === "string" ? body : JSON.stringify(body);
  const message = `${timestamp}.${bodyStr}`;

  let privateKey: crypto.KeyObject;
  try {
    privateKey = crypto.createPrivateKey({
      key: Buffer.from(privateKeyB64, "base64"),
      format: "pem",
      type: "pkcs8",
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    throw new Error(`Failed to create Ed25519 private key: ${msg}`);
  }

  const signature = crypto.sign(null, Buffer.from(message), privateKey);
  return { signature: signature.toString("base64"), timestamp };
}

// ── HTTP helpers ──

/**
 * Parse a URL string and return protocol + host + path info for Node's http/https module.
 */
function parseUrl(url: string): {
  protocol: "http:" | "https:";
  host: string;
  path: string;
  hostname: string;
  port: number;
} {
  const parsed = new URL(url);
  const protocol = parsed.protocol as "http:" | "https:";
  const port = parsed.port ? parseInt(parsed.port, 10) : (protocol === "https:" ? 443 : 80);
  return {
    protocol,
    host: parsed.host,
    path: parsed.pathname + parsed.search,
    hostname: parsed.hostname,
    port,
  };
}

/**
 * Make an HTTP request using Node's built-in http/https modules.
 * Supports timeout and retry logic.
 */
function makeHttpRequest(
  method: HttpMethod,
  url: string,
  headers: Record<string, string>,
  body?: any,
  timeoutMs?: number,
  maxRetries?: number,
): Promise<{ status: number; data: any }> {
  return new Promise((resolve, reject) => {
    const parsed = parseUrl(url);
    const mod = parsed.protocol === "https:" ? https : http;

    const payload = body ? JSON.stringify(body) : undefined;

    const options: https.RequestOptions = {
      hostname: parsed.hostname,
      port: parsed.port,
      path: parsed.path,
      method,
      headers: {
        "Content-Type": "application/json",
        ...headers,
      },
      timeout: timeoutMs ?? 5000,
    };

    const req = mod.request(options, (res: http.IncomingMessage) => {
      const chunks: Buffer[] = [];

      res.on("data", (chunk: Buffer) => {
        chunks.push(chunk);
      });

      res.on("end", () => {
        const raw = Buffer.concat(chunks).toString("utf-8");
        let data: any;
        try {
          data = raw ? JSON.parse(raw) : {};
        } catch {
          data = { raw };
        }
        resolve({ status: res.statusCode ?? 0, data });
      });

      res.on("error", (err) => {
        reject(err);
      });
    });

    req.on("error", (err) => {
      reject(err);
    });

    req.on("timeout", () => {
      req.destroy();
      reject(new Error(`Request timeout after ${timeoutMs ?? 5000}ms`));
    });

    if (payload) {
      req.write(payload);
    }
    req.end();
  });
}

/**
 * Send a request with retry logic.
 */
async function sendWithRetry(
  method: HttpMethod,
  url: string,
  headers: Record<string, string>,
  body?: any,
  timeoutMs?: number,
  maxRetries?: number,
): Promise<{ status: number; data: any }> {
  let lastError: Error | undefined;
  const attempts = maxRetries ?? 3;

  for (let attempt = 0; attempt <= attempts; attempt++) {
    try {
      const result = await makeHttpRequest(method, url, headers, body, timeoutMs);
      // Retry on server errors (5xx)
      if (result.status >= 500 && attempt < attempts) {
        lastError = new Error(`HTTP ${result.status}: retrying`);
        // Exponential backoff: 100ms, 200ms, 400ms...
        await new Promise((r) => setTimeout(r, 100 * Math.pow(2, attempt)));
        continue;
      }
      return result;
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      if (attempt < attempts) {
        await new Promise((r) => setTimeout(r, 100 * Math.pow(2, attempt)));
      }
    }
  }

  throw lastError ?? new Error("Request failed after retries");
}

// ── ApiClient ──

export class ApiClient implements IApiClient {
  private baseUrl: string;
  private privateKeyB64?: string;
  private iamtoken?: string;
  private iamtokenProvider?: () => Promise<string | undefined>;
  private timeout: number;
  private maxRetries: number;

  constructor(config: ApiClientConfig | unknown) {
    const c = config as ApiClientConfig;
    this.baseUrl = c.baseUrl ?? "";
    this.privateKeyB64 = c.privateKeyB64 || undefined;
    this.iamtoken = c.iamtoken || undefined;
    this.iamtokenProvider = c.iamtokenProvider || undefined;
    this.timeout = c.timeout ?? 5000;
    this.maxRetries = c.maxRetries ?? 3;
  }

  /** Resolve the current iamtoken (static or from provider). */
  private async resolveIamtoken(): Promise<string | undefined> {
    if (this.iamtokenProvider) {
      return this.iamtokenProvider();
    }
    return this.iamtoken;
  }

  /** Build base headers for all requests. */
  private async buildHeaders(method: HttpMethod, body?: any): Promise<Record<string, string>> {
    const headers: Record<string, string> = {};
    const token = await this.resolveIamtoken();
    if (token) {
      headers["Cookie"] = `iamtoken=${token}`;
    }
    if (this.privateKeyB64) {
      const { signature, timestamp } = signRequest(this.privateKeyB64, Date.now(), body);
      headers["X-Signature"] = signature;
      headers["X-Timestamp"] = String(timestamp);
    }
    return headers;
  }

  /**
   * Parse server response envelope: { success, data, error, message }
   * Maps to our ApiResponse: { ok, data, status, error }
   */
  private parseResponse<T>(response: { status: number; data: any }): ApiResponse<T> {
    const status = response.status;
    const body = response.data;

    if (typeof body === "object" && body !== null && "success" in body) {
      return {
        ok: body.success === true,
        data: body.data as T,
        status,
        error: body.error ?? body.message ?? undefined,
      };
    }

    // Fallback: treat as success if status is 2xx
    return {
      ok: status >= 200 && status < 300,
      data: body as T,
      status,
      error: status >= 400 ? `HTTP ${status}` : undefined,
    };
  }

  async get<T = any>(path: string, query?: Record<string, string>): Promise<ApiResponse<T>> {
    const url = new URL(path, this.baseUrl);
    if (query) {
      Object.entries(query).forEach(([k, v]) => url.searchParams.append(k, v));
    }
    const headers = await this.buildHeaders("GET");
    const response = await sendWithRetry("GET", url.toString(), headers, undefined, this.timeout, this.maxRetries);
    return this.parseResponse<T>(response);
  }

  async post<T = any>(path: string, body?: any): Promise<ApiResponse<T>> {
    const url = new URL(path, this.baseUrl);
    const headers = await this.buildHeaders("POST", body);
    const response = await sendWithRetry("POST", url.toString(), headers, body, this.timeout, this.maxRetries);
    return this.parseResponse<T>(response);
  }

  async put<T = any>(path: string, body?: any): Promise<ApiResponse<T>> {
    const url = new URL(path, this.baseUrl);
    const headers = await this.buildHeaders("PUT", body);
    const response = await sendWithRetry("PUT", url.toString(), headers, body, this.timeout, this.maxRetries);
    return this.parseResponse<T>(response);
  }

  async delete<T = any>(path: string): Promise<ApiResponse<T>> {
    const url = new URL(path, this.baseUrl);
    const headers = await this.buildHeaders("DELETE");
    const response = await sendWithRetry("DELETE", url.toString(), headers, undefined, this.timeout, this.maxRetries);
    return this.parseResponse<T>(response);
  }

  /**
   * Helper for operations that return { ok: true/false } boolean (not { ok, data }).
   * Used by repositories that only care about success/failure.
   */
  async requestOk<T>(method: HttpMethod, path: string, body?: any): Promise<boolean> {
    const response = await this.requestRaw(method, path, body);
    return response.ok;
  }

  /**
   * Low-level: make a raw request and return the ApiResponse.
   * Does NOT parse the response envelope — returns raw server data.
   */
  async requestRaw<T = any>(method: HttpMethod, path: string, body?: any): Promise<ApiResponse<T>> {
    const url = new URL(path, this.baseUrl);
    const headers = await this.buildHeaders(method, body);
    const response = await sendWithRetry(method, url.toString(), headers, body, this.timeout, this.maxRetries);
    const parsed = this.parseResponse<T>(response);
    return parsed;
  }
}