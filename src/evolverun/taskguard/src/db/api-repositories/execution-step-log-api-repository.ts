/**
 * ExecutionStepLogApiRepository — HTTP client implementation of IExecutionStepLogRepository.
 *
 * Reads execution step logs from evolvetrace's public read endpoint:
 *   GET /api/runs/:flowId/steps  → { success, data: ExecutionStepLogRow[], meta: { total, limit, offset } }
 *
 * Note: This repository is only exercised when running taskguard in HTTP API mode against a
 * remote evolvetrace server. In that mode the server owns the execution_step_log table — the
 * client reads step logs over HTTP and does NOT write/clean them directly. Therefore the
 * write-side methods (insertStep / deleteOlderThan) are best-effort no-ops: they log that the
 * operation is not supported over the remote API and return a safe default, mirroring the
 * DB repository's "best-effort writes never throw" contract.
 *
 * Read methods are best-effort too: on any failure they log and return empty/nil rather than throw.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IExecutionStepLogRepository,
  ExecutionStepLogRow,
  ExecutionStepLogInsert,
  FindExecutionStepLogOptions,
} from "../repositories/types.js";

export class ExecutionStepLogApiRepository implements IExecutionStepLogRepository {
  constructor(private api: ApiClient) {}

  /**
   * Insert a single execution step log entry.
   *
   * The server owns the execution_step_log table in API mode, so the client cannot write step
   * logs over HTTP. Best-effort no-op: logs and returns false without throwing.
   */
  async insertStep(step: ExecutionStepLogInsert): Promise<boolean> {
    void step;
    console.warn(
      "[ExecutionStepLogApi] insertStep is not supported over HTTP API mode " +
        "(the server owns the execution_step_log table). Skipped insert.",
    );
    return false;
  }

  /** Query step logs for a flow, with optional filters. Sorted by timestamp ascending. */
  async getStepsByFlow(
    flowId: string,
    options?: FindExecutionStepLogOptions,
  ): Promise<ExecutionStepLogRow[]> {
    try {
      const query: Record<string, string> = {};
      if (options?.nodeId) query.nodeId = options.nodeId;
      if (options?.stepType) query.stepType = options.stepType;
      query.limit = String(options?.limit ?? 100);
      query.offset = String(options?.offset ?? 0);

      const resp = await this.api.get<{ success: boolean; data: ExecutionStepLogRow[] }>(
        `/api/runs/${encodeURIComponent(flowId)}/steps`,
        query,
      );
      if (!resp.ok || !Array.isArray(resp.data)) return [];
      return resp.data;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[ExecutionStepLogApi] getStepsByFlow failed: ${msg}`);
      return [];
    }
  }

  /** Count step logs for a flow, with optional filters. */
  async getStepCountByFlow(
    flowId: string,
    options?: FindExecutionStepLogOptions,
  ): Promise<number> {
    // The read endpoint returns a limited page, so a count derived from a single page is only
    // exact when the result set fits within the limit. Fetch a large page (mirroring the
    // server's /:id/replay endpoint) and report its length.
    const steps = await this.getStepsByFlow(flowId, { ...options, limit: 10000, offset: 0 });
    return steps.length;
  }

  /**
   * Delete step logs older than the given Unix timestamp (cleanup).
   *
   * Like insertStep, this is a server-owned-table concern and is not supported over HTTP API
   * mode. Best-effort no-op (logs and returns 0).
   */
  async deleteOlderThan(olderThan: number): Promise<number> {
    void olderThan;
    console.warn(
      "[ExecutionStepLogApi] deleteOlderThan is not supported over HTTP API mode " +
        "(the server owns the execution_step_log table). Skipped cleanup.",
    );
    return 0;
  }
}