import type {
  OverviewController,
  OverviewIdentityParams,
  OverviewRunsParams,
  OverviewWindowParams,
} from '@/services/backendApi/harnessFlow/overviewController';
import { overviewController } from '@/services/backendApi/harnessFlow/overviewController';
import { mapOverviewHealth, mapOverviewNodeStats, mapOverviewTrend } from './overviewAnalytics';
import { mapOverviewArchive, mapOverviewLogs } from './overviewEvidence';
import { mapOverviewNode, mapOverviewRun, mapOverviewRuns, mapOverviewWorkflow } from './overviewMapper';
import { list, payload, record } from './overviewValidation';

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
      const run = mapOverviewRun(response.run);
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
      return mapOverviewLogs(payload(await api.timeline(runId, params, signal)), runId);
    },
    async archive(workflowId: string, runId: string, params: OverviewIdentityParams, signal?: AbortSignal) {
      return mapOverviewArchive(payload(await api.archive(runId, params, signal)), workflowId, runId);
    },
  };
}
export const overviewService = createOverviewService();
