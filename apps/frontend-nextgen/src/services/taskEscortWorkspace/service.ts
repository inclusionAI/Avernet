import type {
  DomainRunDetail,
  Metrics,
  TaskEscortOperationResult,
  TaskEscortWorkspaceMode,
} from '@/domain/taskEscortWorkspace';
import { emptyWorkspaceSnapshot, getWorkspaceSupport } from '@/domain/taskEscortWorkspace';
import { immutableCopy } from '@/domain/taskEscortWorkspace/immutable';
import { createTaskEscortWorkspaceStore } from '@/stores/taskEscortWorkspaceStore';
import { defaultWorkspaceApi } from './api';
import { mapRun, mapRunDetail, mapWorkflow } from './mapper';
import type { TaskEscortWorkspaceApi, TaskEscortWorkspaceService } from './types';

const unavailable = (): TaskEscortOperationResult => ({
  ok: false,
  message: '此真实写入能力暂未接入，首阶段仅支持只读查看',
});
const unavailableMetrics: Metrics = immutableCopy({
  available: false,
  scope: 'unavailable',
  reason: '真实全量统计暂未接入；请使用明确标注的只读运行样本',
  runs: 0,
  successRate: 0,
  anomalies: 0,
  p50: 0,
  running: 0,
  waiting: 0,
  blocked: 0,
  queued: 0,
  series: [],
  successSeries: [],
  labels: [],
});
const errorMessage = (error: unknown): string => {
  if (error instanceof Error) return error.message || '工作流与运行样本加载失败';
  if (typeof error === 'string' && error) return error;
  return '工作流与运行样本加载失败';
};

export function createTaskEscortWorkspaceService(
  _requestedMode: TaskEscortWorkspaceMode = 'live',
  injectedApi?: TaskEscortWorkspaceApi,
): TaskEscortWorkspaceService {
  // Keep the legacy argument for callers, but never permit synthetic runtime data.
  void _requestedMode;
  const mode = 'live';
  const store = createTaskEscortWorkspaceStore(emptyWorkspaceSnapshot());
  const api = injectedApi ?? defaultWorkspaceApi;
  let generation = 0;
  let latestLoad = 0;
  const getSnapshot = () => store.getState().snapshot;
  const cancelPending = () => {
    generation += 1;
    latestLoad += 1;
    if (getSnapshot().loading) store.getState().setLoading(false);
  };
  return {
    mode,
    support: getWorkspaceSupport(mode),
    getSnapshot,
    subscribe: (listener) => store.subscribe(listener),
    async load(): Promise<void> {
      const currentGeneration = generation;
      const loadId = ++latestLoad;
      const current = () => generation === currentGeneration && latestLoad === loadId;
      store.getState().setSnapshot({ ...emptyWorkspaceSnapshot(), loading: true });
      try {
        const [workflowRows, runRows] = await Promise.all([api.listWorkflows(), api.listFlowRuns({ limit: 100 })]);
        if (!current()) return;
        if (!Array.isArray(workflowRows) || !Array.isArray(runRows))
          throw new Error('工作流与运行列表返回了非预期的数据结构');
        const workflows = workflowRows.map((row) => mapWorkflow(row, runRows));
        const runs = runRows.map((row) => mapRun(row, workflows));
        store.getState().setSnapshot({ ...emptyWorkspaceSnapshot(), workflows, runs });
      } catch (error) {
        if (current()) store.getState().setSnapshot({ ...emptyWorkspaceSnapshot(), error: errorMessage(error) });
      }
    },
    cancelPending,
    async getRunDetail(flowId: string): Promise<DomainRunDetail> {
      if (!flowId.trim()) throw new Error('运行 ID 不能为空');
      const currentGeneration = generation;
      const detail = await api.getFlowRun(flowId);
      if (generation !== currentGeneration) throw new Error('运行详情读取已取消 (cancelled)');
      return mapRunDetail(detail, getSnapshot().workflows);
    },
    getMetrics: () => unavailableMetrics,
    applySuggestion: unavailable,
    verifyIssue: unavailable,
    saveExperience: unavailable,
    updateSettings: unavailable,
    saveSettings: unavailable,
    reset: () => {
      cancelPending();
      store.getState().reset();
    },
  };
}
