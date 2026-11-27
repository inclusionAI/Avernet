import type {
  OverviewController,
  OverviewIdentityParams,
  OverviewRunsParams,
  OverviewWindowParams,
} from '@/services/backendApi/harnessFlow/overviewController';
import { overviewController } from '@/services/backendApi/harnessFlow/overviewController';
import { mapOverviewHealth, mapOverviewNodeStats, mapOverviewTrend } from './overviewAnalytics';
import { mapOverviewArchive, mapOverviewLogs } from './overviewEvidence';
import { mapOverviewDetailedRun, mapOverviewNode, mapOverviewRuns, mapOverviewWorkflow } from './overviewMapper';
import { list, payload, record } from './overviewValidation';
import { displayRunText, redactRunEvidence } from './runLaunchDetails';

/** Bound identity is provided by the page's read context, never read from a global store here. */
export function createOverviewService(api: OverviewController = overviewController) {
  return {
    async runs(params: OverviewRunsParams, signal?: AbortSignal) {
      return mapOverviewRuns(payload(await api.runs(params, signal)), params.workflowId);
    },
    async health(workflowId: string, params: OverviewWindowParams, signal?: AbortSignal) {
      return mapOverviewHealth(payload(await api.health(workflowId, params, signal)));
    },
    async trend(workflowId: string, params: OverviewWindowParams, signal?: AbortSignal) {
      return mapOverviewTrend(payload(await api.trend(workflowId, params, signal)));
    },
    async nodeStats(workflowId: string, params: OverviewWindowParams, signal?: AbortSignal) {
      return mapOverviewNodeStats(payload(await api.nodeStats(workflowId, params, signal)), workflowId);
    },
    async runDetail(workflowId: string, runId: string, params: OverviewIdentityParams, signal?: AbortSignal) {
      const response = record(payload(await api.run(runId, params, signal)));
      const run = mapOverviewDetailedRun(response.run);
      if (run.workflowId !== workflowId) throw new Error('所选运行不属于当前工作流，请返回列表重新选择');
      if (run.id !== runId) throw new Error('运行详情 ID 与当前选择不一致');
      // full=true is the verified endpoint for all attempts, without the default I/O truncation.
      const nodes = list(payload(await api.nodes(runId, params, signal))).map(mapOverviewNode);
      if (nodes.some((node) => node.runId !== runId)) throw new Error('节点执行记录不属于当前运行');
      return { run, nodes };
    },
    async workflow(workflowId: string, params: OverviewIdentityParams, signal?: AbortSignal) {
      return mapOverviewWorkflow(payload(await api.workflow(workflowId, params, signal)), workflowId);
    },
    async logs(runId: string, params: OverviewIdentityParams, signal?: AbortSignal) {
      return mapOverviewLogs(payload(await api.timeline(runId, params, signal)), runId).map((log) => ({
        ...log,
        message: displayRunText(log.message) ?? '',
      }));
    },
    async archive(workflowId: string, runId: string, params: OverviewIdentityParams, signal?: AbortSignal) {
      const archive = mapOverviewArchive(payload(await api.archive(runId, params, signal)), workflowId, runId);
      // Validate identity before redaction; retain evidence, never raw executor/runtime envelopes.
      return {
        ...archive,
        errors: archive.errors.map((value) => displayRunText(value) ?? ''),
        rootCauseHints: archive.rootCauseHints.map((value) => displayRunText(value) ?? ''),
        failedNodes: archive.failedNodes.map((node) => ({ ...node, error: displayRunText(node.error) })),
        analysis: archive.analysis && {
          ...archive.analysis,
          summary: displayRunText(archive.analysis.summary),
          error: displayRunText(archive.analysis.error),
          diagnoses: archive.analysis.diagnoses.map((value) => redactRunEvidence(value)),
        },
        suggestedYaml: archive.suggestedYaml && {
          ...archive.suggestedYaml,
          content: displayRunText(archive.suggestedYaml.content),
          summary: displayRunText(archive.suggestedYaml.summary),
          proposal: redactRunEvidence(archive.suggestedYaml.proposal),
        },
        sections: archive.sections.map((section) => ({ ...section, value: redactRunEvidence(section.value) })),
      };
    },
  };
}
export const overviewService = createOverviewService();
