import type { Issue, Run, RunStatus } from '@/domain/taskEscortWorkspace/types';

export function filterRuns(runs: Run[], filters: { query: string; status: string; workflowId: string }): Run[] {
  const query = filters.query.trim().toLocaleLowerCase();
  return runs.filter(
    (run) =>
      (!filters.status || filters.status === 'all' || run.status === filters.status) &&
      (!filters.workflowId || filters.workflowId === 'all' || run.workflowId === filters.workflowId) &&
      (!query || [run.id, run.name, run.bot, run.node, run.trigger].join(' ').toLocaleLowerCase().includes(query)),
  );
}

export function getRunMetrics(runs: Run[]) {
  const completed = runs.filter((run) => run.status === 'success' || run.status === 'failed');
  const timed = completed.filter(
    (run): run is Run & { duration: number } =>
      run.duration !== null && Number.isFinite(run.duration) && run.duration >= 0,
  );
  const successes = completed.filter((run) => run.status === 'success').length;
  return {
    total: runs.length,
    successRate: completed.length ? Math.round((successes / completed.length) * 1000) / 10 : 0,
    active: runs.filter((run) => run.status === 'running' || run.status === 'waiting').length,
    failed: runs.filter((run) => run.status === 'failed').length,
    averageDuration: timed.length
      ? Math.round(timed.reduce((total, run) => total + run.duration, 0) / timed.length)
      : 0,
  };
}

export interface RunStep {
  id: string;
  name: string;
  status: RunStatus;
  detail: string;
}

/** Illustrative topology, using only the fixture's real terminal/current state. */
export function buildRunSteps(run: Run): RunStep[] {
  const names = ['触发与入队', '准备上下文', run.node || '执行任务', '结果检查', '完成与归档'];
  return names.map((name, index) => {
    let status: RunStatus = 'waiting';
    if (run.status === 'unknown') status = 'unknown';
    else if (run.status === 'success') status = 'success';
    else if (run.status !== 'waiting') {
      if (index < 2) status = 'success';
      if (index === 2) status = run.status;
    }
    const detail: Record<RunStatus, string> = {
      success: '演示快照：此步骤已完成',
      failed: '在此节点中断，等待人工排查',
      running: '演示快照：当前执行节点',
      waiting: '尚未执行',
      cancelled: '任务已取消，后续步骤未执行',
      unknown: '状态未知，无法推断节点执行结果',
    };
    return {
      id: `${run.id}-step-${index}`,
      name,
      status,
      detail: detail[status],
    };
  });
}

export function getRunDetailPresentation(
  runs: Run[],
  issues: Issue[],
  selectedId: string | null,
  activeId: string | null,
) {
  const run = runs.find((entry) => entry.id === selectedId);
  if (!run) return null;
  const steps = buildRunSteps(run);
  const selected =
    steps.find((step) => step.id === activeId) ||
    steps.find((step) => ['failed', 'running', 'cancelled'].includes(step.status)) ||
    steps[2];
  return { run, steps, selected, relatedIssues: issues.filter((issue) => issue.workflowId === run.workflowId) };
}
