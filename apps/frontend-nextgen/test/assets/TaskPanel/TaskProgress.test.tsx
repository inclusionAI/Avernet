/** @jest-environment jsdom */
import { MarkdownCell } from '@/assets/TaskPanel/MarkdownCell';
import { TaskInfoTab, formatRuntimeDuration } from '@/assets/TaskPanel/TaskInfoTab';
import { truncateText } from '@/assets/TaskPanel/text';
import type { TaskView } from '@/assets/TaskPanel/types';

import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';



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
    ownerUserId: 'user-1',
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

  it('运行时长覆盖空开始时间、无效时间和秒级时长', () => {
    expect(formatRuntimeDuration(null, '2026-08-22T10:00:00+08:00')).toBe('—');
    expect(formatRuntimeDuration('invalid', '2026-08-22T10:00:00+08:00')).toBe('—');
    expect(formatRuntimeDuration('2026-08-22T10:00:20+08:00', '2026-08-22T10:00:00+08:00')).toBe('—');
    expect(formatRuntimeDuration('2026-08-22T10:00:00+08:00', '2026-08-22T10:00:20+08:00')).toBe('20秒');
  });

  it('workflow 且带模板与父任务时展示关联元信息', () => {
    const task = makeTask([]);
    task.taskType = 'workflow';
    task.template = '应急处理模板';
    task.parentTaskId = 'parent-task';
    render(<TaskInfoTab task={task} />);

    expect(screen.getByText('关联模板')).toBeInTheDocument();
    expect(screen.getByText('应急处理模板')).toBeInTheDocument();
    expect(screen.getByText('父任务')).toBeInTheDocument();
    expect(screen.getByText('parent-task')).toBeInTheDocument();
  });

  it('无验收标准与信息字段时渲染空态和占位符', () => {
    const task = makeTask([]);
    task.acceptances = [];
    task.description = '';
    task.goal = '';
    task.createdAt = '';
    render(<TaskInfoTab task={task} />);

    expect(screen.getByText('暂无')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('运行中的任务按当前时间计算运行时长', () => {
    expect(
      formatRuntimeDuration('2026-08-22T10:00:00+08:00', null, new Date('2026-08-22T10:01:05+08:00').getTime()),
    ).toBe('1分5秒');
  });

  it('覆盖整分(seconds=0)、整小时与带余分钟分支', () => {
    // 正好整分 → `${minutes}分钟`(命中 seconds 三元 false 臂)
    expect(formatRuntimeDuration('2026-08-22T10:00:00+08:00', '2026-08-22T10:02:00+08:00')).toBe('2分钟');
    // 整小时 → `${hours}小时`(命中 remainingMinutes 三元 false 臂)
    expect(formatRuntimeDuration('2026-08-22T10:00:00+08:00', '2026-08-22T11:00:00+08:00')).toBe('1小时');
    // 带余分钟 → `${hours}小时${remainingMinutes}分钟`(命中 remainingMinutes 三元 true 臂)
    expect(formatRuntimeDuration('2026-08-22T10:00:00+08:00', '2026-08-22T11:05:00+08:00')).toBe('1小时5分钟');
  });
});

describe('MarkdownCell', () => {
  it('按 Markdown View 样式渲染标题和列表', () => {
    render(<MarkdownCell content={'# 实施计划\n\n- 平台差异化执行'} />);

    expect(screen.getByRole('heading', { name: '实施计划' })).toBeInTheDocument();
    expect(screen.getByText('平台差异化执行')).toBeInTheDocument();
  });
});

  it('空内容渲染占位符，长内容可展开后收起', () => {
    const { rerender } = render(<MarkdownCell content="   " />);
    expect(screen.getByText('—')).toBeInTheDocument();

    rerender(
      <MarkdownCell content={'## 完整内容\n\n- 第一条\n- 第二条\n- 第三条\n- 第四条\n- 第五条'} />,
    );
    const expanded = screen.getByRole('button', { name: '展开全部 ▾' });
    fireEvent.click(expanded);
    expect(screen.getByRole('button', { name: '收起 ▴' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '收起 ▴' }));
    expect(screen.getByRole('button', { name: '展开全部 ▾' })).toBeInTheDocument();
  });

describe('truncateText', () => {
  it('按 Unicode 字符截断,省略号计入最大长度', () => {
    expect(truncateText('这是一个特别长的节点标题要截断', 8)).toBe('这是一个特别长…');
    expect(truncateText('短', 8)).toBe('短');
    expect(truncateText('', 8)).toBe('');
    expect(truncateText(null, 8)).toBe('');
  });
});
