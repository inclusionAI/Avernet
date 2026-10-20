/** @jest-environment jsdom */
import { MarkdownCell } from '@/assets/TaskPanel/MarkdownCell';
import { TaskInfoTab, formatRuntimeDuration } from '@/assets/TaskPanel/TaskInfoTab';
import { truncateText } from '@/assets/TaskPanel/text';
import type { TaskView } from '@/assets/TaskPanel/types';

import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';



function makeTask(nodes: TaskView['nodes']): TaskView {
  return {
    id: 'task-1',
    name: '突发 Case 处置',
    description: '任务描述',
    goal: '定位根因',
    objective: '定位根因',
    acceptances: ['确认连接池耗尽原因'],
    status: 'EXECUTING',
    taskType: 'dynamic',
    taskTypeLabel: '动态任务',
    sourceLabel: 'Bot 对话',
    ownerBotName: '我的助手',
    ownerBotId: 'bot-1',
    createdAt: '2026-08-22T10:00:00+08:00',
    finishedAt: null,
    loopRound: 1,
    needsAttention: false,
    progress: { total: nodes.length, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    artifacts: [],
    nodes,
    dagNodes: [],
    dagEdges: [],
  } as TaskView;
}

describe('TaskInfoTab metadata', () => {
  it('按要求展示任务类型、创建时间、结束时间和运行时长，不展示发起会话', () => {
    const task = makeTask([]);
    task.finishedAt = '2026-08-22T10:04:00+08:00';
    task.mainSessionName = '不应展示的发起会话';

    render(<TaskInfoTab task={task} />);

    expect(screen.getByText('任务类型')).toBeInTheDocument();
    expect(screen.getByText('动态任务')).toBeInTheDocument();
    expect(screen.getByText('创建时间')).toBeInTheDocument();
    expect(screen.getByText('结束时间')).toBeInTheDocument();
    expect(screen.getByText('运行时长')).toBeInTheDocument();
    expect(screen.getByText('Owner Bot')).toBeInTheDocument();
    expect(screen.getByText('来源')).toBeInTheDocument();
    expect(screen.getByText('4分钟')).toBeInTheDocument();
    expect(screen.queryByText('发起会话')).not.toBeInTheDocument();
    expect(screen.queryByText('不应展示的发起会话')).not.toBeInTheDocument();
  });

  it('任务信息字段内容字号不大于任务目标和任务描述标题', () => {
    render(<TaskInfoTab task={makeTask([])} />);

    expect(screen.getByText('任务目标')).toHaveStyle('font-size: 11px');
    screen.getAllByText('任务描述').forEach((element) => expect(element).toHaveStyle('font-size: 11px'));
    expect(screen.getByText('定位根因')).toHaveStyle('font-size: 11px');
    expect(screen.getByText('动态任务')).toHaveStyle('font-size: 11px');
  });

  it('运行中的任务按当前时间计算运行时长', () => {
    expect(
      formatRuntimeDuration('2026-08-22T10:00:00+08:00', null, new Date('2026-08-22T10:01:05+08:00').getTime()),
    ).toBe('1分5秒');
  });
});

describe('MarkdownCell', () => {
  it('按 Markdown View 样式渲染标题和列表', () => {
    render(<MarkdownCell content={'# 实施计划\n\n- 平台差异化执行'} />);

    expect(screen.getByRole('heading', { name: '实施计划' })).toBeInTheDocument();
    expect(screen.getByText('平台差异化执行')).toBeInTheDocument();
  });
});

describe('truncateText', () => {
  it('按 Unicode 字符截断,省略号计入最大长度', () => {
    expect(truncateText('这是一个特别长的节点标题要截断', 8)).toBe('这是一个特别长…');
    expect(truncateText('短', 8)).toBe('短');
    expect(truncateText('', 8)).toBe('');
    expect(truncateText(null, 8)).toBe('');
  });
});
