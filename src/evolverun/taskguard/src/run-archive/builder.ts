/**
 * RunArchiveBuilder — aggregates all execution data for a single workflow
 * instance into a structured RunArchive JSON.
 *
 * Data sources (8 total):
 *   ClawMind local tables (6):
 *     flow_runs, node_executions, flow_events, node_step_traces,
 *     execution_step_log, run_logs
 *   clawweb shared tables (2):
 *     aw_langfuse_traces, aw_langfuse_observation
 *
 * Langfuse query pattern follows clawweb/server/routes/langfuse.ts:115-172
 * (queryTracesFromDB): session_id → traces → trace_ids → observations.
 */
import type { IDatabase, Row } from "../db/types.js";
import type { IRunLogRepository } from "../db/repositories/types.js";
import type {
  RunArchive,
  RunLogRow,
  LangfuseTraceRow,
  LangfuseObservationRow,
  FailureSummary,
  FailedNodeInfo,
  ErrorTimelineEntry,
} from "./types.js";

export class RunArchiveBuilder {
  constructor(
    private db: IDatabase,
    private runLogRepo: IRunLogRepository,
  ) {}

  async buildArchive(flowId: string): Promise<RunArchive> {
    const archiveId = `archive_${flowId}_${Date.now()}`;
    const errors: string[] = [];

    // 1. Parallel query all ClawMind local tables
    const [
      flowRunRows,
      nodeExecutionRows,
      flowEventRows,
      nodeStepTraceRows,
      executionStepLogRows,
      runLogs,
    ] = await Promise.all([
      this.queryOne(`SELECT * FROM flow_runs WHERE flow_id = ?`, [flowId]),
      this.queryMany(`SELECT * FROM node_executions WHERE flow_id = ? ORDER BY started_at`, [flowId]),
      this.queryMany(`SELECT * FROM flow_events WHERE flow_id = ? ORDER BY time`, [flowId]),
      this.queryMany(`SELECT * FROM node_step_traces WHERE flow_id = ? ORDER BY step_seq`, [flowId]),
      this.queryMany(`SELECT * FROM execution_step_log WHERE flow_id = ? ORDER BY timestamp`, [flowId]),
      this.runLogRepo.findByFlowId(flowId).catch((): RunLogRow[] => []),
    ]);

    if (!flowRunRows) {
      throw new Error(`Flow run not found: ${flowId}`);
    }

    // 2. Query Langfuse traces + observations via embedded_session_key
    const sessionKeys = (nodeExecutionRows as Row[])
      .map((ne) => ne.embedded_session_key as string | null)
      .filter((k): k is string => !!k);

    let langfuseTraces: LangfuseTraceRow[] = [];
    let langfuseObservations: LangfuseObservationRow[] = [];

    try {
      const result = await this.queryLangfuse(sessionKeys);
      langfuseTraces = result.traces;
      langfuseObservations = result.observations;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      errors.push(`Langfuse query failed: ${msg}`);
    }

    // 3. Build failure summary
    const failureSummary = this.buildFailureSummary(
      nodeExecutionRows as Row[],
      flowEventRows as Row[],
      runLogs,
      langfuseObservations,
    );

    // 4. Assemble archive
    return {
      archive: {
        flowId,
        archiveId,
        archiveVersion: "1.0.0",
        createdAt: new Date().toISOString(),
        status: errors.length > 0 ? "partial" : "completed",
        errors,
      },
      flowRun: flowRunRows ?? null,
      nodeExecutions: nodeExecutionRows as Record<string, unknown>[],
      flowEvents: flowEventRows as Record<string, unknown>[],
      nodeStepTraces: nodeStepTraceRows as Record<string, unknown>[],
      executionStepLogs: executionStepLogRows as Record<string, unknown>[],
      runLogs,
      langfuseTraces,
      langfuseObservations,
      failureSummary,
    };
  }

  // ── Private helpers ──

  private async queryOne(sql: string, params: unknown[]): Promise<Row | null> {
    try {
      const rows = await this.db.query<Row>(sql, params);
      return rows[0] ?? null;
    } catch {
      return null;
    }
  }

  private async queryMany(sql: string, params: unknown[]): Promise<Row[]> {
    try {
      return await this.db.query<Row>(sql, params);
    } catch {
      return [];
    }
  }

