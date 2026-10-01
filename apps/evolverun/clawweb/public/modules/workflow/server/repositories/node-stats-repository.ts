/**
 * NodeStatsRepository — aggregates node_executions by workflowId for analytics.
 *
 * Provides per-node statistics: execution count, failure rate, retry count,
 * duration distribution (avg/P50/P95/max/min), token usage, error classification.
 *
 * SQL GROUP BY on node_executions (indexed by workflow_id).
 */
import type { IDatabase, Row } from "@avernet/clawweb-shared/server/db";

// ── Types ──

export type ErrorCategory = {
  category: string;
  count: number;
  sampleError: string | null;
};

export type NodeStat = {
  nodeId: string;
  nodeTitle: string | null;
  executorType: string | null;
  totalExecutions: number;
  succeededCount: number;
  failedCount: number;
  failureRate: number;
  avgRetryCount: number;
  avgDurationMs: number;
  p50DurationMs: number;
  p95DurationMs: number;
  maxDurationMs: number;
  minDurationMs: number;
  avgTokens: number | null;
  totalTokens: number | null;
  lastErrorText: string | null;
  lastErrorAt: number | null;
  errorCategories: ErrorCategory[];
};

export type WorkflowNodeStats = {
  workflowId: string;
  totalRuns: number;
  nodes: NodeStat[];
};

export type WorkflowHealth = {
  overallScore: number;
  successRate: number;
  nodeFailureRate: number;
  p95DurationMs: number;
  retryRate: number;
  totalTokens: number | null;
  bottleneckNode: string | null;
  fragileNode: string | null;
  recommendation: string;
};

export type DailySuccessTrend = {
  date: string;           // YYYY-MM-DD
  totalRuns: number;
  succeededRuns: number;
  failedRuns: number;
  successRate: number;    // 0-100
};

// ── Repository ──

export class NodeStatsRepository {
  constructor(private db: IDatabase) {}

  /**
   * Get per-node statistics for a workflow, optionally filtered by time range.
   * Returns nodes sorted by failed_count DESC, then avg_duration_ms DESC.
   */
  async getNodeStats(workflowId: string, days?: number): Promise<WorkflowNodeStats> {
    const timeFilter = days && days > 0
      ? `AND started_at >= ${Math.floor(Date.now() / 1000) - days * 86400}`
      : "";

    // 1. Aggregate stats per node
    const sql = `
      SELECT
        node_id,
        MAX(node_title) AS node_title,
        MAX(executor_type) AS executor_type,
        COUNT(*) AS total_executions,
        SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
        AVG(attempt) AS avg_retry_count,
        AVG(NULLIF(duration_ms, 0)) AS avg_duration_ms,
        MAX(NULLIF(duration_ms, 0)) AS max_duration_ms,
        MIN(NULLIF(duration_ms, 0)) AS min_duration_ms,
        MAX(CASE WHEN status = 'failed' THEN error_text ELSE NULL END) AS last_error_text,
        MAX(CASE WHEN status = 'failed' THEN completed_at ELSE NULL END) AS last_error_at
      FROM node_executions
      WHERE workflow_id = ? ${timeFilter}
      GROUP BY node_id
      ORDER BY failed_count DESC, avg_duration_ms DESC
    `;

    const rows = await this.db.query<Row>(sql, [workflowId]);

    if (rows.length === 0) {
      return { workflowId, totalRuns: 0, nodes: [] };
    }

    // 2. Get total run count (distinct flow_ids)
    const countSql = `SELECT COUNT(DISTINCT flow_id) AS total_runs FROM node_executions WHERE workflow_id = ? ${timeFilter}`;
    const countRows = await this.db.query<Row>(countSql, [workflowId]);
    const totalRuns = Number(countRows[0]?.total_runs ?? 0);

    // 3. Get P50/P95 duration per node (separate query for compatibility)
    const nodeIds = rows.map((r) => String(r.node_id));
    const pStats = await this.getDurationPercentiles(workflowId, nodeIds, timeFilter);

    // 4. Get token stats per node
    const tokenStats = await this.getTokenStats(workflowId, nodeIds, timeFilter);

    // 5. Get error categories per node
    const errorCats = await this.getErrorCategories(workflowId, nodeIds, timeFilter);

    // 6. Assemble
    const nodes: NodeStat[] = rows.map((r) => {
      const nodeId = String(r.node_id);
      const total = Number(r.total_executions) || 0;
      const failed = Number(r.failed_count) || 0;
      const pStat = pStats.get(nodeId);
      const tokStat = tokenStats.get(nodeId);

      return {
        nodeId,
        nodeTitle: r.node_title != null ? String(r.node_title) : null,
        executorType: r.executor_type != null ? String(r.executor_type) : null,
        totalExecutions: total,
        succeededCount: Number(r.succeeded_count) || 0,
        failedCount: failed,
        failureRate: total > 0 ? failed / total : 0,
        avgRetryCount: Number(r.avg_retry_count) || 1,
        avgDurationMs: Math.round(Number(r.avg_duration_ms) || 0),
        p50DurationMs: pStat?.p50 ?? 0,
        p95DurationMs: pStat?.p95 ?? 0,
        maxDurationMs: Number(r.max_duration_ms) || 0,
        minDurationMs: Number(r.min_duration_ms) || 0,
        avgTokens: tokStat?.avg ?? null,
        totalTokens: tokStat?.total ?? null,
        lastErrorText: r.last_error_text != null ? String(r.last_error_text).slice(0, 500) : null,
        lastErrorAt: r.last_error_at != null ? Number(r.last_error_at) : null,
        errorCategories: errorCats.get(nodeId) ?? [],
      };
    });

    return { workflowId, totalRuns, nodes };
  }

