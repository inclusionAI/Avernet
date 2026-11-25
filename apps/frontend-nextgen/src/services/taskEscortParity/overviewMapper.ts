import { parseOverviewTimestamp } from '@/domain/taskEscortParity/overview';
import type {
  OverviewNodeExecution,
  OverviewRun,
  OverviewRuns,
  OverviewWorkflow,
} from '@/domain/taskEscortParity/overviewTypes';
import { count, list, metric, record, requiredText, strings, text } from './overviewValidation';

export function mapOverviewRun(value: unknown): OverviewRun {
  const row = record(value);
  return {
    id: requiredText(row.flow_id, '运行 ID'),
    workflowId: requiredText(row.workflow_id, '工作流 ID'),
    workflowTitle: text(row.workflow_title),
    status: requiredText(row.status, '运行状态'),
    nodeCount: count(row.node_count),
    succeededCount: count(row.succeeded_count),
    failedCount: count(row.failed_count),
    startedAt: parseOverviewTimestamp(row.started_at),
    completedAt: parseOverviewTimestamp(row.completed_at),
    durationMs: metric(row.total_duration_ms),
    totalTokens: metric(row.total_token_usage),
    initiator: text(row.user_id) ?? text(row.triggered_by),
    botId: text(row.origin_bot_id),
    pluginVersion: text(row.plugin_version),
    version: row.workflow_version === -1 ? -1 : count(row.workflow_version),
    engine: text(row.engine),
    analysisStatus: text(row.evolution_analysis_status),
  };
}
export function mapOverviewRuns(value: unknown, workflowId: string): OverviewRuns {
  const response = record(value),
    total = count(response.total);
  if (total === null) throw new Error('运行总条数字段缺失或无效');
  const runs = list(response.runs).map(mapOverviewRun);
  if (runs.some((run) => run.workflowId !== workflowId)) throw new Error('运行列表不属于当前工作流');
  let statusCounts: Record<string, number> | null = null;
  if (response.statusCounts && typeof response.statusCounts === 'object' && !Array.isArray(response.statusCounts)) {
    const entries = Object.entries(response.statusCounts);
    if (entries.every(([, value]) => count(value) !== null))
      statusCounts = Object.fromEntries(entries) as Record<string, number>;
  }
  return { runs, total, statusCounts };
}
export function mapOverviewNode(value: unknown): OverviewNodeExecution {
  const row = record(value),
    nodeId = requiredText(row.node_id, '节点 ID'),
    attempt = count(row.attempt);
  return {
    id: `${nodeId}:${attempt ?? 'unknown'}`,
    runId: requiredText(row.flow_id, '运行 ID'),
    nodeId,
    title: text(row.node_title),
    status: requiredText(row.status, '节点状态'),
    executor: text(row.executor_type),
    initiator: text(row.triggered_by),
    phase: text(row.phase),
    attempt,
    durationMs: metric(row.duration_ms),
    startedAt: parseOverviewTimestamp(row.started_at),
    completedAt: parseOverviewTimestamp(row.completed_at),
    input: text(row.input_json),
    output: text(row.output_json),
    error: text(row.error_text),
    tokens: text(row.token_usage_json),
    context: text(row.system_context_json),
    prompt: text(row.resolved_prompt),
    progress: text(row.progress_message),
    sessionKey: text(row.embedded_session_key) ?? text(row.session_key),
    sessionId: text(row.session_id),
  };
}
export function mapOverviewWorkflow(value: unknown, workflowId: string): OverviewWorkflow {
  const row = record(value),
    id = requiredText(row.id, '工作流 ID');
  if (id !== workflowId) throw new Error('工作流规格不属于当前工作流');
  const nodes = list(row.nodes).map((value) => {
    const node = record(value),
      id = requiredText(node.id, '规格节点 ID');
    return {
      id,
      title: text(node.title) ?? id,
      executor: node.executor && typeof node.executor === 'object' ? text(record(node.executor).type) : null,
      dependsOn: strings(node.dependsOn),
    };
  });
  if (new Set(nodes.map((node) => node.id)).size !== nodes.length) throw new Error('工作流规格包含重复节点 ID');
  return { id, version: text(row.version), nodes };
}
