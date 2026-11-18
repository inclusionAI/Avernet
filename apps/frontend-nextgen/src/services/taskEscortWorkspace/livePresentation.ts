import type { Run, RunStatus } from '@/domain/taskEscortWorkspace';

const labels: Record<RunStatus, string> = {
  success: '运行成功',
  failed: '运行失败',
  running: '运行中',
  waiting: '等待中',
  cancelled: '已取消',
  unknown: '未知状态',
};
const tones = {
  success: 'success',
  failed: 'error',
  running: 'primary',
  waiting: 'warning',
  cancelled: 'neutral',
  unknown: 'neutral',
} as const;
export function liveRunPresentation(run: Pick<Run, 'status' | 'duration' | 'startedAt'>) {
  const timestamp = run.startedAt ? Date.parse(run.startedAt) : NaN;
  return {
    statusLabel: labels[run.status] ?? labels.unknown,
    tone: tones[run.status] ?? tones.unknown,
    durationLabel: run.duration === null ? '—' : `${Number(run.duration.toFixed(2))} 秒`,
    startedAtLabel: Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString('zh-CN', { hour12: false }) : '—',
  };
}
export function selectLiveRuns(runs: Run[], filters: { query: string; workflowId: string; status: string }) {
  const query = filters.query.trim().toLocaleLowerCase();
  return runs.filter(
    (run) =>
      (filters.workflowId === 'all' || run.workflowId === filters.workflowId) &&
      (filters.status === 'all' || run.status === filters.status) &&
      (!query || [run.id, run.name, run.workflowId].some((value) => value.toLocaleLowerCase().includes(query))),
  );
}
export function summarizeLiveSample(runs: Run[]) {
  return {
    count: runs.length,
    failed: runs.filter((run) => run.status === 'failed').length,
    running: runs.filter((run) => run.status === 'running').length,
    unknown: runs.filter((run) => run.status === 'unknown').length,
  };
}
