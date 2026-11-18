import type { Experience, Issue, Run } from '@/domain/taskEscortWorkspace/types';
import {
  experienceCategories,
  filterExperiences,
  getExperienceSummary,
} from '@/services/taskEscortWorkspace/experiencePresentation';
import {
  filterIssues,
  getIssueActions,
  getIssueDetailPresentation,
  getIssueLifecycleMessage,
  getIssueSummary,
  prioritizeIssues,
} from '@/services/taskEscortWorkspace/issuePresentation';
import {
  buildRunSteps,
  filterRuns,
  getRunDetailPresentation,
  getRunMetrics,
} from '@/services/taskEscortWorkspace/runPresentation';
import {
  getWorkbenchPresentation,
  getWorkbenchScope,
  getWorkbenchSummary,
  isActionableIssue,
} from '@/services/taskEscortWorkspace/workbenchPresentation';

const issue: Issue = {
  id: 'issue-1',
  title: '采集超时',
  description: '请求中断',
  workflowId: 'wf-1',
  workflowName: '采集工作流',
  node: '请求节点',
  severity: 'high',
  status: 'open',
  affectedRuns: 5,
  category: '可靠性',
  rootCause: '限流',
  recommendation: '增加退避',
  before: 'retry: 0',
  after: 'retry: 2',
  evidence: ['HTTP 429'],
  confidence: 0.9,
};
const experience: Experience = {
  id: 'exp-1',
  title: '退避重试',
  description: '处理限流',
  category: '可靠性',
  workflowName: '采集工作流',
  reuseCount: 2,
  verified: true,
  sourceIssueId: issue.id,
};
const run: Run = {
  id: 'run-1',
  workflowId: 'wf-1',
  name: '采集工作流',
  bot: '示例助手',
  status: 'failed',
  duration: 90,
  startedAt: '2026-11-17T10:00:00',
  trigger: '手动',
  node: '请求节点',
};

describe('issue presentation', () => {
  test('combines search, workflow and status, including log evidence', () => {
    const input = [issue, { ...issue, id: 'issue-2', workflowId: 'wf-2' }];
    expect(filterIssues(input, { query: '  HTTP 429  ', workflowId: 'wf-1', status: 'open' })).toEqual([issue]);
    expect(filterIssues(input, { query: 'http', workflowId: 'wf-1', status: 'verified' })).toEqual([]);
    expect(filterIssues(input, { query: '', workflowId: 'all', status: 'all' })).toHaveLength(2);
  });
  test.each([
    ['open', true, false, false],
    ['recurring', true, false, false],
    ['applied', false, true, false],
    ['verified', false, false, true],
  ] as const)('%s exposes only legal lifecycle actions', (status, canApply, canVerify, canSave) => {
    expect(getIssueActions({ ...issue, status }, [])).toEqual({ canApply, canVerify, canSave, saved: false });
  });
  test('saved experiences disable duplicate saves and preserve verified copy', () => {
    expect(getIssueActions({ ...issue, status: 'verified' }, [experience])).toEqual({
      canApply: false,
      canVerify: false,
      canSave: false,
      saved: true,
    });
    expect(getIssueLifecycleMessage({ status: 'verified' }, true)).toContain('已保存至经验库');
    expect(getIssueLifecycleMessage({ status: 'verified' }, false)).toContain('尚未保存经验');
    expect(getIssueLifecycleMessage({ status: 'applied' }, false)).toContain('待人工验证');
    expect(getIssueLifecycleMessage({ status: 'recurring' }, false)).toContain('不会自动应用');
    expect(getIssueLifecycleMessage({ status: 'open' }, false)).toContain('异常待处理');
  });
  test('prioritizes recurrence, severity and affected runs without mutating input', () => {
    const low: Issue = { ...issue, id: 'low', severity: 'low' };
    const recurring: Issue = { ...low, id: 'recurring', status: 'recurring' };
    const high: Issue = { ...issue, id: 'high', affectedRuns: 20 };
    const input = [low, issue, recurring, high];
    expect(prioritizeIssues(input).map((entry) => entry.id)).toEqual(['recurring', 'high', issue.id, 'low']);
    expect(input[0]).toBe(low);
    expect(
      getIssueSummary([issue, recurring, { ...issue, status: 'applied' }, { ...issue, status: 'verified' }]),
    ).toEqual({ open: 1, recurring: 1, applied: 1, verified: 1 });
  });
});

describe('workbench presentation', () => {
  test('sidebar count and pending rows use the same predicate; verified stays in history', () => {
    const input: Issue[] = ['open', 'applied', 'recurring', 'verified'].map((status) => ({
      ...issue,
      id: status,
      status: status as Issue['status'],
    }));
    const scoped = getWorkbenchScope(input);
    expect(scoped.actionable).toEqual(input.filter(isActionableIssue));
    expect(scoped.completed.map((entry) => entry.status)).toEqual(['verified']);
    expect(scoped.actionable).toHaveLength(3);
    expect(getWorkbenchScope([])).toEqual({ actionable: [], completed: [] });
  });
});

