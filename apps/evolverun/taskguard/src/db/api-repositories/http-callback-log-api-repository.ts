/**
 * HttpCallbackLogApiRepository — HTTP client implementation of IHttpCallbackLogRepository.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for HTTP callback logs.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IHttpCallbackLogRepository,
  HttpCallbackLogRow,
  HttpCallbackLogInsert,
} from "../../alerts/http-callback-types.js";

export class HttpCallbackLogApiRepository implements IHttpCallbackLogRepository {
  constructor(private api: ApiClient) {}

  async insert(log: HttpCallbackLogInsert): Promise<number> {
    void log;
    console.warn(
      "[HttpCallbackLogApi] insert is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }

  async findByFlowId(flowId: string, limit?: number): Promise<HttpCallbackLogRow[]> {
    void flowId; void limit;
    console.warn(
      "[HttpCallbackLogApi] findByFlowId is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async findByWorkflowId(workflowId: string, limit?: number): Promise<HttpCallbackLogRow[]> {
    void workflowId; void limit;
    console.warn(
      "[HttpCallbackLogApi] findByWorkflowId is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async findByStatus(status: string, limit?: number): Promise<HttpCallbackLogRow[]> {
    void status; void limit;
    console.warn(
      "[HttpCallbackLogApi] findByStatus is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async deleteOlderThan(timestamp: number): Promise<number> {
    void timestamp;
    console.warn(
      "[HttpCallbackLogApi] deleteOlderThan is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }
}