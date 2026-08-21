/**
 * FlowMetricsRepository — persists and queries numeric metrics.
 *
 * Best-effort writes: DB failure is logged but doesn't throw.
 */
import type { IDatabase, Row } from "../types.js";
import { nowForDb, formatTimestamp } from "../types.js";
import type { IFlowMetricsRepository } from "./types.js";

export type FlowMetricsRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string;
  metric_name: string;
  metric_value: number;
  time: number;
  labels_json: string | null;
  gmt_create: number;
};

export type MetricsAggregateResult = {
  group_key: string;
  aggregate_value: number;
};

export type AggregateOptions = {
  metricName: string;
  aggregation: "avg" | "count" | "sum";
  groupBy?: string;
};

export class FlowMetricsRepository implements IFlowMetricsRepository {
  constructor(private db: IDatabase) {}

  /** Record a metric. Returns true on success, false on failure. */
  async record(
    flowId: string,
    workflowId: string,
    nodeId: string,
    metricName: string,
    metricValue: number,
    labels?: Record<string, string>,
  ): Promise<boolean> {
    try {
      await this.db.exec(
        `INSERT INTO flow_metrics (flow_id, workflow_id, node_id, metric_name, metric_value, time, labels_json, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          flowId,
          workflowId,
          nodeId,
          metricName,
          metricValue,
          Math.floor(Date.now() / 1000),
          labels ? JSON.stringify(labels) : null,
          nowForDb(this.db.dbType),
        ],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowMetricsRepository.record failed: ${msg}`);
      return false;
    }
  }

  /**
   * Aggregate metrics for a workflow over a time range.
   *
   * Returns grouped results: { group_key, aggregate_value }.
   * group_key is the value of the groupBy field (defaults to "node_id").
   */
  async aggregate(
    workflowId: string,
    startTime: number,
    endTime: number,
    options: AggregateOptions,
  ): Promise<MetricsAggregateResult[]> {
    const groupBy = options.groupBy ?? "node_id";
    const aggFunc = options.aggregation.toUpperCase();
    try {
      const rows = await this.db.query<Row>(
        `SELECT ${groupBy} AS group_key, ${aggFunc}(metric_value) AS aggregate_value
         FROM flow_metrics
         WHERE workflow_id = ? AND metric_name = ? AND time >= ? AND time <= ?
         GROUP BY ${groupBy}
         ORDER BY aggregate_value DESC`,
        [workflowId, options.metricName, startTime, endTime],
      );
      return rows.map((r) => ({
        group_key: String(r.group_key ?? ""),
        aggregate_value: Number(r.aggregate_value ?? 0),
      }));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowMetricsRepository.aggregate failed: ${msg}`);
      return [];
    }
  }
}