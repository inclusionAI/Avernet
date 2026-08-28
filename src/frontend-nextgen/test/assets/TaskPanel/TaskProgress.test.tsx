/** @jest-environment jsdom */
import { TaskProgressTab } from '@/assets/TaskPanel/TaskProgressTab';
import { truncateText } from '@/assets/TaskPanel/text';
import type { TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

function makeNode(overrides: Partial<TaskNodeView> = {}): TaskNodeView {
  return {
    id: 'node-1',
    name: '异常检测',
    sequence: 1,
    status: 'done',
    executor: '我的助手',
    executorColor: '#165DFF',
    runMode: 'single_bot',
    startedAt: '8月22日 10:00',
    endAt: '8月22日 10:04',
    timeConsuming: '4分钟',
    output: null,
    outputSummary: '发现连接池耗尽',
    taskSpec: {
      title: '异常检测',
      instruction: '拉取日志并识别异常连接。',
      target: '定位连接池耗尽的根因',
      acceptances: ['确认连接池耗尽原因', '输出修复建议'],
    },
    artifacts: [],
    groupId: null,
    sessionId: null,
    hasSubTask: false,
    subTaskId: null,
    stepTraces: [],
    acceptanceResult: null,
    ...overrides,
  };
}

function makeTask(nodes: TaskNodeView[]): TaskView {
  return {
    id: 'task-1',
    name: '突发 Case 处置',
    description: '任务描述',
    goal: '定位根因',
    objective: '定位根因',
    acceptances: [],
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
    progress: {
      total: nodes.length,
      pending: 0,
      planning: 0,
      running: 0,
      done: nodes.length,
      failed: 0,
      hung: 0,
      skipped: 0,
      percent: 100,
    },
    artifacts: [],
    nodes,
    dagNodes: nodes.map((node, index) => ({
      id: node.id,
      label: node.name,
      status: node.status,
      x: 40 + index * 180,
      y: 40,
      isCurrent: false,
    })),
    dagEdges: [],
  };
}

describe('TaskProgressTab', () => {
  it('按字符数截断标题和描述，并保留省略号', () => {
    expect(truncateText('一'.repeat(20), 20)).toBe('一'.repeat(20));
    expect(truncateText('一'.repeat(21), 20)).toBe(`${'一'.repeat(19)}…`);
    expect(truncateText('描述'.repeat(26), 50)).toBe(`${'描述'.repeat(24)}描…`);
  });
  it('节点状态使用完成勾选图标和执行中旋转图标', () => {
    const task = makeTask([
      makeNode({ id: 'node-done', status: 'done', name: '已完成节点' }),
      makeNode({ id: 'node-running', status: 'running', name: '执行中节点' }),
    ]);

    const { container } = render(<TaskProgressTab task={task} />);
    const doneIcon = container.querySelector('svg[aria-label="已完成"]');
    const runningIcon = container.querySelector('svg[aria-label="执行中"]');

    expect(doneIcon).toBeInTheDocument();
    expect(doneIcon).toHaveAttribute('width', '20');
    expect(runningIcon).toBeInTheDocument();
    expect(runningIcon).toHaveAttribute('width', '16');
    expect(runningIcon).toHaveStyle('animation: task-panel-spin 1s linear infinite');
  });

  it('节点带子任务时，点击节点卡片调用子任务下钻回调', () => {
    const onOpenSubTask = jest.fn();
    const task = makeTask([makeNode({ hasSubTask: true, subTaskId: 'task-child-1' })]);

    render(<TaskProgressTab task={task} onOpenSubTask={onOpenSubTask} />);
    fireEvent.click(screen.getByRole('button', { name: '打开子任务 异常检测' }));

    expect(onOpenSubTask).toHaveBeenCalledWith('task-child-1');
  });

  it('coop_group 节点点击 Bot 名称查看执行会话', () => {
    const onOpenGroupSession = jest.fn();
    const node = makeNode({
      runMode: 'coop_group',
      groupId: 'group-1',
      sessionId: 'session-1',
      executor: '虾摸鱼',
      groupName: '业务架构分析群',
    });
    const task = makeTask([node]);

    render(<TaskProgressTab task={task} onOpenGroupSession={onOpenGroupSession} />);
    fireEvent.click(screen.getByRole('button', { name: '查看执行会话 业务架构分析群' }));

    expect(onOpenGroupSession).toHaveBeenCalledWith(node);
  });

  it('节点卡片不显示额外的查看提示文案', () => {
    const task = makeTask([makeNode()]);

    render(<TaskProgressTab task={task} />);

    expect(screen.queryByText('点击查看节点详情')).not.toBeInTheDocument();
    expect(screen.queryByText('点击查看子任务执行上下文')).not.toBeInTheDocument();
  });

  it('节点详情展示 Task Spec 的标题、指令、目标和验收标准', () => {
    const task = makeTask([makeNode()]);

    render(<TaskProgressTab task={task} />);
    fireEvent.click(screen.getByRole('button', { name: '查看节点详情 异常检测' }));

    expect(screen.getByText('标题')).toBeInTheDocument();
    expect(screen.getByText('拉取日志并识别异常连接。')).toBeInTheDocument();
    expect(screen.getByText('目标')).toBeInTheDocument();
    expect(screen.getByText('定位连接池耗尽的根因')).toBeInTheDocument();
    expect(screen.getByText('验收标准')).toBeInTheDocument();
    expect(screen.getByText('确认连接池耗尽原因')).toBeInTheDocument();
    expect(screen.getByText('输出修复建议')).toBeInTheDocument();
  });

  it('节点没有子任务时，点击节点卡片打开详情抽屉', () => {
    const task = makeTask([makeNode()]);

    render(<TaskProgressTab task={task} />);
    fireEvent.click(screen.getByRole('button', { name: '查看节点详情 异常检测' }));

    expect(screen.getByText('基本信息')).toBeInTheDocument();
    expect(screen.getAllByText('发现连接池耗尽').length).toBeGreaterThanOrEqual(2);
  });

  it('DAG 节点点击复用节点详情入口', () => {
    const task = makeTask([makeNode()]);

    render(<TaskProgressTab task={task} />);
    fireEvent.click(screen.getByRole('button', { name: 'DAG 视图' }));
    fireEvent.mouseDown(screen.getByRole('button', { name: '查看节点 异常检测' }));
    fireEvent.mouseUp(screen.getByRole('button', { name: '查看节点 异常检测' }));

    expect(screen.getByText('基本信息')).toBeInTheDocument();
  });
});
