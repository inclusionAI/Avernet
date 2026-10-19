/** @jest-environment jsdom */
import type { Task } from '@/domain/evolution/types';
import TaskDetail from '@/pages/EvolutionFactory/TaskDetail';
import { evolutionApi } from '@/services/evolution/evolutionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { history, useLocation } from '@umijs/max';

jest.mock('@umijs/max', () => {
  const mockJest = require('@jest/globals').jest;
  return {
    history: { push: mockJest.fn(), replace: mockJest.fn() },
    useLocation: mockJest.fn(() => ({ search: '' })),
    useParams: () => ({ taskId: 'task-1' }),
  };
});
jest.mock('@/pages/EvolutionFactory/useEvolutionContext', () => ({
  useEvolutionContext: () => ({
    api: jest.requireMock<typeof import('@/services/evolution/evolutionService')>(
      '@/services/evolution/evolutionService',
    ).evolutionApi,
    ready: true,
    userId: 'owner',
    key: 'owner:42',
    spaceId: 42,
    loading: false,
  }),
}));
jest.mock('@/services/evolution/evolutionService', () => {
  const mockJest = require('@jest/globals').jest;
  return {
    evolutionApi: {
      versions: mockJest.fn().mockResolvedValue({ items: [] }),
      packs: mockJest.fn(),
      task: mockJest.fn(),
      tasks: mockJest.fn(),
      stepAction: mockJest.fn(),
      diff: mockJest.fn(),
    },
  };
});
const api = jest.mocked(evolutionApi);
const task: Task = {
  task_id: 'task-1',
  task_name: '目标进化演示',
  task_type: 'full',
  status: 'completed',
  user_id: 'owner',
  bot_id: 'bot-1',
  created_by: 'owner',
  config: {},
  steps: [
    {
      stepId: 'plan-1',
      stepType: 'plan',
      status: 'succeeded',
      summary: '规划已生成',
      output: { spec: { version: 'v0', content: '# 验证目标\n固定问候' } },
    },
    {
      stepId: 'opt-1',
      stepType: 'optimize',
      roundNo: 1,
      status: 'succeeded',
      summary: '首轮优化',
      output: { scoreComparison: { name: 'test_score', baseline: 0.73, candidate: 0.94, delta: 0.94 - 0.73 } },
    },
  ],
};
beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(useLocation).mockReturnValue({ search: '' } as ReturnType<typeof useLocation>);
  api.task.mockResolvedValue(task);
  api.tasks.mockResolvedValue({ tasks: [], page: 1, pageSize: 1, total: 0, totalPages: 1, canViewAll: false });
});
it('retains module Tabs and distinct PRD sections with real-step progress', async () => {
  render(<TaskDetail />);
  await screen.findByRole('heading', { name: '目标进化演示' });
  expect(screen.getByRole('heading', { name: '智能进化' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: '进化任务' })).toHaveAttribute('aria-selected', 'true');
  for (const name of ['基本信息', '执行进度', '执行时间线', '产出物', '执行日志 · 优化'])
    expect(screen.getByRole('heading', { name })).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '2');
  expect(screen.getByText(/已完成 2 \/ 2 个已生成步骤/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看产出物 · 优化 · opt-1' }));
  expect(await screen.findByText('Test delta')).toBeInTheDocument();
  expect(screen.getByText('+0.21')).toBeInTheDocument();
  expect(task.steps?.[1].output?.scoreComparison).toEqual({
    name: 'test_score',
    baseline: 0.73,
    candidate: 0.94,
    delta: 0.94 - 0.73,
  });
  expect(api.stepAction).not.toHaveBeenCalled();
});
it('opens earlier real output without losing any Step, changing APIs or dispatching a task', async () => {
  render(<TaskDetail />);
  fireEvent.click(await screen.findByRole('button', { name: '查看产出物 · 目标规划 · plan-1' }));
  expect(screen.getByRole('heading', { name: '验证目标' })).toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: '产出物 · 目标规划' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '关闭弹窗' }));
  expect(screen.getByRole('heading', { name: '执行日志 · 目标规划' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '查看产出物 · 优化 · opt-1' })).toBeInTheDocument();
  expect(api.task).toHaveBeenCalledWith('task-1', expect.any(AbortSignal));
  expect(api.stepAction).not.toHaveBeenCalled();
});
it('preserves the existing failed-step retry operation and does not label it a new task-level stop', async () => {
  api.task.mockResolvedValue({
    ...task,
    status: 'failed',
    steps: [{ stepId: 'failed-plan', stepType: 'plan', status: 'failed', error: { message: '执行失败' } }],
  });
  api.stepAction.mockResolvedValue({});
  render(<TaskDetail />);
  fireEvent.click(await screen.findByRole('button', { name: '继续执行' }));
  await waitFor(() => expect(api.stepAction).toHaveBeenCalledWith('task-1', 'failed-plan', 'retry'));
  expect(screen.queryByRole('button', { name: '终止任务' })).not.toBeInTheDocument();
});

it('consumes the creation response for success feedback and never creates again', async () => {
  jest
    .mocked(useLocation)
    .mockReturnValue({ search: '?created=1', state: { createdTask: task, spaceKey: 'owner:42' } } as ReturnType<
      typeof useLocation
    >);
  render(<TaskDetail />);
  expect(screen.getByRole('heading', { name: '任务创建成功' })).toBeInTheDocument();
  expect(screen.getByText(/以下为任务类型的流程说明/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看任务详情' }));
  expect(history.replace).toHaveBeenCalledWith('/evolution-factory/tasks/task-1');
  expect(api.stepAction).not.toHaveBeenCalled();
});
it('displays historical diagnosis-driven full tasks without losing diagnosis steps', async () => {
  api.task.mockResolvedValue({
    ...task,
    config: { inputMode: 'diagnose_goal' },
    steps: [
      { stepId: 'd', stepType: 'diagnose', status: 'succeeded', output: { diagnosis: { summary: '历史诊断' } } },
      ...task.steps!,
    ],
  });
  render(<TaskDetail />);
  fireEvent.click(await screen.findByRole('button', { name: '查看产出物 · Bot 诊断 · d' }));
  expect(screen.getByText('历史诊断')).toBeInTheDocument();
});

it('keeps Diff reading in the artifact dialog on the existing API', async () => {
  api.task.mockResolvedValue({
    ...task,
    steps: [
      {
        ...task.steps![1],
        output: {
          diff: { summary: '更新问候 Skill', files: [{ path: 'skills/greeting/SKILL.md', change: '更新回复规则' }] },
        },
      },
    ],
  });
  api.diff.mockResolvedValue('diff --git a/SKILL.md b/SKILL.md');
  render(<TaskDetail />);
  fireEvent.click(await screen.findByRole('button', { name: '查看产出物 · 优化 · opt-1' }));
  fireEvent.click(screen.getByRole('button', { name: '查看完整 Diff' }));
  await waitFor(() => expect(api.diff).toHaveBeenCalledWith('task-1', 'opt-1'));
  expect(await screen.findAllByText('diff --git a/SKILL.md b/SKILL.md')).not.toHaveLength(0);
  expect(api.stepAction).not.toHaveBeenCalled();
});