  /**
   * Get P50 and P95 duration per node.
   * MySQL/OceanBase: use PERCENTILE_CONT; SQLite: client-side sort + index.
   */
  private async getDurationPercentiles(
    workflowId: string,
    nodeIds: string[],
    timeFilter: string,
  ): Promise<Map<string, { p50: number; p95: number }>> {
    const result = new Map<string, { p50: number; p95: number }>();

    if (nodeIds.length === 0) return result;

    // Fetch all durations per node (client-side percentile for SQLite compatibility)
    const sql = `
      SELECT node_id, duration_ms
      FROM node_executions
      WHERE workflow_id = ? ${timeFilter} AND duration_ms IS NOT NULL AND duration_ms > 0
      ORDER BY node_id, duration_ms
    `;
    const rows = await this.db.query<Row>(sql, [workflowId]);

    // Group by node_id
    const byNode = new Map<string, number[]>();
    for (const r of rows) {
      const nodeId = String(r.node_id);
      const dur = Number(r.duration_ms);
      if (!byNode.has(nodeId)) byNode.set(nodeId, []);
      byNode.get(nodeId)!.push(dur);
    }

    for (const [nodeId, durations] of byNode) {
      if (durations.length === 0) {
        result.set(nodeId, { p50: 0, p95: 0 });
        continue;
      }
      durations.sort((a, b) => a - b);
      const p50Idx = Math.floor(durations.length * 0.5);
      const p95Idx = Math.floor(durations.length * 0.95);
      result.set(nodeId, {
        p50: Math.round(durations[p50Idx] || 0),
        p95: Math.round(durations[p95Idx] || 0),
      });
    }

    return result;
  }

  /**
   * Get token usage stats per node from token_usage_json field.
   */
  private async getTokenStats(
    workflowId: string,
    nodeIds: string[],
    timeFilter: string,
  ): Promise<Map<string, { avg: number; total: number }>> {
    const result = new Map<string, { avg: number; total: number }>();
    if (nodeIds.length === 0) return result;

    const sql = `
      SELECT node_id, token_usage_json
      FROM node_executions
      WHERE workflow_id = ? ${timeFilter} AND token_usage_json IS NOT NULL AND token_usage_json != ''
    `;
    const rows = await this.db.query<Row>(sql, [workflowId]);

    const byNode = new Map<string, number[]>();
    for (const r of rows) {
      const nodeId = String(r.node_id);
      try {
        const parsed = JSON.parse(String(r.token_usage_json)) as Record<string, unknown>;
        const tokens = Number(parsed.total_tokens ?? parsed.totalTokens ?? parsed.total ?? 0);
        if (tokens > 0) {
          if (!byNode.has(nodeId)) byNode.set(nodeId, []);
          byNode.get(nodeId)!.push(tokens);
        }
      } catch { /* skip unparseable */ }
    }

    for (const [nodeId, tokens] of byNode) {
      const total = tokens.reduce((a, b) => a + b, 0);
      result.set(nodeId, { avg: Math.round(total / tokens.length), total });
    }

    return result;
  }

