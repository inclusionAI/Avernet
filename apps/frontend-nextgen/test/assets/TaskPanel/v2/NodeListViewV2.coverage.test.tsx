/** @jest-environment jsdom */
// NodeListViewV2 覆盖补齐:空态/键盘选择/执行体与返工徽章/HITL 徽章/子任务入口/未知态兜底。
import type { TaskView } from '@/assets/TaskPanel/types';
import { NodeListViewV2 } from '@/assets/TaskPanel/v2/NodeListViewV2';
import type { TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

interface RecordedNav {
  nav: TaskPanelNav;
  calls: {
    root: string[];
    select: Array<[string, unknown]>;
    default: string[];
    drill: string[];
    back: number[];
  };
}

function makeNav(selectedNodeId: string | null = null): RecordedNav {
  const calls = { root: [] as string[], select: [] as Array<[string, unknown]>, default: [] as string[], drill: [] as string[], back: [] as number[] };
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
    drillInto: (id: string) => {
      calls.drill.push(id);
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
    const rootTitle = screen.getByText('第1棒 根节点');
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

  it('点击根节点走 selectRootNode,点击/键盘 Enter/空格联动普通节点', () => {
    render(<NodeListViewV2 task={task} nav={recorded.nav} />);

    // 根节点:恢复宿主会话,不走 selectNode
    fireEvent.click(screen.getByRole('button', { name: '选中节点 第1棒 根节点' }));
    expect(recorded.calls.root).toEqual(['n-root']);
    expect(recorded.calls.select).toHaveLength(0);

    // 其他节点:选中 + 会话联动(coop_group 派生会话唯一 → 群会话目标)
    fireEvent.click(screen.getByRole('button', { name: '选中节点 第2棒 群协作节点' }));
    expect(recorded.calls.select).toHaveLength(1);
    const [, groupTarget] = recorded.calls.select[0];
    expect(groupTarget).toMatchObject({ sessionId: 'bcs_grp_x', kind: 'group' });

    // 空格键同样触发选择;Enter 对根节点也走 selectRootNode
    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 第3棒 未知态节点' }), { key: ' ' });
    expect(recorded.calls.select).toHaveLength(2);
    expect(recorded.calls.select[1][0]).toBe('n-mystery');

    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 第1棒 根节点' }), { key: 'Enter' });
    expect(recorded.calls.root).toEqual(['n-root', 'n-root']);

    // 其它按键不触发选择
    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 第4棒 带子任务节点' }), { key: 'Tab' });
    expect(recorded.calls.select).toHaveLength(2);
    expect(recorded.calls.root).toHaveLength(2);
  });

  it('子任务按钮:独立点击 stopPropagation,不触发节点选择', () => {
    const onOpenSubTask = jest.fn();
    render(<NodeListViewV2 task={task} nav={recorded.nav} onOpenSubTask={onOpenSubTask} />);

    fireEvent.click(screen.getByRole('button', { name: '子任务' }));
    expect(onOpenSubTask).toHaveBeenCalledWith('sub-1');
    expect(recorded.calls.root).toHaveLength(0);
    expect(recorded.calls.select).toHaveLength(0);
  });
});
