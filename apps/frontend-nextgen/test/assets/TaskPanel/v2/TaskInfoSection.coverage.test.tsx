/** @jest-environment jsdom */
// TaskInfoSection 覆盖补齐:默认折叠 details 渲染摘要行与 TaskInfoTab 内容。
import type { TaskView } from '@/assets/TaskPanel/types';
import { TaskInfoSection } from '@/assets/TaskPanel/v2/TaskInfoSection';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

const task = {
  id: 't1',
  name: '竞品分析',
  description: '对比主流产品的能力矩阵',
  goal: '输出竞品调研结论',
  objective: '',
  acceptances: ['结论覆盖 5 个竞品'],
  status: 'EXECUTING',
  taskType: 'dynamic',
  taskTypeLabel: '动态',
  sourceLabel: '',
  ownerBotId: 'bot-1',
  ownerBotName: '规划师',
  ownerUserId: 'user-1',
  createdAt: '2026-10-20T08:00:00.000Z',
  finishedAt: null,
  loopRound: 1,
  needsAttention: false,
  artifacts: [],
  derivedSessionIds: [],
  dagNodes: [],
  dagEdges: [],
  nodes: [],
} as unknown as TaskView;

describe('TaskInfoSection 覆盖补齐', () => {
  it('渲染折叠的任务信息区,summary 可见且内容区默认收起', () => {
    const { container } = render(<TaskInfoSection task={task} />);

    expect(screen.getByTestId('task-info-section')).toBeInTheDocument();
    expect(screen.getByText('任务信息')).toBeInTheDocument();
    // 折叠态:details 未 open,正文不参与展示
    const details = container.querySelector('details');
    expect(details).not.toBeNull();
    expect(details?.hasAttribute('open')).toBe(false);
  });

  it('宿主容器展开时可直接读取任务信息正文(内容始终在文档内)', () => {
    const { container } = render(<TaskInfoSection task={task} />);
    const details = container.querySelector('details');
    details?.setAttribute('open', '');
    expect(screen.getByText('对比主流产品的能力矩阵')).toBeInTheDocument();
    expect(screen.getByText('输出竞品调研结论')).toBeInTheDocument();
  });
});