  /**
   * Get error categories per node using SQL CASE WHEN classification.
   */
  private async getErrorCategories(
    workflowId: string,
    nodeIds: string[],
    timeFilter: string,
  ): Promise<Map<string, ErrorCategory[]>> {
    const result = new Map<string, ErrorCategory[]>();
    if (nodeIds.length === 0) return result;

    // First-level: 6 base categories; "Other" gets second-level sub-classification
    const sql = `
      SELECT
        node_id,
        CASE
          WHEN error_text LIKE '%TypeError%' OR error_text LIKE '%Cannot read properties of%' THEN 'TypeError'
          WHEN error_text LIKE '%timeout%' OR error_text LIKE '%TimeoutError%' THEN 'Timeout'
          WHEN error_text LIKE '%Output contract%' OR error_text LIKE '%output contract%' THEN 'OutputContract'
          WHEN error_text LIKE '%JSON.parse%' OR error_text LIKE '%SyntaxError%JSON%' THEN 'JSON.parse'
          WHEN error_text LIKE '%embedded-agent execution failed%' THEN 'EmbeddedAgent'
          ELSE
            CASE
              WHEN error_text LIKE '%odps%' OR error_text LIKE '%ODPS%' OR error_text LIKE '%MaxCompute%' THEN 'ODPS'
              WHEN error_text LIKE '%yuque%' OR error_text LIKE '%Yuque%' THEN 'Yuque'
              WHEN error_text LIKE '%permission%' OR error_text LIKE '%Permission denied%' OR error_text LIKE '%unauthorized%' OR error_text LIKE '%Unauthorized%' THEN 'Permission'
              WHEN error_text LIKE '%network%' OR error_text LIKE '%ECONNREFUSED%' OR error_text LIKE '%ECONNRESET%' OR error_text LIKE '%ENOTFOUND%' THEN 'Network'
              WHEN error_text LIKE '%rate limit%' OR error_text LIKE '%RateLimit%' OR error_text LIKE '%429%' THEN 'RateLimit'
              WHEN error_text LIKE '%invalid%' OR error_text LIKE '%Invalid%' OR error_text LIKE '%malformed%' THEN 'InvalidFormat'
              WHEN error_text LIKE '%empty%' OR error_text LIKE '%null%' OR error_text LIKE '%undefined%' OR error_text LIKE '%No data%' THEN 'EmptyData'
              ELSE 'Other'
            END
        END AS error_category,
        COUNT(*) AS cat_count,
        MIN(error_text) AS sample_error
      FROM node_executions
      WHERE workflow_id = ? ${timeFilter} AND status = 'failed' AND error_text IS NOT NULL
      GROUP BY node_id, error_category
      ORDER BY cat_count DESC
    `;
    const rows = await this.db.query<Row>(sql, [workflowId]);

    for (const r of rows) {
      const nodeId = String(r.node_id);
      if (!result.has(nodeId)) result.set(nodeId, []);
      result.get(nodeId)!.push({
        category: String(r.error_category),
        count: Number(r.cat_count),
        sampleError: r.sample_error != null ? String(r.sample_error).slice(0, 200) : null,
      });
    }

    return result;
  }

