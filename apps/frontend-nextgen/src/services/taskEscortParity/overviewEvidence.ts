import { parseOverviewTimestamp } from '@/domain/taskEscortParity/overview';
import type { OverviewArchive, OverviewLog } from '@/domain/taskEscortParity/overviewTypes';
import { count, list, metric, record, strings, text } from './overviewValidation';

export function mapOverviewLogs(value: unknown, runId: string): OverviewLog[] {
  const row = record(value);
  if (row.flowId !== runId) throw new Error('日志不属于当前运行');
  return list(row.events)
    .flatMap((value, index) => {
      const event = record(value);
      if (event.source !== 'run_log') return [];
      const details = event.payload && typeof event.payload === 'object' ? record(event.payload) : {};
      return [
        {
          id: text(event.id) ?? `log-${index}`,
          timestamp: parseOverviewTimestamp(event.timestamp),
          relativeMs: metric(event.relativeMs),
          nodeId: text(event.nodeId),
          level: text(details.level) ?? text(event.displayType) ?? 'unknown',
          source: text(details.source),
          message: text(event.detail) ?? text(details.message) ?? '',
        },
      ];
    })
    .sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
}
export function mapOverviewArchive(value: unknown, workflowId: string, runId: string): OverviewArchive {
  const row = record(value),
    archive = record(row.archive);
  if (archive.flowId !== runId) throw new Error('档案不属于当前运行');
  if (row.flowRun !== null && row.flowRun !== undefined) {
    const run = record(row.flowRun);
    if (run.flow_id !== runId || run.workflow_id !== workflowId) throw new Error('档案不属于当前工作流或运行');
  }
  const failure = row.failureSummary && typeof row.failureSummary === 'object' ? record(row.failureSummary) : {};
  const analysis = row.analysis && typeof row.analysis === 'object' ? record(row.analysis) : null;
  const yaml = row.suggestedYaml && typeof row.suggestedYaml === 'object' ? record(row.suggestedYaml) : null;
  const labels: Array<[string, string]> = [
    ['flowRun', '流程信息'],
    ['nodeExecutions', '节点执行'],
    ['flowEvents', '事件流'],
    ['nodeStepTraces', '步骤追踪'],
    ['executionStepLogs', '执行步骤日志'],
    ['runLogs', '运行日志'],
    ['langfuseTraces', 'Trace'],
    ['langfuseObservations', 'Observations'],
  ];
  return {
    id: text(archive.archiveId) ?? runId,
    status: text(archive.status),
    createdAt: parseOverviewTimestamp(archive.createdAt),
    errors: strings(archive.errors),
    failedNodeCount: count(failure.failedNodeCount),
    rootCauseHints: strings(failure.rootCauseHints),
    failedNodes: Array.isArray(failure.failedNodes)
      ? failure.failedNodes.map((value) => {
          const node = record(value);
          return {
            nodeId: text(node.nodeId) ?? '未知节点',
            title: text(node.nodeTitle),
            error: text(node.error),
            attempt: count(node.attempt),
          };
        })
      : [],
    analysis: analysis
      ? {
          source: text(analysis.source),
          status: text(analysis.status),
          summary: text(analysis.summary),
          error: text(analysis.error),
          diagnoses: Array.isArray(analysis.diagnoses) ? analysis.diagnoses : [],
        }
      : null,
    suggestedYaml: yaml
      ? {
          content: text(yaml.yamlContent),
          summary: text(yaml.summary),
          confidence: text(yaml.confidence),
          proposal: yaml.patchProposal ?? null,
        }
      : null,
    sections: labels
      .filter(([key]) => row[key] !== undefined && row[key] !== null)
      .map(([key, label]) => ({ key, label, value: row[key] })),
  };
}
