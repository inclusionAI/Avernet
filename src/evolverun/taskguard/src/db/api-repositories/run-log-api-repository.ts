/**
 * RunLogApiRepository — HTTP client implementation of IRunLogRepository.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for run logs.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IRunLogRepository,
  RunLogRow,
  RunLogInsert,
} from "../repositories/types.js";

export class RunLogApiRepository implements IRunLogRepository {
  constructor(private api: ApiClient) {}

  async insertBatch(entries: RunLogInsert[]): Promise<number> {
    void entries;
    console.warn(
      "[RunLogApi] insertBatch is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }

  async findByFlowId(flowId: string): Promise<RunLogRow[]> {
    void flowId;
    console.warn(
      "[RunLogApi] findByFlowId is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    void flowId;
    console.warn(
      "[RunLogApi] deleteByFlowId is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }

  async deleteOlderThan(olderThan: number): Promise<number> {
    void olderThan;
    console.warn(
      "[RunLogApi] deleteOlderThan is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }
}