/**
 * Unified API client contract (interface in open source).
 *
 * Public surface:
 *   - {@link IApiClient}            — the unified contract
 *   - {@link ApiResponse}           — normalized response envelope
 *   - {@link RequestConfig}         — loose per-request config
 *   - {@link SignMode}              — "none" | "ed25519-iam"
 *   - {@link ApiClientFactoryConfig}— config consumed by the factory
 *   - {@link createApiClient}       — factory (community `ApiClient` or throws
 *                                     for the enterprise `ed25519-iam` path)
 */
export type { IApiClient } from "./types.js";
export type { ApiResponse } from "./types.js";
export type { RequestConfig } from "./types.js";
export type { SignMode } from "./types.js";
export type { ApiClientFactoryConfig } from "./types.js";
export { createApiClient } from "./factory.js";