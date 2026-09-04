/**
 * MetricsRepository — reads and writes flow_metrics table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "../db.js";

export type FlowMetricRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string;
  metric_name: string;
  metric_value: number;
  time: number;
  labels_json: string | null;
  gmt_create: number;
  gmt_modified: number;
};

export type MetricsAggregateResult = {
  metric_name: string;
  total_count: number;
  avg_value: number;
  min_value: number;
  max_value: number;
  sum_value: number;
};

export type AggregateOptions = {
  metricName?: string;
  nodeId?: string;
  limit?: number;
  offset?: number;
};

export class MetricsRepository {
  constructor(protected db: IDatabase) {}

  // ── Write methods (best-effort: catch errors, log, return false) ──

  async record(
    flowId: string,
    workflowId: string,
    nodeId: string,
    metricName: string,
    metricValue: number,
    labels?: Record<string, unknown>,
  ): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const time = typeof now === "number" ? now : Math.floor(Date.now() / 1000);
      await this.db.exec(
        `INSERT INTO flow_metrics (flow_id, workflow_id, node_id, metric_name, metric_value, time, labels_json, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          flowId,
          workflowId,
          nodeId,
          metricName,
          metricValue,
          time,
          labels ? JSON.stringify(labels) : null,
          now,
          now,
        ],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] MetricsRepository.record failed: ${msg}`);
      return false;
    }
  }

  // ── Read methods ──

  async aggregate(
    workflowId: string,
    startTime: number,
    endTime: number,
    options: AggregateOptions = {},
  ): Promise<MetricsAggregateResult[]> {
    try {
      const conditions: string[] = ["workflow_id = ?", "time >= ?", "time <= ?"];
      const values: unknown[] = [workflowId, startTime, endTime];

      if (options.metricName) {
        conditions.push("metric_name = ?");
        values.push(options.metricName);
      }
      if (options.nodeId) {
        conditions.push("node_id = ?");
        values.push(options.nodeId);
      }

      const limit = options.limit ?? 100;
      const offset = options.offset ?? 0;
      values.push(limit, offset);

      return await this.db.query<MetricsAggregateResult>(
        `SELECT metric_name, COUNT(*) as total_count, AVG(metric_value) as avg_value, MIN(metric_value) as min_value, MAX(metric_value) as max_value, SUM(metric_value) as sum_value
         FROM flow_metrics
         WHERE ${conditions.join(" AND ")}
         GROUP BY metric_name
         ORDER BY metric_name
         LIMIT ? OFFSET ?`,
        values,
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] MetricsRepository.aggregate failed: ${msg}`);
      return [];
    }
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_metrics WHERE flow_id = ?",
        [flowId],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] MetricsRepository.deleteByFlowId failed: ${msg}`);
      return 0;
    }
  }

  async findByFlowId(flowId: string): Promise<FlowMetricRow[]> {
    try {
      return await this.db.query<FlowMetricRow>(
        "SELECT * FROM flow_metrics WHERE flow_id = ? ORDER BY time ASC",
        [flowId],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] MetricsRepository.findByFlowId failed: ${msg}`);
      return [];
    }
  }
}

// ── Backward-compatible aliases ──

/** @deprecated Use MetricsRepository */
export type InsertMetricInput = {
  flow_id: string;
  workflow_id: string;
  node_id: string;
  metric_name: string;
  metric_value: number;
  time?: number;
  labels_json?: string | null;
};

/** @deprecated Use MetricsAggregateResult */
export type AggregateResult = MetricsAggregateResult;

/** @deprecated Use MetricsRepository */
export class FlowMetricsRepository extends MetricsRepository {
  /** @deprecated Use record() */
  async insert(input: InsertMetricInput): Promise<FlowMetricRow> {
    const ok = await this.record(
      input.flow_id,
      input.workflow_id,
      input.node_id,
      input.metric_name,
      input.metric_value,
      input.labels_json ? JSON.parse(input.labels_json) as Record<string, unknown> : undefined,
    );
    if (!ok) {
      throw new Error("Failed to insert metric");
    }
    const rows = await this.findByFlowId(input.flow_id);
    const last = rows[rows.length - 1];
    return last!;
  }

  /**
   * @deprecated Use aggregate(workflowId, startTime, endTime, options)
   * Backward-compatible aggregate accepting old-style options object.
   */
  async aggregate(optionsOrWorkflowId?: Parameters<typeof MetricsRepository.prototype.aggregate>[0] | {
    workflow_id?: string;
    metric_name?: string;
    start_time?: number;
    end_time?: number;
  }, startTime?: number, endTime?: number, options?: AggregateOptions): Promise<AggregateResult[]> {
    // New-style call: aggregate(workflowId, startTime, endTime, options)
    if (typeof optionsOrWorkflowId === "string") {
      return super.aggregate(optionsOrWorkflowId, startTime ?? 0, endTime ?? Math.floor(Date.now() / 1000), options);
    }
    // Old-style call: aggregate({ workflow_id, ... })
    const opts = optionsOrWorkflowId ?? {};
    if (!opts.workflow_id) {
      const conditions: string[] = [];
      const values: unknown[] = [];

      if (opts.metric_name) {
        conditions.push("metric_name = ?");
        values.push(opts.metric_name);
      }

      const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
      values.push(100, 0);

      return this.db.query<AggregateResult>(
        `SELECT metric_name, COUNT(*) as total_count, AVG(metric_value) as avg_value, MIN(metric_value) as min_value, MAX(metric_value) as max_value, SUM(metric_value) as sum_value FROM flow_metrics ${where} GROUP BY metric_name ORDER BY metric_name LIMIT ? OFFSET ?`,
        values,
      );
    }
    return super.aggregate(
      opts.workflow_id,
      opts.start_time ?? 0,
      opts.end_time ?? Math.floor(Date.now() / 1000),
      { metricName: opts.metric_name },
    );
  }
}