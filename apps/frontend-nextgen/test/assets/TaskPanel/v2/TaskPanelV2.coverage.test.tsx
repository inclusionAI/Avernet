/** @jest-environment jsdom */
// TaskPanelV2 覆盖补齐:默认选中 effect 的早退分支(selectedNodeId 已注入时)+ 收起按钮 hover 样式。
import { C } from '@/assets/TaskPanel/tokens';
import type { TaskView } from '@/assets/TaskPanel/types';
import { TaskPanelV2, type TaskPanelV2Props } from '@/assets/TaskPanel/v2/TaskPanelV2';
import type { TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
// jest.mock 工厂在 import 绑定初始化前执行,工厂内使用注入的全局 jest。
import { describe, expect, it } from '@jest/globals';

jest.mock('@/assets/TaskPanel/GroupDrillDown', () => {
  const actual = jest.requireActual('@/assets/TaskPanel/GroupDrillDown');
  return {
    ...actual,
    fetchGroupDetail: jest.fn(),
  };
});

const baseTask = {
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
  ownerUserId: 'user-1',
  createdAt: '',
  finishedAt: null,
  loopRound: 1,
  needsAttention: false,
  progress: { total: 2, pending: 0, planning: 0, running: 1, done: 1, failed: 0, hung: 0, skipped: 0, percent: 50 },
  artifacts: [],
  derivedSessionIds: [],
  nodes: [
    { id: 'n-solo', name: '规划与澄清', sequence: 1, status: 'done', present: 'merged', hasSubTask: false, artifacts: [], stepTraces: [] },
  ],
  dagNodes: [{ id: 'n-solo', label: '规划与澄清', status: 'done', x: 0, y: 0, isCurrent: false, short: '规划与澄清' }],
  dagEdges: [],
} as unknown as TaskView;

function makeNav(selectedNodeId: string | null = null) {
  const calls = { default: [] as string[], back: [] as number[] };
  const nav = {
    selectedNodeId,
    drillStack: [] as string[],
    selectRootNode: jest.fn(),
    selectNode: jest.fn(),
    selectDefaultNode: (id: string) => {
      calls.default.push(id);
    },
    drillInto: jest.fn(),
    backToOverview: () => {
      calls.back.push(1);
    },
  } as unknown as TaskPanelNav & { calls: typeof calls };
  return { nav, calls };
}

/** hex 色值 → jsdom style 归一化后的 rgb() 形式(断言 hover 底色用)。 */
function toRgb(hex: string): string {
  const value = hex.replace('#', '');
  const numeric = Number.parseInt(value, 16);
  return `rgb(${(numeric >> 16) & 255}, ${(numeric >> 8) & 255}, ${numeric & 255})`;
}

const baseProps = (overrides?: Partial<TaskPanelV2Props>): TaskPanelV2Props => ({
  task: baseTask,
  nav: makeNav().nav,
  apiBaseUrl: '',
  ...overrides,
});

describe('TaskPanelV2 覆盖补齐', () => {
  it('nav 已注入 selectedNodeId 时默认选中 effect 直接返回', () => {
    const { nav, calls } = makeNav('n-external');
    render(<TaskPanelV2 {...baseProps({ nav })} />);

    // 不会向宿主写默认选中,面板照常渲染
    expect(calls.default).toEqual([]);
    expect(screen.getByTitle('任务总览：竞品分析')).toBeInTheDocument();
  });

  it('收起按钮 hover:mouseenter 覆底色,mouseleave 还原透明', () => {
    const onTogglePanel = jest.fn();
    render(<TaskPanelV2 {...baseProps({ onTogglePanel })} />);

    const toggle = screen.getByRole('button', { name: '收起任务副屏' });
    expect(toggle.style.background).toBe('');

    // jsdom 的 cssstyle 会把 hex 归一化成 rgb(...)
    const expectedSurfaceAlt = toRgb(C.surfaceAlt);
    fireEvent.mouseEnter(toggle);
    expect(toggle.style.background).toBe(expectedSurfaceAlt);

    fireEvent.mouseLeave(toggle);
    expect(toggle.style.background).toBe('transparent');

    fireEvent.click(toggle);
    expect(onTogglePanel).toHaveBeenCalledTimes(1);
  });
});