  /**
   * Query Langfuse traces + observations.
   * Pattern follows clawweb/server/routes/langfuse.ts:115-172 (queryTracesFromDB).
   */
  private async queryLangfuse(sessionKeys: string[]): Promise<{
    traces: LangfuseTraceRow[];
    observations: LangfuseObservationRow[];
  }> {
    if (sessionKeys.length === 0) return { traces: [], observations: [] };

    const placeholders = sessionKeys.map(() => "?").join(",");

    // 1. Query traces by session_id or real_session_id
    const traceRows = await this.db.query<Row>(
      `SELECT trace_id, name, session_id, real_session_id, gmt_trace,
              input, output, metadata, latency, total_cost, user_id
       FROM aw_langfuse_traces
       WHERE session_id IN (${placeholders}) OR real_session_id IN (${placeholders})
       ORDER BY gmt_trace DESC LIMIT 100`,
      [...sessionKeys, ...sessionKeys],
    );

    if (traceRows.length === 0) return { traces: [], observations: [] };

    const traces: LangfuseTraceRow[] = traceRows.map((r) => ({
      trace_id: String(r.trace_id ?? ""),
      name: r.name != null ? String(r.name) : null,
      session_id: r.session_id != null ? String(r.session_id) : null,
      real_session_id: r.real_session_id != null ? String(r.real_session_id) : null,
      gmt_trace: r.gmt_trace != null ? Number(r.gmt_trace) : null,
      input: r.input != null ? String(r.input) : null,
      output: r.output != null ? String(r.output) : null,
      metadata: r.metadata != null ? String(r.metadata) : null,
      latency: r.latency != null ? Number(r.latency) : null,
      total_cost: r.total_cost != null ? Number(r.total_cost) : null,
      user_id: r.user_id != null ? String(r.user_id) : null,
    }));

    // 2. Batch query observations by trace_id
    const traceIds = traces.map((t) => t.trace_id).filter(Boolean);
    if (traceIds.length === 0) return { traces, observations: [] };

    const obsPlaceholders = traceIds.map(() => "?").join(",");
    let obsRows: Row[] = [];
    try {
      obsRows = await this.db.query<Row>(
        `SELECT observation_id, trace_id, parent_observation_id, type, name,
                start_time, end_time, input, output, model, status_message,
                usage_input_tokens, usage_output_tokens, usage_total_tokens, latency
         FROM aw_langfuse_observation
         WHERE trace_id IN (${obsPlaceholders})
         ORDER BY start_time ASC`,
        traceIds,
      );
    } catch {
      // Observations table might not exist in some deployments
    }

    const observations: LangfuseObservationRow[] = obsRows.map((r) => ({
      observation_id: String(r.observation_id ?? ""),
      trace_id: String(r.trace_id ?? ""),
      parent_observation_id: r.parent_observation_id != null ? String(r.parent_observation_id) : null,
      type: r.type != null ? String(r.type) : null,
      name: r.name != null ? String(r.name) : null,
      start_time: r.start_time != null ? Number(r.start_time) : null,
      end_time: r.end_time != null ? Number(r.end_time) : null,
      input: r.input != null ? String(r.input) : null,
      output: r.output != null ? String(r.output) : null,
      model: r.model != null ? String(r.model) : null,
      status_message: r.status_message != null ? String(r.status_message) : null,
      usage_input_tokens: r.usage_input_tokens != null ? Number(r.usage_input_tokens) : null,
      usage_output_tokens: r.usage_output_tokens != null ? Number(r.usage_output_tokens) : null,
      usage_total_tokens: r.usage_total_tokens != null ? Number(r.usage_total_tokens) : null,
      latency: r.latency != null ? Number(r.latency) : null,
    }));

    return { traces, observations };
  }

  private buildFailureSummary(
    nodeExecutions: Row[],
    flowEvents: Row[],
    runLogs: RunLogRow[],
    observations: LangfuseObservationRow[],
  ): FailureSummary {
    const failedNodes = nodeExecutions.filter((ne) => ne.status === "failed");
    const errorLogs = runLogs.filter((l) => l.level === "error");

    const failedNodeInfos: FailedNodeInfo[] = failedNodes.map((fn) => {
      const nodeId = String(fn.node_id ?? "");
      const relatedErrorLogs = errorLogs.filter(
        (l) => l.node_id === nodeId || l.message.includes(nodeId),
      );

      return {
        nodeId,
        nodeTitle: fn.node_title != null ? String(fn.node_title) : null,
        executorType: fn.executor_type != null ? String(fn.executor_type) : null,
        error: fn.error_text != null ? String(fn.error_text) : null,
        attempt: Number(fn.attempt ?? 1),
        embeddedSessionKey: fn.embedded_session_key != null ? String(fn.embedded_session_key) : null,
        relatedErrorLogs,
      };
    });

    // Generate root cause hints
    const rootCauseHints = this.generateRootCauseHints(failedNodes);

    // Build error timeline
    const errorTimeline = this.buildErrorTimeline(flowEvents, errorLogs);

    return {
      failedNodeCount: failedNodes.length,
      failedNodes: failedNodeInfos,
      rootCauseHints,
      errorTimeline,
    };
  }

