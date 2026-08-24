/**
 * FlowMetricsApiRepository — HTTP client implementation of IFlowMetricsRepository.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for flow metrics.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IFlowMetricsRepository,
  MetricsAggregateResult,
  AggregateOptions,
} from "../repositories/types.js";

export class FlowMetricsApiRepository implements IFlowMetricsRepository {
  constructor(private api: ApiClient) {}

  async record(
    flowId: string, workflowId: string, nodeId: string,
    metricName: string, metricValue: number, labels?: Record<string, string>,
  ): Promise<boolean> {
    void flowId; void workflowId; void nodeId; void metricName; void metricValue; void labels;
    console.warn(
      "[FlowMetricsApi] record is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return false;
  }

  async aggregate(
    workflowId: string, startTime: number, endTime: number,
    options: AggregateOptions,
  ): Promise<MetricsAggregateResult[]> {
    void workflowId; void startTime; void endTime; void options;
    console.warn(
      "[FlowMetricsApi] aggregate is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }
}