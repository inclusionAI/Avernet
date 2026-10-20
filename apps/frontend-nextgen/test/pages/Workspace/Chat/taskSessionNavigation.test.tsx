/** @jest-environment jsdom */
// 会话联动观察者(P4):pendingNavigation 消费 → capability 跳会话 → 时间窗 DOM 定位降级。
// capability 的 URL 构建测在 defaultCapabilities.test.ts;此处以注入的 mock capability 断言联动协议。
import { getCapabilities } from '@/capabilities';
import { useTaskSessionNavigationObserver } from '@/pages/Workspace/Chat/hooks/taskSessionNavigation';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

const mockNavigate = jest.fn((): { status: 'available'; value: null } => ({ status: 'available', value: null }));

const Harness = () => {
  useTaskSessionNavigationObserver();
  return null;
};

describe('useTaskSessionNavigationObserver', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    useTaskSidePanelStore.getState().resetAll();
    mockNavigate.mockClear();
    mockNavigate.mockImplementation(() => ({ status: 'available', value: null }));
    document.body.innerHTML = '';
    // 测试缝合:替换 capability 注册表项(模块级单例,本文件隔离)
    Object.assign(getCapabilities() as object, { navigateToConversation: mockNavigate });
  });
  afterEach(() => {
    act(() => jest.runOnlyPendingTimers());
    jest.useRealTimers();
  });

  it('协作群目标:带 kind/groupId 透传给 capability,消费后 pending 清空', () => {
    render(<Harness />);
    act(() => {
      useTaskSidePanelStore.getState().selectNode('t1', 'n1', {
        taskId: 't1',
        sessionId: 'bcs_grp_902:round:2',
        kind: 'group',
        groupId: 'g-902',
      });
    });
    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902' }),
    );
    expect(useTaskSidePanelStore.getState().pendingNavigation).toBeNull();
  });

  it('单聊目标:botId 随行', () => {
    render(<Harness />);
    act(() => {
      useTaskSidePanelStore.getState().selectNode('t1', 'n1', {
        taskId: 't1',
        sessionId: 'agent:main:session:s-1',
        kind: 'bot',
        botId: 'bot-9',
      });
    });
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'agent:main:session:s-1', kind: 'bot', botId: 'bot-9' }),
    );
  });

  it('Review Focus #2:capability 不可用 → 不导航不抛错,pending 已消费', () => {
    const infoSpy = jest.spyOn(console, 'info').mockImplementation(() => {});
    mockNavigate.mockImplementation(() => ({ status: 'unsupported', value: null, reason: '不支持' }));
    render(<Harness />);
    act(() => {
      useTaskSidePanelStore.getState().selectNode('t1', 'n1', {
        taskId: 't1',
        sessionId: '',
        kind: 'unknown',
      });
    });
    expect(useTaskSidePanelStore.getState().pendingNavigation).toBeNull();
    expect(infoSpy).toHaveBeenCalled();
    infoSpy.mockRestore();
  });

  it('时间窗存在:延迟探测命中 [data-ts] 元素并滚动;未命中 console.info 降级', () => {
    const infoSpy = jest.spyOn(console, 'info').mockImplementation(() => {});
    const el = document.createElement('div');
    el.setAttribute('data-ts', '1700000003000');
    el.scrollIntoView = jest.fn();
    document.body.appendChild(el);

    render(<Harness />);
    act(() => {
      useTaskSidePanelStore.getState().selectNode('t1', 'n1', {
        taskId: 't1',
        sessionId: 'agent:main:session:s-1',
        kind: 'bot',
        startTime: 1700000000000,
        endTime: 1700000005000,
      });
    });
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    expect(el.scrollIntoView).toHaveBeenCalled();

    // 降级路径:清空 DOM 后再触发一次,未命中仅 console.info,不抛错
    document.body.innerHTML = '';
    const before = infoSpy.mock.calls.length;
    act(() => {
      useTaskSidePanelStore.getState().selectNode('t2', 'n2', {
        taskId: 't2',
        sessionId: 'agent:main:session:s-2',
        kind: 'bot',
        startTime: 1,
        endTime: 2,
      });
    });
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    expect(infoSpy.mock.calls.length).toBeGreaterThan(before);
    infoSpy.mockRestore();
  });

  it('无时间窗:不注册 DOM 探测', () => {
    render(<Harness />);
    act(() => {
      useTaskSidePanelStore.getState().selectNode('t1', 'n1', {
        taskId: 't1',
        sessionId: 'agent:main:session:s-1',
        kind: 'bot',
      });
    });
    expect(jest.getTimerCount()).toBe(0);
  });
});