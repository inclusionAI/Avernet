/**
 * Execution analysis types for post-workflow health scoring
 * and threshold-based alert generation.
 */

/** Per-node metrics collected from flow_metrics for analysis. */
export type NodeMetrics = {
  nodeId: string;
  executorType: string;
  /** Number of successful executions. */
  successCount: number;
  /** Number of failed executions. */
  failureCount: number;
  /** Number of retry attempts. */
  retryCount: number;
  /** Average duration in milliseconds (0 if no data). */
  avgDurationMs: number;
  /** Total token usage (0 if no data). */
  totalTokens: number;
};

/** Aggregated workflow analysis result. */
export type AnalysisResult = {
  workflowId: string;
  flowId: string;
  /** Analysis timestamp (Unix seconds). */
  analyzedAt: number;
  /** Total nodes executed. */
  totalNodes: number;
  /** Nodes that succeeded. */
  succeededNodes: number;
  /** Nodes that failed (final status). */
  failedNodes: number;
  /** Nodes that required at least one retry. */
  retriedNodes: number;
  /** Overall health score: 1.0 = all succeeded, no retries; 0.0 = all failed. */
  healthScore: number;
  /** Failure rate: failedNodes / totalNodes. */
  toolFailureRate: number;
  /** Incomplete rate: (failedNodes + retriedNodes) / totalNodes. */
  incompleteRate: number;
  /** Average duration across all nodes (ms). */
  avgDurationMs: number;
  /** Total token usage. */
  totalTokens: number;
  /** Per-node breakdown. */
  nodes: NodeMetrics[];
};

/** A threshold breach detected during analysis. */
export type ThresholdBreach = {
  metric: "healthScore" | "toolFailureRate" | "incompleteRate";
  value: number;
  threshold: number;
  severity: "warning" | "critical";
  message: string;
};

/** Complete health report produced by the analyzer. */
export type HealthReport = {
  result: AnalysisResult;
  breaches: ThresholdBreach[];
  /** Whether any threshold was breached. */
  hasBreaches: boolean;
};