describe('run presentation', () => {
  test('combines query, status and workflow filters without modifying runs', () => {
    const input = [run, { ...run, id: 'run-2', workflowId: 'wf-2', status: 'success' as const }];
    expect(filterRuns(input, { query: '  请求节点 ', status: 'failed', workflowId: 'wf-1' })).toEqual([run]);
    expect(filterRuns(input, { query: 'missing', status: 'all', workflowId: 'all' })).toEqual([]);
  });
  test('metrics use completed samples, not waiting/running durations', () => {
    const input: Run[] = [
      run,
      { ...run, status: 'success', duration: 30 },
      { ...run, status: 'waiting', duration: 999 },
    ];
    expect(getRunMetrics(input)).toEqual({ total: 3, successRate: 50, active: 1, failed: 1, averageDuration: 60 });
    expect(getRunMetrics([])).toEqual({ total: 0, successRate: 0, active: 0, failed: 0, averageDuration: 0 });
  });
  test.each(['failed', 'running', 'cancelled'] as const)('%s does not imply downstream completion', (status) => {
    expect(buildRunSteps({ ...run, status }).map((step) => step.status)).toEqual([
      'success',
      'success',
      status,
      'waiting',
      'waiting',
    ]);
  });
  test('waiting and success snapshots show honest step states', () => {
    expect(buildRunSteps({ ...run, status: 'waiting' }).every((step) => step.status === 'waiting')).toBe(true);
    expect(buildRunSteps({ ...run, status: 'success' }).every((step) => step.status === 'success')).toBe(true);
    expect(buildRunSteps({ ...run, node: '' })[2].name).toBe('执行任务');
  });
});

describe('experience presentation', () => {
  test('combines search and category; categories are unique and empty states are valid', () => {
    const other = { ...experience, id: 'exp-2', category: '质量' };
    expect(filterExperiences([experience, other], '  采集 ', '可靠性')).toEqual([experience]);
    expect(filterExperiences([experience], 'missing', 'all')).toEqual([]);
    expect(experienceCategories([experience, other, experience])).toEqual(['可靠性', '质量']);
    expect(experienceCategories([])).toEqual([]);
  });
});

test('read-only detail presentation never exposes writes and reports lifecycle progress', () => {
  expect(getIssueDetailPresentation([issue], [], 'missing', true)).toBeNull();
  expect(getIssueDetailPresentation([issue], [], issue.id, false)?.actions).toEqual({
    canApply: false,
    canVerify: false,
    canSave: false,
    saved: false,
  });
  expect(getIssueDetailPresentation([{ ...issue, status: 'applied' }], [], issue.id, true)?.lifecycleStep).toBe(1);
  expect(
    getIssueDetailPresentation([{ ...issue, status: 'verified' }], [experience], issue.id, true)?.lifecycleStep,
  ).toBe(3);
});

test('workbench derives scoped tabs and safely falls back from unknown filters', () => {
  const input: Issue[] = [issue, { ...issue, id: 'done', status: 'verified' }];
  expect(getWorkbenchPresentation(input, { query: '', status: 'unknown', workflowId: 'all' }).issues).toEqual([issue]);
  expect(getWorkbenchPresentation(input, { query: '', status: 'verified', workflowId: 'all' }).issues[0].id).toBe(
    'done',
  );
  expect(getWorkbenchSummary(input)).toMatchObject({
    actionableCount: 1,
    completedCount: 1,
    affectedRuns: 10,
    recommendations: 2,
  });
});

test('run detail defaults to the actual terminal node and honours valid selection', () => {
  expect(getRunDetailPresentation([], [], 'missing', null)).toBeNull();
  const detail = getRunDetailPresentation([run], [issue], run.id, null);
  expect(detail?.selected.name).toBe(run.node);
  expect(detail?.relatedIssues).toEqual([issue]);
  expect(getRunDetailPresentation([run], [], run.id, 'run-1-step-1')?.selected.name).toBe('准备上下文');
});

test('missing durations and unknown statuses do not turn into invented sample evidence', () => {
  expect(
    getRunMetrics([
      { ...run, duration: null },
      { ...run, status: 'success', duration: 20 },
    ]).averageDuration,
  ).toBe(20);
  expect(buildRunSteps({ ...run, status: 'unknown' }).every((step) => step.status === 'unknown')).toBe(true);
});

test('experience summary treats an empty library as an honest zero', () => {
  expect(getExperienceSummary([])).toEqual({ verifiedCount: 0, reuseCount: 0 });
  expect(getExperienceSummary([experience])).toEqual({ verifiedCount: 1, reuseCount: 2 });
});
