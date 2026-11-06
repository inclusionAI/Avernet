/** @jest-environment jsdom */
import { TaskInfoTab } from '@/assets/TaskPanel/TaskInfoTab';
import type { TaskView } from '@/assets/TaskPanel/types';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

// 未覆盖分支补充:workflow 任务携带模板时元信息补「关联模板」一项。

const task = (overrides: Partial<TaskView> = {}): TaskView =>
  ({
    id: 't1',
    name: '周年庆任务',
    description: '任务描述',
    goal: '任务目标',
    objective: '任务目标',
    acceptances: ['验收项一'],
    status: 'EXECUTING',
    taskType: 'workflow',
    taskTypeLabel: '工作流',
    sourceLabel: 'Bot',
    ownerBotName: 'OwnerBot',
    ownerBotId: 'bot-owner',
    ownerUserId: 'user-1',
    createdAt: '2026-11-01T10:00:00+08:00',
    finishedAt: null,
    loopRound: 1,
    needsAttention: false,
    template: 'tpl-anniversary',
    parentTaskId: 'task-parent',
    progress: { total: 1, pending: 0, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    artifacts: [],
    nodes: [],
    dagNodes: [],
    dagEdges: [],
    ...overrides,
  }) as TaskView;

describe('TaskInfoTab 元信息补充项', () => {
  it('workflow 任务且带模板 → 展示「关联模板」;携带父任务 → 展示「父任务」', () => {
    render(<TaskInfoTab task={task()} />);
    expect(screen.getByText('关联模板')).toBeInTheDocument();
    expect(screen.getByText('tpl-anniversary')).toBeInTheDocument();
    expect(screen.getByText('父任务')).toBeInTheDocument();
    expect(screen.getByText('task-parent')).toBeInTheDocument();
  });

  it('非 workflow 或无模板时不出现「关联模板」', () => {
    render(<TaskInfoTab task={task({ taskType: 'dynamic', parentTaskId: null })} />);
    expect(screen.queryByText('关联模板')).toBeNull();
    expect(screen.queryByText('父任务')).toBeNull();
  });
});
