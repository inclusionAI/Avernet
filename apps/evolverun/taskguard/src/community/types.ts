/**
 * TaskGuard extension point interfaces.
 *
 * Community code defines these interfaces; corp code provides implementations.
 * The registerTaskguardPlugin() function accepts optional extensions that
 * override community defaults.
 */

import type { IDatabase, DatabaseConfig } from "../db/types.js";

/**
 * Optional extensions that corp/internal code can provide.
 * Any field left undefined falls back to the community default.
 */
export interface TaskguardExtensions {
  /**
   * Create a database instance (e.g. ZDAS MySQL adapter).
   * Default: SQLite or NoOp.
   */
  createDatabase?: (config: DatabaseConfig) => Promise<IDatabase>;

  /**
   * Create an API client for remote workflow state management (e.g. clawweb).
   * Default: undefined (no remote API).
   */
  createApiClient?: (config: unknown) => unknown;

  /**
   * Create a notification dispatcher (e.g. enterprise DingTalk robot).
   * Default: basic DingTalk webhook.
   */
  createNotifier?: (config: unknown) => unknown;

  /**
   * Create an approval provider (e.g. internal approval system).
   * Default: basic DB-backed approval.
   */
  createApprovalProvider?: (config: unknown) => unknown;

  /**
   * Create knowledge adapters (e.g. YuQue, AgentMind).
   * Default: empty array (no external knowledge sources).
   */
  createKnowledgeAdapters?: (config: unknown) => unknown[];

  /**
   * Register additional executors (e.g. approval-card-web, approval-card-dingtalk).
   * Default: no additional executors.
   */
  registerExecutors?: (registry: unknown) => void;

  /**
   * Register additional callback auth methods (e.g. x-one-id IAM).
   * Default: HMAC only.
   */
  registerAuthMethods?: (registry: unknown) => void;

  /**
   * Start internal background pollers (e.g. approval card web poller).
   * Default: no background pollers.
   */
  startPollers?: (deps: unknown) => void;

  /**
   * Handle internal callback routes (e.g. dev-workflow-callback).
   * Default: community stub (no-op).
   */
  handleCallback?: (deps: unknown, params: unknown) => Promise<unknown>;
}
