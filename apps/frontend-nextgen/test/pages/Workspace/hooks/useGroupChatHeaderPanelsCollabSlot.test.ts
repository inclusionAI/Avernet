/**
 * @jest-environment jsdom
 */
// collab 形态任务副屏 bootstrap 运行时回归（CF-4）：先注册全 null 槽位再静态 import
// useGroupChatHeaderPanels（taskPanelBootstrap 槽位按模块求值一次性绑定本地 null 兜底），
// 断言 Open 形态的真实 bootstrap 触发条件全部就绪（sessionId + userId + 无活跃任务）时：
// 不发起 services/myTask listMyTasks 拉取、不向副屏重放任务面板页签、不消费任务面板打开意图，
// hook 返回形状照常（非任务能力不受槽位精简影响）。
// 静态导入红线（hook 源不得直接 import useSessionTaskPanelBootstrap）由
// useGroupChatHeaderPanelsSlot.test.ts 源级断言覆盖，本套件补运行时行为面。
import '../../../helpers/registerCollabNullSlots';
// 注意：@jest/globals 必须先于 @/services/myTask 引入——jest.mock 工厂在 myTask 首次
// require 时执行，晚于本行的 jest 绑定才可解析（工厂内使用 jest.fn）。
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { SessionView } from '@/domain/collaboration';
import { useGroupChatHeaderPanels } from '@/pages/Workspace/hooks/useGroupChatHeaderPanels';
import { listMyTasks } from '@/services/myTask';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle } from '@tc-chat/core';
import { renderHook } from '@testing-library/react';

// 仅桩 useSessionTaskPanelBootstrap 已知的三个进口：若回归把真实 bootstrap 拉进该 hook 的
// 运行时闭包，listMyTasks mock 命中断言即显式失败（不得静默吞过）。
jest.mock('@/services/myTask', () => ({
  listMyTasks: jest.fn(),
  isEnvelopeFailure: jest.fn(),
  normalizeMyTaskPage: jest.fn(),
}));

const session: SessionView = {
  sessionId: 'sess-1',
  groupId: 'g1',
  title: '会话',
  kind: 'chat',
  status: 'running',
  participants: [],
  lastMessageAt: 1,
  createdAt: 1,
  favorite: false,
};

function makePanelRef() {
  const openTab = jest.fn();
  const closePanel = jest.fn();
  const handle = { openTab, closePanel, openPanel: jest.fn(), closePanelForce: jest.fn() } as unknown as PanelHandle;
  return { panelRef: { current: handle } as React.RefObject<PanelHandle | null>, openTab, closePanel };
}

beforeEach(() => {
  jest.mocked(listMyTasks).mockClear();
  useTaskSidePanelStore.getState().resetAll();
});

describe('useGroupChatHeaderPanels — collab 空槽位（任务副屏 bootstrap 缺席）', () => {
  it('bootstrap 触发条件就绪时不拉取任务列表、不重放任务面板页签，返回形状照常', () => {
    const { panelRef, openTab } = makePanelRef();
    const { result } = renderHook(() =>
      useGroupChatHeaderPanels({
        // Open 真实 bootstrap 的触发条件：sessionId + userId 就绪（activeTaskId 默认 null）。
        userId: 'u-1',
        session,
        messages: [],
        panelRef,
        toggleFavorite: jest.fn(),
      }),
    );
    expect(listMyTasks).not.toHaveBeenCalled();
    expect(openTab).not.toHaveBeenCalled();

    // 非任务能力不受槽位精简影响：收藏/历史搜索/副屏镜像开合形状照常。
    expect(result.current.favorites.disabled).toBe(false);
    expect(result.current.historySearch.results).toEqual([]);
    expect(result.current.sidePaneOpen).toBe(false);
    expect(result.current.sidePaneDisabled).toBe(true);
    expect(typeof result.current.toggleSidePane).toBe('function');
    expect(typeof result.current.onSidePaneIsOpenChange).toBe('function');
    expect(result.current.highlightMessageId).toBe(null);
    expect(typeof result.current.locateMessage).toBe('function');
  });

  it('任务面板打开意图存在时不消费意图、不拉取任务列表（collab 无任务栈消费方）', () => {
    useTaskSidePanelStore.getState().markTaskPanelOpenIntent('t-9');
    const { panelRef, openTab } = makePanelRef();
    renderHook(() =>
      useGroupChatHeaderPanels({
        userId: 'u-1',
        session,
        messages: [],
        panelRef,
        toggleFavorite: jest.fn(),
      }),
    );
    expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBe('t-9');
    expect(listMyTasks).not.toHaveBeenCalled();
    expect(openTab).not.toHaveBeenCalled();
  });
});
