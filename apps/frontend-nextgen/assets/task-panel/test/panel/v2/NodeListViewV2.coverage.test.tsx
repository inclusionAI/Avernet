/** @vitest-environment jsdom */
// NodeListViewV2 覆盖补齐:空态/键盘选择/执行体与返工徽章/HITL 徽章/子任务入口/未知态兜底。
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TaskView } from '../../../src/panel/types';
import { NodeListViewV2 } from '../../../src/panel/v2/NodeListViewV2';
import type { TaskPanelNav } from '../../../src/panel/v2/taskPanelNav';

interface RecordedNav {
  nav: TaskPanelNav;
  calls: {
    root: string[];
    select: Array<[string, unknown]>;
    default: string[];
    drill: Array<[string, unknown]>;
    back: number[];
  };
}

function makeNav(selectedNodeId: string | null = null): RecordedNav {
  const calls = {
    root: [] as string[],
    select: [] as Array<[string, unknown]>,
    default: [] as string[],
    drill: [] as Array<[string, unknown]>,
    back: [] as number[],
  };
  const nav = {
    selectedNodeId,
    drillStack: [] as string[],
    selectRootNode: (id: string) => {
      calls.root.push(id);
    },
    selectNode: (id: string, target: unknown) => {
      calls.select.push([id, target]);
    },
    selectDefaultNode: (id: string) => {
      calls.default.push(id);
    },
    drillInto: (id: string, target: unknown) => {
      calls.drill.push([id, target]);
    },
    backToOverview: () => {
      calls.back.push(1);
    },
  } as unknown as TaskPanelNav;
  return { nav, calls };
}

const task = {
  id: 't1',
  name: '竞品分析',
  description: '对比主流产品',
  goal: '',
  objective: '',
  acceptances: [],
  status: 'EXECUTING',
  taskType: 'dynamic',
  taskTypeLabel: '动态',
  sourceLabel: '',
  ownerBotId: 'bot-1',
  ownerBotName: '规划师',
  ownerUserId: 'user-1',
  createdAt: '',
  finishedAt: null,
  loopRound: 1,
  needsAttention: false,
  mainSessionId: 'sess-main',
  derivedSessionIds: ['bcs_grp_x'],
  artifacts: [],
  dagNodes: [],
  dagEdges: [],
  nodes: [
    {
      id: 'n-root',
      name: '根节点',
      sequence: 1,
      status: 'done',
      present: 'merged',
      hasSubTask: false,
      artifacts: [],
      stepTraces: [],
    },
    {
      id: 'n-group',
      name: '群协作节点',
      sequence: 2,
      status: 'running',
      present: 'running',
      runMode: 'coop_group',
      groupName: '调研协作群',
      attemptCount: 3,
      needsHitl: true,
      hasSubTask: false,
      artifacts: [],
      stepTraces: [],
    },
    {
      // 非法 present 值 → 走 tone 兜底,label 直接显示原始值
      id: 'n-mystery',
      name: '未知态节点',
      sequence: 3,
      status: 'failed',
      present: 'mystery',
      hasSubTask: false,
      artifacts: [],
      stepTraces: [],
    },
    {
      id: 'n-sub',
      name: '带子任务节点',
      sequence: 4,
      status: 'pending',
      present: 'pending',
      hasSubTask: true,
      subTaskId: 'sub-1',
      artifacts: [],
      stepTraces: [],
    },
  ],
} as unknown as TaskView;

