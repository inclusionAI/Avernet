/** @jest-environment jsdom */
// TaskInfoSection 委托 TaskInfoTab 渲染任务信息。
import { TaskInfoSection } from '@/assets/TaskPanel/v2/TaskInfoSection';
import type { TaskView } from '@/assets/TaskPanel/types';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

const task = {
  acceptances: ['输出结论'],
  artifacts: [],
  createdAt: '2026-10-20T10:00:00.000Z',
  dagEdges: [],
  dagNodes: [],
  description: '任务背景描述',
  finishedAt: null,
  goal: '输出决策建议',
  loopRound: 1,
  name: '竞品分析',
  needsAttention: false,
  nodes: [],
  objective: '输出决策建议',
  ownerBotId: 'bot-1',
  ownerBotName: '规划师 Bot',
  ownerUserId: 'user-1',
  progress: {
    total: 1,
    pending: 0,
    planning: 0,
    running: 0,
    done: 1,
    failed: 0,
    hung: 0,
    skipped: 0,
    percent: 100,
  },
  sourceLabel: 'Bot',
  status: 'DONE' as const,
  taskType: 'workflow',
  taskTypeLabel: '工作流',
} as unknown as TaskView;

describe('TaskInfoSection', () => {
  it('渲染任务信息详情', () => {
    render(<TaskInfoSection task={task} />);

    expect(screen.getByTestId('task-info-section')).toBeInTheDocument();
    expect(screen.getByText('任务信息')).toBeInTheDocument();
    expect(screen.getByText('输出决策建议')).toBeInTheDocument();
    expect(screen.getByText('规划师 Bot')).toBeInTheDocument();
  });
});
