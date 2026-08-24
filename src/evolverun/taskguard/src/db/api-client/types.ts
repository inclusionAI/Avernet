/**
 * Unified `IApiClient` contract + supporting types.
 *
 * This module lives in the open-source Avernet repo and defines the *interface*;
 * concrete implementations live in their own repos (per the
 * "interface in open source, implementation in each" pattern, same as
 * `TaskguardExtensions`):
 *
 *   - Avernet (community default): `../api-client.ts` — `ApiClient`
 *     (conditional signature: signs with Ed25519 only when `privateKeyB64`
 *     is configured, otherwise sends unsigned requests).
 *   - OCB (enterprise): enterprise repo's `api-client.ts` — `CorpApiClient`
 *     (always Ed25519-signed + IAM).
 *
 * All request/response shapes below are intentionally loose so that both
 * implementations can satisfy this interface without changing their
 * runtime behavior.
 */

/**
 * Loose per-request configuration passed alongside a path.
 * Kept optional and permissive so both Avernet and OCB implementations
 * (which today accept no per-request config) can satisfy the interface.
 */
export interface RequestConfig {
  /** Extra headers to merge into the outgoing request. */
  headers?: Record<string, string>;
  /** Per-request timeout in milliseconds. */
  timeout?: number;
  /** Per-request retry count. */
  retries?: number;
}

/**
 * Normalized HTTP response envelope, compatible with both sides:
 *
 *   - Avernet: `{ ok; data; status?; error? }` — always sets `data`
 *   - OCB:     `{ ok; status; data: T | null; error: string | null }` — always sets `data`
 *
 * Both implementations always populate `data` (with `null` when the upstream
 * produced no body), so it is declared non-optional here. `status` / `error`
 * are optional because some callers intentionally omit them.
 */
export interface ApiResponse<T = unknown> {
  ok: boolean;
  data: T | null;
  status?: number;
  error?: string | null;
}

/**
 * How requests are authenticated / signed.
 *
 *   - `"none"`         — unsigned (Avernet community default when no
 *                        `privateKeyB64` is provided).
 *   - `"ed25519-iam"`  — Ed25519-signed + IAM token (OCB enterprise).
 */
export type SignMode = "none" | "ed25519-iam";

/**
 * Config consumed by {@link createApiClient}.
 */
export interface ApiClientFactoryConfig {
  /** Base URL of the target API server. Required. */
  baseUrl: string;
  /** Signing mode. Defaults to "none". */
  signMode?: SignMode;
  /** Base64-encoded PKCS8 DER Ed25519 private key (used in "ed25519-iam"). */
  privateKeyB64?: string;
  /** Static IAM token for cookie-based auth. */
  iamtoken?: string;
  /** Dynamic IAM token provider, called per request (preferred over static). */
  iamtokenProvider?: () => Promise<string | undefined>;
  /** Request timeout in milliseconds. @default 5000 */
  timeout?: number;
  /** Maximum number of retries for transient errors. @default 3 */
  maxRetries?: number;
}

/**
 * Unified API client contract.
 *
 * Method signatures are intentionally broad so that BOTH implementations
 * satisfy it:
 *
 *   - `get` declares an optional second parameter (`queryOrConfig`). The Avernet
 *     community `ApiClient.get(path, query)` passes real query params; the OCB
 *     `CorpApiClient.get(path)` simply ignores it (TS allows implementers to
 *     accept fewer parameters than the interface).
 *   - `post` / `put` accept an optional body (`unknown`).
 *   - `delete` accepts only a path.
 */
export interface IApiClient {
  get<T = unknown>(
    path: string,
    queryOrConfig?: Record<string, string>,
  ): Promise<ApiResponse<T>>;
  post<T = unknown>(path: string, body?: unknown): Promise<ApiResponse<T>>;
  put<T = unknown>(path: string, body?: unknown): Promise<ApiResponse<T>>;
  delete<T = unknown>(path: string): Promise<ApiResponse<T>>;
}