describe('NodeListViewV2 覆盖补齐', () => {
  let recorded: RecordedNav;

  beforeEach(() => {
    recorded = makeNav();
  });

  it('空节点列表渲染 Empty', () => {
    render(<NodeListViewV2 task={{ ...task, nodes: [] } as TaskView} nav={recorded.nav} />);
    expect(screen.getByText('暂无任务节点')).toBeInTheDocument();
  });

  it('节点卡片:完成划线/群执行体/返工与 HITL 徽章/未知态兜底', () => {
    const recordedSelected = makeNav('n-group');
    render(<NodeListViewV2 task={task} nav={recordedSelected.nav} />);
    recorded = recordedSelected;

    // 完成节点标题划线
    const rootTitle = screen.getByRole('button', { name: '选中节点 第1棒 根节点' }).querySelector('[data-node-title]');
    expect(rootTitle).toHaveStyle({ textDecoration: 'line-through' });

    // 群节点:执行体群名 + 返工徽章 + HITL 徽章;未知态节点 tone 兜底
    expect(screen.getByText('调研协作群')).toBeInTheDocument();
    expect(screen.getByText('第 2 次返工')).toBeInTheDocument();
    expect(screen.getByText('人工处理')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'mystery' })).toBeInTheDocument();

    // 徽章在非 running + needsHitl 场景不展示(根节点 present merged)
    const rootLi = screen.getByRole('button', { name: '选中节点 第1棒 根节点' });
    expect(rootLi.textContent).not.toContain('人工处理');
  });

  it('点击和键盘操作定位节点目标，第一棒恢复宿主会话', () => {
    render(<NodeListViewV2 task={task} nav={recorded.nav} />);

    // 根节点统一恢复宿主会话，不因节点目标存在而改走联动
    fireEvent.click(screen.getByRole('button', { name: '选中节点 第1棒 根节点' }));
    expect(recorded.calls.root).toEqual(['n-root']);
    expect(recorded.calls.select).toHaveLength(0);

    // 其他节点:选中 + 会话联动(coop_group 派生会话唯一 → 群会话目标)
    fireEvent.click(screen.getByRole('button', { name: '选中节点 第2棒 群协作节点' }));
    expect(recorded.calls.select).toHaveLength(1);
    const [, groupTarget] = recorded.calls.select[0];
    expect(groupTarget).toMatchObject({ sessionId: 'bcs_grp_x', kind: 'group' });

    // 空格键同样触发选择;根节点 Enter 仍恢复宿主会话
    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 第3棒 未知态节点' }), { key: ' ' });
    expect(recorded.calls.select).toHaveLength(2);
    expect(recorded.calls.select[1][0]).toBe('n-mystery');

    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 第1棒 根节点' }), { key: 'Enter' });
    expect(recorded.calls.root).toHaveLength(2);
    expect(recorded.calls.select).toHaveLength(2);

    // 其它按键不触发选择
    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 第4棒 带子任务节点' }), { key: 'Tab' });
    expect(recorded.calls.select).toHaveLength(2);
    expect(recorded.calls.root).toHaveLength(2);
  });

  it('子任务按钮:独立点击 stopPropagation,不触发节点选择', () => {
    const onOpenSubTask = vi.fn();
    render(<NodeListViewV2 task={task} nav={recorded.nav} onOpenSubTask={onOpenSubTask} />);

    fireEvent.click(screen.getByRole('button', { name: '子任务' }));
    expect(onOpenSubTask).toHaveBeenCalledWith('sub-1');
    expect(recorded.calls.root).toHaveLength(0);
    expect(recorded.calls.select).toHaveLength(0);
  });

  it.each([
    ['Bot', { sessionId: 'sess-bot', assignee: 'bot-2', runMode: 'single_bot' }, { sessionId: 'sess-bot', kind: 'bot', botId: 'bot-2' }],
    ['group', { sessionId: 'bcs_grp_2', groupId: 'g-2', drillable: true, runMode: 'coop_group' }, { sessionId: 'bcs_grp_2', kind: 'group', groupId: 'g-2' }],
  ])('有效 %s 图标下钻节点并联动对应会话', (_label, fields, expectedTarget) => {
    const node = { ...task.nodes[1], ...fields };
    render(<NodeListViewV2 task={{ ...task, nodes: [task.nodes[0], node] }} nav={recorded.nav} />);
    const icon = screen.getByRole('button', { name: `下钻 ${node.name}` });
    expect(icon).toHaveAttribute('aria-disabled', 'false');
    fireEvent.click(icon);
    expect(recorded.calls.drill).toEqual([[node.id, expect.objectContaining(expectedTarget)]]);
    expect(recorded.calls.root).toEqual([]);
    expect(recorded.calls.select).toEqual([]);
  });

  it.each([
    ['Bot 缺 session（有主会话 fallback）', { assignee: 'bot-2', runMode: 'single_bot' }],
    ['群缺 session（派生会话不唯一）', { drillable: true, runMode: 'coop_group' }],
    ['群缺 session（派生会话唯一）', { drillable: true, runMode: 'bbs' }],
    ['session 空白', { sessionId: '  ', assignee: 'bot-2' }],
    ['未知执行者', { sessionId: 'sess-unknown', executor: '显示名称不是身份' }],
    ['Bot 身份空白', { sessionId: 'sess-bot', assignee: '  ' }],
    ['群未被 mapper 确认', { sessionId: 'bcs_grp_2', runMode: 'coop_group', groupName: '群名称' }],
  ])('%s：图标可聚焦并解释不可用，不借用任务级会话', (label, fields) => {
    const node = { ...task.nodes[1], ...fields };
    const derivedSessionIds = label.includes('不唯一') ? ['bcs_grp_a', 'bcs_grp_b'] : task.derivedSessionIds;
    render(<NodeListViewV2 task={{ ...task, derivedSessionIds, nodes: [node] }} nav={recorded.nav} />);
    const icon = screen.getByRole('button', { name: `下钻 ${node.name}：节点缺少会话或执行者信息，无法下钻` });
    expect(icon).toHaveAttribute('aria-disabled', 'true');
    expect(icon).toHaveAttribute('title', '节点缺少会话或执行者信息，无法下钻');
    icon.focus();
    expect(icon).toHaveFocus();
    fireEvent.click(icon);
    for (const key of ['Enter', ' ']) {
      // 原生 button 的键盘默认行为生成 click；keydown 不应被父卡片处理。
      fireEvent.keyDown(icon, { key });
      fireEvent.keyUp(icon, { key });
      fireEvent.click(icon, { detail: 0 });
    }
    expect(recorded.calls.drill).toEqual([]);
    expect(recorded.calls.root).toEqual([]);
    expect(recorded.calls.select).toEqual([]);
  });

  it.each(['Enter', ' '])('%s：图标和子任务按钮不冒泡选择，卡片本体仍可激活', (key) => {
    const node = { ...task.nodes[3], sessionId: 'sess-sub', assignee: 'bot-2' };
    const onOpenSubTask = vi.fn();
    render(
      <NodeListViewV2
        task={{ ...task, nodes: [task.nodes[0], node] }}
        nav={recorded.nav}
        onOpenSubTask={onOpenSubTask}
      />,
    );
    const icon = screen.getByRole('button', { name: `下钻 ${node.name}` });
    const child = screen.getByRole('button', { name: '子任务' });
    for (const control of [icon, child]) {
      fireEvent.keyDown(control, { key });
      expect(recorded.calls.select).toEqual([]);
      expect(recorded.calls.root).toEqual([]);
      fireEvent.keyUp(control, { key });
      fireEvent.click(control, { detail: 0 });
    }
    expect(recorded.calls.drill).toEqual([[node.id, expect.objectContaining({ sessionId: 'sess-sub', kind: 'bot', botId: 'bot-2' })]]);
    expect(onOpenSubTask).toHaveBeenCalledTimes(1);
    expect(onOpenSubTask).toHaveBeenCalledWith('sub-1');
    expect(recorded.calls.select).toEqual([]);
    fireEvent.keyDown(screen.getByRole('button', { name: `选中节点 第2棒 ${node.name}` }), { key });
    expect(recorded.calls.select).toEqual([[node.id, expect.objectContaining({ sessionId: 'sess-sub' })]]);
    expect(recorded.calls.drill).toHaveLength(1);
  });
});
