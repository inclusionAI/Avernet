/** @jest-environment jsdom */
// TaskNodePanelV2(L2)三区:节点信息/执行流程(action_log 派生,只读)/节点产物 + 群节点吸收(Review Focus #4)。
import type { ResolvedSessionTarget } from '@/assets/TaskPanel/sessionResolution';
import type { TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import { TaskNodePanelV2, type TaskNodePanelV2Props } from '@/assets/TaskPanel/v2/TaskNodePanelV2';
import type { TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

const makeNav = (): TaskPanelNav & { calls: { select: string[]; drill: string[]; back: number[] } } => {
  const calls = { select: [] as string[], drill: [] as string[], back: [] as number[] };
  return {
    selectedNodeId: null,
    drillStack: [],
    calls,
    selectNode: (id: string, _t: ResolvedSessionTarget | null) => calls.select.push(id),
    drillInto: (id: string) => calls.drill.push(id),
    backToOverview: () => calls.back.push(1),
  } as unknown as TaskPanelNav & { calls: typeof calls };
};

const baseNode: TaskNodeView = {
  id: 'n-group',
  name: '并行调研执行',
  sequence: 2,
  status: 'running',
  present: 'rework',
  attemptCount: 2,
  needsHitl: true,
  drillable: true,
  executor: '竞品研究员Group',
  assigneeName: '竞品研究员Bot',
  runMode: 'coop_group',
  startedAt: '2026-10-20 14:30',
  endAt: null,
  sessionId: 'bcs_grp_902:round:2',
  groupId: 'g-902',
  taskSpec: { title: '并行调研执行', instruction: '对三家竞品做功能与定价对比', acceptances: ['覆盖三家'] },
  hasSubTask: true,
  subTaskId: 'sub-9',
  artifacts: [
    { id: 'a-1', name: '调研报告', type: 'report', kind: 'text', summary: '对比表', owner: 'bot-1', updatedAt: '2026-10-20T10:00:00.000Z' },
  ],
  stepTraces: [{ id: 't1', seq: 1, title: '自验收', type: 'system', timestamp: '14:40', content: 'RUNNING → DONE' }],
} as unknown as TaskNodeView;

const task = {
  id: 't1',
  name: '竞品分析',
  description: '',
  goal: '',
  objective: '',
  acceptances: [],
  status: 'EXECUTING',
  taskType: 'dynamic',
  taskTypeLabel: '动态',
  sourceLabel: '',
  ownerBotName: '规划师',
  ownerBotId: 'bot-1',
  createdAt: '',
  finishedAt: null,
  loopRound: 1,
  needsAttention: true,
  progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
  artifacts: [{ id: 'root-1', name: '根级产物', type: 'document', updatedAt: '' }],
  nodes: [baseNode],
  dagNodes: [],
  dagEdges: [],
} as unknown as TaskView;

const props = (overrides?: Partial<TaskNodePanelV2Props>): TaskNodePanelV2Props => ({
  task,
  node: baseNode,
  nav: makeNav(),
  apiBaseUrl: '',
  bcsBaseUrl: '',
  ...overrides,
});

beforeEach(() => {
  // 群会话明细 fetch 返回空 envelope:群成员兜底合成 + 空消息,Sections 正常渲染
  (global as { fetch: unknown }).fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: async () => ({ code: 0, message: 'ok', data: null, request_id: 'r' }),
    }),
  );
});

describe('TaskNodePanelV2(L2)三区', () => {
  it('节点信息区:名称/present 标签/返工徽章/HITL 徽章/goal/起止时间/步骤追踪', () => {
    render(<TaskNodePanelV2 {...props()} />);
    expect(screen.getByText('并行调研执行')).toBeTruthy();
    expect(screen.getByText('返工中')).toBeTruthy();
    expect(screen.getByText('第 2 次返工')).toBeTruthy();
    expect(screen.getByText('人工处理')).toBeTruthy();
    expect(screen.getByText('对三家竞品做功能与定价对比')).toBeTruthy();
    expect(screen.getByText(/开始 2026-10-20 14:30/)).toBeTruthy();
    expect(screen.getByText(/进行中/)).toBeTruthy();
    expect(screen.getByText('步骤追踪')).toBeTruthy();
  });

  it('执行流程区:execDag 步骤渲染为只读小图,点击不触发导航,无下钻热区', () => {
    const nav = makeNav();
    const node = {
      ...baseNode,
      execDag: {
        nodes: [
          { id: 'ev-1', label: 'accept_context' },
          { id: 'ev-2', label: 'dispatch 两分支' },
        ],
        edges: [{ from: 'ev-1', to: 'ev-2', semantics: 'none' }],
      },
    } as unknown as TaskNodeView;
    render(<TaskNodePanelV2 {...props({ node, nav })} />);

    const execView = document.querySelector('[data-testid="exec-dag-view"]');
    expect(execView).not.toBeNull();
    // 步骤标签存在(SVG 内文本)
    expect(screen.getByText('accept_context')).toBeTruthy();
    // 只读:点击执行步骤不产生任何导航回调,也不存在下钻热区
    fireEvent.click(execView!.querySelector('rect')!);
    expect(nav.calls.select).toHaveLength(0);
    expect(nav.calls.drill).toHaveLength(0);
    expect(execView!.querySelector('[aria-label="下钻节点副屏"]')).toBeNull();
  });

  it('执行流程区空数据 → 空态文案', () => {
    render(<TaskNodePanelV2 {...props({ node: { ...baseNode } as TaskNodeView })} />);
    expect(screen.getByText(/该节点暂无内部执行流程/)).toBeTruthy();
  });

  it('节点产物区:仅渲染该节点产物(总数徽章 = 1),不含任务根级产物', () => {
    render(<TaskNodePanelV2 {...props()} />);
    expect(screen.getByTitle('产物计数').textContent).toBe('1 项');
    expect(screen.getByText('调研报告')).toBeTruthy();
    expect(screen.queryByText('根级产物')).toBeNull();
  });

  it('群节点追加区:bcsBaseUrl 注入时内嵌群执行明细(群成员 SectionCard)', async () => {
    render(<TaskNodePanelV2 {...props({ bcsBaseUrl: '/__test_bcs__' })} />);
    await waitFor(() => expect(screen.getByText(/群成员/)).toBeTruthy());
  });

  it('非群节点不嵌群明细', () => {
    render(<TaskNodePanelV2 {...props({ node: { ...baseNode, drillable: false, groupId: null, sessionId: null } as TaskNodeView })} />);
    expect(screen.queryByText(/群成员/)).toBeNull();
  });

  it('子任务入口:hasSubTask 节点经 onOpenSubTask 打开既有的任务链路', () => {
    const onOpenSubTask = jest.fn();
    render(<TaskNodePanelV2 {...props({ onOpenSubTask })} />);
    fireEvent.click(screen.getByTitle('查看子任务'));
    expect(onOpenSubTask).toHaveBeenCalledWith('sub-9');
  });

  it('Review Focus #4:drillStack 顶节点已不存在(L2 数据变更)→ 空态而非崩溃;返回可用', () => {
    const nav = makeNav();
    render(<TaskNodePanelV2 {...props({ node: null, nav })} />);
    expect(screen.getByText(/节点数据缺失/)).toBeTruthy();
    fireEvent.click(screen.getByText('返回任务总览'));
    expect(nav.calls.back).toHaveLength(1);
  });
});