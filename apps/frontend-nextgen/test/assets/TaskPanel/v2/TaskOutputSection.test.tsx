/** @jest-environment jsdom */
// TaskOutputSection:承接原产物 Tab 的根节点任务产出渲染(维度卡片/Markdown,经 MarkdownCell 出 HTML)。
import { TaskOutputSection } from '@/assets/TaskPanel/v2/TaskOutputSection';
import type { TaskView } from '@/assets/TaskPanel/types';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

const task = (overrides?: Partial<TaskView>): TaskView =>
  ({
    id: 't1',
    name: '任务',
    description: '',
    goal: '',
    objective: '',
    acceptances: [],
    status: 'DONE',
    taskType: 'dynamic',
    taskTypeLabel: '动态',
    sourceLabel: '',
    ownerBotName: 'Bot',
    ownerBotId: 'b1',
    createdAt: '',
    finishedAt: null,
    loopRound: 1,
    needsAttention: false,
    progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    artifacts: [],
    nodes: [],
    dagNodes: [],
    dagEdges: [],
    ...overrides,
  }) as unknown as TaskView;

describe('TaskOutputSection', () => {
  it('维度卡片优先:rootOutputDimensions 非空逐维度渲染内容', () => {
    render(
      <TaskOutputSection
        task={task({
          rootOutputDimensions: [
            { key: 'result', content: '# 结论正文' },
            { key: 'handoff', content: '交接说明正文' },
          ],
          rootOutputRender: '# 整体产出',
        })}
      />,
    );
    // markdown-it 渲染为标签文本:h1 结论正文 / p 交接说明正文;整块回退不渲染
    expect(screen.getByText('结论正文')).toBeTruthy();
    expect(screen.getByText('交接说明正文')).toBeTruthy();
    expect(screen.queryByText('整体产出')).toBeNull();
  });

  it('无维度时回退整块 rootOutputRender 渲染', () => {
    render(<TaskOutputSection task={task({ rootOutputRender: '# 整体产出', rootOutputDimensions: undefined })} />);
    expect(screen.getByText('整体产出')).toBeTruthy();
  });

  it('全空 → 不渲染区块', () => {
    const { container } = render(<TaskOutputSection task={task()} />);
    expect(container.querySelector('[data-testid="task-output-section"]')).toBeNull();
  });
});