  /**
   * Get detailed error breakdown for a specific node, including up to 3 sample errors per category.
   */
  async getErrorBreakdown(workflowId: string, nodeId: string, days?: number): Promise<{
    nodeId: string;
    categories: Array<{ category: string; count: number; samples: string[] }>;
  }> {
    const timeFilter = days && days > 0
      ? `AND started_at >= ${Math.floor(Date.now() / 1000) - days * 86400}`
      : "";

    // Get categorized counts (reusing the same CASE WHEN logic)
    const catSql = `
      SELECT
        CASE
          WHEN error_text LIKE '%TypeError%' OR error_text LIKE '%Cannot read properties of%' THEN 'TypeError'
          WHEN error_text LIKE '%timeout%' OR error_text LIKE '%TimeoutError%' THEN 'Timeout'
          WHEN error_text LIKE '%Output contract%' OR error_text LIKE '%output contract%' THEN 'OutputContract'
          WHEN error_text LIKE '%JSON.parse%' OR error_text LIKE '%SyntaxError%JSON%' THEN 'JSON.parse'
          WHEN error_text LIKE '%embedded-agent execution failed%' THEN 'EmbeddedAgent'
          ELSE
            CASE
              WHEN error_text LIKE '%odps%' OR error_text LIKE '%ODPS%' OR error_text LIKE '%MaxCompute%' THEN 'ODPS'
              WHEN error_text LIKE '%yuque%' OR error_text LIKE '%Yuque%' THEN 'Yuque'
              WHEN error_text LIKE '%permission%' OR error_text LIKE '%Permission denied%' OR error_text LIKE '%unauthorized%' THEN 'Permission'
              WHEN error_text LIKE '%network%' OR error_text LIKE '%ECONNREFUSED%' OR error_text LIKE '%ECONNRESET%' THEN 'Network'
              WHEN error_text LIKE '%rate limit%' OR error_text LIKE '%RateLimit%' OR error_text LIKE '%429%' THEN 'RateLimit'
              WHEN error_text LIKE '%invalid%' OR error_text LIKE '%Invalid%' OR error_text LIKE '%malformed%' THEN 'InvalidFormat'
              WHEN error_text LIKE '%empty%' OR error_text LIKE '%null%' OR error_text LIKE '%undefined%' THEN 'EmptyData'
              ELSE 'Other'
            END
        END AS error_category,
        COUNT(*) AS cat_count
      FROM node_executions
      WHERE workflow_id = ? AND node_id = ? ${timeFilter} AND status = 'failed' AND error_text IS NOT NULL
      GROUP BY error_category
      ORDER BY cat_count DESC
    `;
    const catRows = await this.db.query<Row>(catSql, [workflowId, nodeId]);

    // For each category, get up to 3 sample errors
    const categories: Array<{ category: string; count: number; samples: string[] }> = [];
    for (const cr of catRows) {
      const cat = String(cr.error_category);
      const sampleSql = `
        SELECT error_text FROM node_executions
        WHERE workflow_id = ? AND node_id = ? ${timeFilter} AND status = 'failed' AND error_text IS NOT NULL
        AND (
          ${this.categoryToSqlCondition(cat)}
        )
        ORDER BY completed_at DESC LIMIT 3
      `;
      const sampleRows = await this.db.query<Row>(sampleSql, [workflowId, nodeId]);
      categories.push({
        category: cat,
        count: Number(cr.cat_count),
        samples: sampleRows.map((r) => String(r.error_text ?? "").slice(0, 300)),
      });
    }

    return { nodeId, categories };
  }

