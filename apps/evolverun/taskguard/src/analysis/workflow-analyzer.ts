/**
 * WorkflowAnalyzer — computes health scores and analysis results
 * from recorded flow_metrics data after a workflow completes.
 *
 * Reads metrics via FlowMetricsRepository, aggregates per-node stats,
 * and produces an AnalysisResult with health scores.
 */
import type { IFlowMetricsRepository } from "../db/repositories/types.js";
import type { AnalysisResult, NodeMetrics } from "./types.js";

export class WorkflowAnalyzer {
  constructor(private metricsRepo: IFlowMetricsRepository) {}

  /**
   * Analyze a completed workflow by aggregating its metrics.
   *
   * @param workflowId - The workflow definition ID.
   * @param flowId - The specific flow execution ID.
   * @param startTime - Unix timestamp of flow start.
   * @param endTime - Unix timestamp of flow end.
   */
  async analyze(
    workflowId: string,
    flowId: string,
    startTime: number,
    endTime: number,
  ): Promise<AnalysisResult> {
    const nodes = await this.aggregateNodeMetrics(workflowId, startTime, endTime);

    const totalNodes = nodes.length;
    const succeededNodes = nodes.reduce((s, n) => s + n.successCount, 0) > 0
      ? nodes.filter((n) => n.successCount > 0 && n.failureCount === 0).length
      : 0;
    const failedNodes = nodes.filter((n) => n.failureCount > 0 && n.successCount === 0).length;
    const retriedNodes = nodes.filter((n) => n.retryCount > 0).length;

    const totalSuccess = nodes.reduce((s, n) => s + n.successCount, 0);
    const totalFailure = nodes.reduce((s, n) => s + n.failureCount, 0);
    const totalExecutions = totalSuccess + totalFailure;

    // Health score: weighted combination of success rate and retry penalty
    // 1.0 = all succeeded, no retries; 0.0 = all failed
    const successRate = totalExecutions > 0 ? totalSuccess / totalExecutions : 1;
    const retryPenalty = totalNodes > 0 ? retriedNodes / totalNodes * 0.2 : 0;
    const healthScore = Math.max(0, Math.min(1, successRate - retryPenalty));

    const toolFailureRate = totalNodes > 0 ? failedNodes / totalNodes : 0;
    const incompleteRate = totalNodes > 0 ? (failedNodes + retriedNodes) / totalNodes : 0;

    const avgDurationMs = totalNodes > 0
      ? nodes.reduce((s, n) => s + n.avgDurationMs, 0) / totalNodes
      : 0;
    const totalTokens = nodes.reduce((s, n) => s + n.totalTokens, 0);

    return {
      workflowId,
      flowId,
      analyzedAt: Math.floor(Date.now() / 1000),
      totalNodes,
      succeededNodes,
      failedNodes,
      retriedNodes,
      healthScore: Math.round(healthScore * 1000) / 1000,
      toolFailureRate: Math.round(toolFailureRate * 1000) / 1000,
      incompleteRate: Math.round(incompleteRate * 1000) / 1000,
      avgDurationMs: Math.round(avgDurationMs),
      totalTokens,
      nodes,
    };
  }

  /**
   * Aggregate per-node metrics from the flow_metrics table.
   * Queries each metric type separately and merges results by nodeId.
   */
  private async aggregateNodeMetrics(
    workflowId: string,
    startTime: number,
    endTime: number,
  ): Promise<NodeMetrics[]> {
    const nodeMap = new Map<string, NodeMetrics>();

    // Query success counts
    const successes = await this.metricsRepo.aggregate(workflowId, startTime, endTime, {
      metricName: "node_succeeded",
      aggregation: "sum",
      groupBy: "node_id",
    });
    for (const row of successes) {
      const n = this.getOrCreate(nodeMap, row.group_key, "");
      n.successCount = row.aggregate_value;
    }

    // Query failure counts
    const failures = await this.metricsRepo.aggregate(workflowId, startTime, endTime, {
      metricName: "node_failed",
      aggregation: "sum",
      groupBy: "node_id",
    });
    for (const row of failures) {
      const n = this.getOrCreate(nodeMap, row.group_key, "");
      n.failureCount = row.aggregate_value;
    }

    // Query retry counts
    const retries = await this.metricsRepo.aggregate(workflowId, startTime, endTime, {
      metricName: "node_retry",
      aggregation: "sum",
      groupBy: "node_id",
    });
    for (const row of retries) {
      const n = this.getOrCreate(nodeMap, row.group_key, "");
      n.retryCount = row.aggregate_value;
    }

    // Query average duration
    const durations = await this.metricsRepo.aggregate(workflowId, startTime, endTime, {
      metricName: "node_duration_ms",
      aggregation: "avg",
      groupBy: "node_id",
    });
    for (const row of durations) {
      const n = this.getOrCreate(nodeMap, row.group_key, "");
      n.avgDurationMs = row.aggregate_value;
    }

    // Query total token usage
    const tokens = await this.metricsRepo.aggregate(workflowId, startTime, endTime, {
      metricName: "node_token_usage_total",
      aggregation: "sum",
      groupBy: "node_id",
    });
    for (const row of tokens) {
      const n = this.getOrCreate(nodeMap, row.group_key, "");
      n.totalTokens = row.aggregate_value;
    }

    // Fill executorType from the latest metric labels (best-effort)
    try {
      const allMetrics = await this.metricsRepo.aggregate(workflowId, startTime, endTime, {
        metricName: "node_succeeded",
        aggregation: "count",
        groupBy: "node_id",
      });
      // executorType comes from labels which aren't in aggregate results,
      // so we leave it as empty string — it can be enriched later if needed
      void allMetrics;
    } catch {
      // best-effort
    }

    return Array.from(nodeMap.values());
  }

  private getOrCreate(map: Map<string, NodeMetrics>, nodeId: string, executorType: string): NodeMetrics {
    let node = map.get(nodeId);
    if (!node) {
      node = {
        nodeId,
        executorType,
        successCount: 0,
        failureCount: 0,
        retryCount: 0,
        avgDurationMs: 0,
        totalTokens: 0,
      };
      map.set(nodeId, node);
    }
    return node;
  }
}