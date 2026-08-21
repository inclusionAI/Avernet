/**
 * TriggeredAlertApiRepository — HTTP client implementation of ITriggeredAlertRepository.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for triggered alerts.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type {
  ITriggeredAlertRepository,
  TriggeredAlertRow,
  FindUnacknowledgedOptions,
} from "../repositories/types.js";

export class TriggeredAlertApiRepository implements ITriggeredAlertRepository {
  constructor(private api: ApiClient) {}

  async record(
    flowId: string, workflowId: string, nodeId: string | null,
    alertRule: string, severity: string, message: string,
  ): Promise<boolean> {
    void flowId; void workflowId; void nodeId; void alertRule; void severity; void message;
    console.warn(
      "[TriggeredAlertApi] record is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return false;
  }

  async findUnacknowledged(
    workflowId: string,
    options?: FindUnacknowledgedOptions,
  ): Promise<TriggeredAlertRow[]> {
    void workflowId; void options;
    console.warn(
      "[TriggeredAlertApi] findUnacknowledged is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async acknowledge(alertId: number): Promise<boolean> {
    void alertId;
    console.warn(
      "[TriggeredAlertApi] acknowledge is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return false;
  }
}