  /** Convert a category name back to SQL LIKE conditions for sample query */
  private categoryToSqlCondition(cat: string): string {
    const conditions: Record<string, string> = {
      "TypeError": "error_text LIKE '%TypeError%' OR error_text LIKE '%Cannot read properties of%'",
      "Timeout": "error_text LIKE '%timeout%' OR error_text LIKE '%TimeoutError%'",
      "OutputContract": "error_text LIKE '%Output contract%' OR error_text LIKE '%output contract%'",
      "JSON.parse": "error_text LIKE '%JSON.parse%' OR error_text LIKE '%SyntaxError%JSON%'",
      "EmbeddedAgent": "error_text LIKE '%embedded-agent execution failed%'",
      "ODPS": "error_text LIKE '%odps%' OR error_text LIKE '%ODPS%' OR error_text LIKE '%MaxCompute%'",
      "Yuque": "error_text LIKE '%yuque%' OR error_text LIKE '%Yuque%'",
      "Permission": "error_text LIKE '%permission%' OR error_text LIKE '%Permission denied%' OR error_text LIKE '%unauthorized%'",
      "Network": "error_text LIKE '%network%' OR error_text LIKE '%ECONNREFUSED%' OR error_text LIKE '%ECONNRESET%'",
      "RateLimit": "error_text LIKE '%rate limit%' OR error_text LIKE '%RateLimit%' OR error_text LIKE '%429%'",
      "InvalidFormat": "error_text LIKE '%invalid%' OR error_text LIKE '%Invalid%' OR error_text LIKE '%malformed%'",
      "EmptyData": "error_text LIKE '%empty%' OR error_text LIKE '%null%' OR error_text LIKE '%undefined%'",
      "Other": "1=1", // fallback: all remaining
    };
    return conditions[cat] ?? "1=1";
  }

  /**
   * Get daily success rate trend for a workflow.
   * Groups flow_runs by date, calculates success rate per day.
   */
  async getSuccessTrend(workflowId: string, days: number = 7): Promise<DailySuccessTrend[]> {
    // Use DATE_FORMAT for MySQL/ZDAS, date() for SQLite — detect by dbType
    const isMysql = this.db.dbType === "mysql" || this.db.dbType === "zdas";
    const dateExpr = isMysql
      ? `DATE_FORMAT(FROM_UNIXTIME(started_at), '%Y-%m-%d')`
      : `date(started_at, 'unixepoch')`;
    // Inline days as integer to avoid parameterized ? in arithmetic expression issues
    const cutoffSeconds = Math.floor(Date.now() / 1000) - days * 86400;

    const rows = await this.db.query<Row>(
      `SELECT
         ${dateExpr} AS run_date,
         COUNT(*) AS total_runs,
         SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_runs,
         SUM(CASE WHEN status IN ('failed', 'aborted', 'cancelled', 'canceled') THEN 1 ELSE 0 END) AS failed_runs
       FROM flow_runs
       WHERE workflow_id = ?
         AND started_at >= ?
         AND status IN ('succeeded', 'failed', 'aborted', 'cancelled', 'canceled')
       GROUP BY run_date
       ORDER BY run_date ASC`,
      [workflowId, cutoffSeconds],
    );

    return rows.map((r) => {
      const total = Number(r.total_runs) || 0;
      const succeeded = Number(r.succeeded_runs) || 0;
      const failed = Number(r.failed_runs) || 0;
      return {
        date: String(r.run_date),
        totalRuns: total,
        succeededRuns: succeeded,
        failedRuns: failed,
        successRate: total > 0 ? Math.round((succeeded / total) * 1000) / 10 : 0,
      };
    });
  }