  private generateRootCauseHints(failedNodes: Row[]): string[] {
    const hints: string[] = [];
    for (const fn of failedNodes) {
      const nodeId = String(fn.node_id ?? "");
      const errorText = fn.error_text != null ? String(fn.error_text) : "";
      if (!errorText) continue;

      if (errorText.includes("TypeError") || errorText.includes("Cannot read properties of")) {
        hints.push(`[${nodeId}] 疑似空值引用: ${errorText.slice(0, 200)}`);
      }
      if (errorText.includes("JSON.parse")) {
        hints.push(`[${nodeId}] JSON 解析失败，可能上游节点输出了非 JSON 格式数据`);
      }
      if (errorText.includes("timeout") || errorText.includes("TimeoutError")) {
        hints.push(`[${nodeId}] 执行超时，可能是 LLM 响应过慢或网络问题`);
      }
      if (errorText.includes("embedded-agent execution failed")) {
        hints.push(`[${nodeId}] embedded-agent 失败，检查 Langfuse observations 中的工具调用`);
      }
      if (errorText.includes("Output contract validation failed")) {
        hints.push(`[${nodeId}] 输出契约验证失败，检查 embedded-agent 输出格式`);
      }
    }
    return hints;
  }

  private buildErrorTimeline(
    flowEvents: Row[],
    errorLogs: RunLogRow[],
  ): ErrorTimelineEntry[] {
    const timeline: ErrorTimelineEntry[] = [];

    for (const e of flowEvents) {
      const eventType = String(e.event_type ?? "");
      if (!eventType.includes("failed") && !e.error_text) continue;
      const time = Number(e.time ?? 0);
      timeline.push({
        timestamp: time > 0 ? new Date(time * 1000).toISOString() : "",
        event: eventType,
        detail: e.error_text != null
          ? String(e.error_text).slice(0, 200)
          : JSON.stringify(e.data_json ?? {}).slice(0, 200),
      });
    }

    for (const l of errorLogs) {
      timeline.push({
        timestamp: new Date(l.timestamp).toISOString(),
        event: `console.${l.level}`,
        detail: l.message.slice(0, 200),
      });
    }

    timeline.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    return timeline;
  }
}

/**
 * Format a RunArchive into a human-readable summary text.
 */
export function formatArchiveSummary(archive: RunArchive): string {
  const fr = archive.flowRun as Record<string, unknown> | null;
  const flowId = archive.archive.flowId;
  const workflowId = fr?.workflow_id as string ?? "unknown";
  const status = fr?.status as string ?? "unknown";
  const startedAt = fr?.started_at as number | undefined;
  const completedAt = fr?.completed_at as number | undefined;

  const nodeCount = (fr?.node_count as number) ?? 0;
  const succeededCount = (fr?.succeeded_count as number) ?? 0;
  const failedCount = (fr?.failed_count as number) ?? 0;

  const lines: string[] = [
    `## 工作流运行档案`,
    ``,
    `**FlowId**: ${flowId}`,
    `**WorkflowId**: ${workflowId}`,
    `**状态**: ${status}`,
    `**节点数**: ${nodeCount} (成功: ${succeededCount}, 失败: ${failedCount})`,
    ``,
  ];

  if (archive.failureSummary.failedNodeCount > 0) {
    lines.push(`### 失败节点`);
    lines.push(``);
    for (const fn of archive.failureSummary.failedNodes) {
      lines.push(`- **${fn.nodeId}** (${fn.executorType ?? "unknown"}): ${fn.error?.slice(0, 100) ?? "无错误信息"}`);
    }
    lines.push(``);
  }

  if (archive.failureSummary.rootCauseHints.length > 0) {
    lines.push(`### 根因提示`);
    lines.push(``);
    archive.failureSummary.rootCauseHints.forEach((h, i) => {
      lines.push(`${i + 1}. ${h}`);
    });
    lines.push(``);
  }

  lines.push(`### 数据汇总`);
  lines.push(``);
  lines.push(`| 数据源 | 记录数 |`);
  lines.push(`|--------|--------|`);
  lines.push(`| flow_events | ${archive.flowEvents.length} |`);
  lines.push(`| node_executions | ${archive.nodeExecutions.length} |`);
  lines.push(`| node_step_traces | ${archive.nodeStepTraces.length} |`);
  lines.push(`| run_logs | ${archive.runLogs.length} |`);
  lines.push(`| langfuse_traces | ${archive.langfuseTraces.length} |`);
  lines.push(`| langfuse_observations | ${archive.langfuseObservations.length} |`);
  lines.push(`| execution_step_log | ${archive.executionStepLogs.length} |`);

  if (archive.archive.errors.length > 0) {
    lines.push(``);
    lines.push(`### 警告`);
    lines.push(``);
    for (const e of archive.archive.errors) {
      lines.push(`- ${e}`);
    }
  }

  return lines.join("\n");
}