  async getWorkflowHealth(workflowId: string, days?: number): Promise<WorkflowHealth> {
    const stats = await this.getNodeStats(workflowId, days);

    if (stats.nodes.length === 0 || stats.totalRuns === 0) {
      return {
        overallScore: 0,
        successRate: 0,
        nodeFailureRate: 0,
        p95DurationMs: 0,
        retryRate: 0,
        totalTokens: null,
        bottleneckNode: null,
        fragileNode: null,
        recommendation: "暂无运行数据",
      };
    }

    // Success rate — query flow_runs table for run-level accuracy (not node-level approximation)
    const timeFilter = days && days > 0
      ? `AND started_at >= ${Math.floor(Date.now() / 1000) - days * 86400}`
      : "";
    const runRateRows = await this.db.query<Row>(
      `SELECT
         SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_runs,
         SUM(CASE WHEN status IN ('failed', 'aborted', 'cancelled', 'canceled') THEN 1 ELSE 0 END) AS failed_runs
       FROM flow_runs WHERE workflow_id = ? ${timeFilter}`,
      [workflowId],
    );
    const succeededRuns = Number(runRateRows[0]?.succeeded_runs ?? 0);
    const failedRuns = Number(runRateRows[0]?.failed_runs ?? 0);
    const terminalRuns = succeededRuns + failedRuns;
    const successRate = terminalRuns > 0 ? succeededRuns / terminalRuns : 0;

    // Node failure rate (average across nodes)
    const nodeFailureRate = stats.nodes.reduce((s, n) => s + n.failureRate, 0) / stats.nodes.length;

    // P95 duration (max across nodes = worst case)
    const p95DurationMs = Math.max(...stats.nodes.map((n) => n.p95DurationMs));

    // Retry rate (average across nodes)
    const retryRate = stats.nodes.reduce((s, n) => s + n.avgRetryCount, 0) / stats.nodes.length;

    // Bottleneck node (highest P95)
    const bottleneckNode = stats.nodes.reduce((max, n) => n.p95DurationMs > max.p95DurationMs ? n : max, stats.nodes[0]);

    // Fragile node (highest failure rate)
    const fragileNode = stats.nodes.reduce((max, n) => n.failureRate > max.failureRate ? n : max, stats.nodes[0]);

    // Token stats — aggregate from node_executions (not flow_runs.total_token_usage which is often null)
    const totalTokens = stats.nodes.reduce((s, n) => s + (n.totalTokens ?? 0), 0);
    const avgTokens = stats.nodes.length > 0
      ? stats.nodes.reduce((s, n) => s + (n.avgTokens ?? 0), 0) / stats.nodes.length
      : 0;

    // Score calculation (5 dimensions, total 100)
    let score = 0;
    // Success rate (30 pts) — linear interpolation for smoother scoring
    if (successRate >= 0.95) score += 30;
    else if (successRate >= 0.80) score += 15 + Math.round((successRate - 0.80) / 0.15 * 15);
    else score += Math.round(successRate / 0.80 * 15);
    // Node failure rate (20 pts)
    const highFailureNodes = stats.nodes.filter((n) => n.failureRate > 0.10).length;
    if (highFailureNodes === 0) score += 20;
    else if (highFailureNodes <= 1) score += 10;
    // P95 duration (20 pts)
    if (p95DurationMs < 30000) score += 20;
    else if (p95DurationMs < 120000) score += 10;
    // Retry rate (15 pts)
    if (retryRate < 1.2) score += 15;
    else if (retryRate < 2.0) score += 8;
    // Token efficiency (15 pts) — based on avg tokens per execution from node_executions
    if (avgTokens > 0 && avgTokens < 5000) score += 15;
    else if (avgTokens > 0 && avgTokens < 10000) score += 8;

    // Recommendation
    const recommendations: string[] = [];
    if (fragileNode.failureRate > 0.10) {
      recommendations.push(`节点 ${fragileNode.nodeId} 失败率 ${(fragileNode.failureRate * 100).toFixed(1)}%，建议优化 prompt 或加 fallback`);
    }
    if (bottleneckNode.p95DurationMs > 30000) {
      recommendations.push(`节点 ${bottleneckNode.nodeId} P95 耗时 ${(bottleneckNode.p95DurationMs / 1000).toFixed(1)}s，建议拆分或调整超时`);
    }
    if (retryRate > 1.5) {
      recommendations.push(`平均重试 ${retryRate.toFixed(1)} 次，建议调整 retry 策略`);
    }
    if (successRate < 0.80) {
      recommendations.push(`运行成功率仅 ${(successRate * 100).toFixed(1)}%，需重点关注`);
    }
    if (recommendations.length === 0) {
      recommendations.push("工作流运行健康，无明显问题");
    }

    return {
      overallScore: score,
      successRate: Math.round(successRate * 1000) / 10,
      nodeFailureRate: Math.round(nodeFailureRate * 1000) / 10,
      p95DurationMs,
      retryRate: Math.round(retryRate * 10) / 10,
      totalTokens: totalTokens > 0 ? totalTokens : null,
      bottleneckNode: bottleneckNode.nodeId,
      fragileNode: fragileNode.nodeId,
      recommendation: recommendations.join("；"),
    };
  }
}
