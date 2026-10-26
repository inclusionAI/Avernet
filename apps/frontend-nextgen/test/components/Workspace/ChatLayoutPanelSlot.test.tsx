/**
 * @jest-environment jsdom
 */
import { ChatLayoutPanelSlot } from '@/components/Workspace/ChatLayoutPanelSlot';
import { PersistentChatLayoutPanelProvider } from '@/components/Workspace/PersistentChatLayoutPanelHost';
import { PersistentChatLayoutPanelViewport } from '@/components/Workspace/PersistentChatLayoutPanelViewport';
import { PANEL_WIDTH_STORAGE_KEY } from '@/services/workspace/panelWidthMemory';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

// 捕获 <ChatLayoutPanelSlot> 内部渲染 <ChatLayout.Panel> 收到的 props（push-stack 防类型收窄）。
// 真实运行时 ChatLayoutRoot 会 cloneElement 注入 onIsOpenChange（Root 的 ResizeHandle
// setter）——用例模拟该注入行为，验证组合分发语义。
let mockPanelRenders: Array<Record<string, unknown>> = [];
let mockPanelMountCount = 0;
let mockPanelUnmountCount = 0;
const mockRawHandle = {
  openTab: jest.fn(),
  closeTab: jest.fn(),
  updateTab: jest.fn(),
  focusTab: jest.fn(),
  openPanel: jest.fn(),
  closePanel: jest.fn(),
  closePanelForce: jest.fn(),
  flushContext: jest.fn(() => ({ interactions: [], panelState: { tabs: [], activeTabId: null } })),
  flushInteractions: jest.fn(() => []),
};

/**
 * 模拟 Root cloneElement 注入：onIsOpenChange 不在 ChatLayoutPanelSlot 公开类型里
 * （运行时由 ChatLayoutRoot 注入），测试里以类型逃逸挂载同语义 props；
 * 对 spread 不做 excess property 检查，故以组件 props 整体断言即可。
 */
const asRootInjected = (props: Record<string, unknown>) =>
  props as unknown as React.ComponentProps<typeof ChatLayoutPanelSlot>;

function PersistentPanelHarness({ children }: { children?: React.ReactNode }): JSX.Element {
  return (
    <PersistentChatLayoutPanelProvider>
      <div data-testid="mock-route-content">{children}</div>
      <PersistentChatLayoutPanelViewport />
    </PersistentChatLayoutPanelProvider>
  );
}

jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const { createElement, forwardRef, useEffect, useImperativeHandle, useState } = require('react');
  const Panel = forwardRef((props: Record<string, unknown>, ref: React.ForwardedRef<unknown>) => {
    const [localState, setLocalState] = useState(0);
    useImperativeHandle(ref, () => mockRawHandle);
    useEffect(() => {
      mockPanelMountCount += 1;
      return () => {
        mockPanelUnmountCount += 1;
      };
    }, []);
    useEffect(() => {
      const bridge = props.bridge as
        | {
            registerHandler?: (name: string, handler: () => void) => void;
            unregisterHandler?: (name: string) => void;
          }
        | undefined;
      const handler = () => undefined;
      bridge?.registerHandler?.('openPanel', handler);
      return () => bridge?.unregisterHandler?.('openPanel');
    }, [props.bridge]);
    mockPanelRenders.push(props);
    return createElement(
      'button',
      {
        'data-testid': 'mock-persistent-panel-state',
        onClick: () => setLocalState((value: number) => value + 1),
        type: 'button',
      },
      String(localState),
    );
  });
  const ChatLayout = () => null;
  ChatLayout.Panel = Panel;
  return { ChatLayout };
});

describe('ChatLayoutPanelSlot', () => {
  beforeEach(() => {
    mockPanelRenders = [];
    mockPanelMountCount = 0;
    mockPanelUnmountCount = 0;
    jest.clearAllMocks();
    localStorage.clear();
    useTaskConversationNavigationStore.getState().resetAll();
    window.history.pushState({}, '', '/workspace/chat?bot=b1&session=s1');
  });

  it('displayName 为 "ChatLayout.Panel"，使 ChatLayoutRoot 识别插槽并 cloneElement 注入真实开合态', () => {
    // Root 以 child.type.displayName === 'ChatLayout.Panel' 扫描 child 识别副屏插槽。
    expect(ChatLayoutPanelSlot.displayName).toBe('ChatLayout.Panel');
  });

  it('SDK 尚未测得 containerWidth 时直接以 TaskPanel 的 420px 初始宽回退', () => {
    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', containerWidth: null })} />);
    expect(mockPanelRenders.at(-1)?.panelWidth).toBe(420);
  });

  it('调用方显式提供 panelWidth 时仍优先使用调用方宽度', () => {
    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', panelWidth: 500 })} />);
    expect(mockPanelRenders.at(-1)?.panelWidth).toBe(500);
  });

  it('组合分发 onIsOpenChange：Root 注入的 setter 原样透传，App 侧 onOpenStateChange 串联其后', () => {
    // Root 经 cloneElement 注入的 setIsPanelOpen（ResizeHandle 开合镜像）。
    const rootIsOpenChange = jest.fn();
    // App 侧真实开合态观察口。
    const onOpenStateChange = jest.fn();
    const { rerender } = render(
      <ChatLayoutPanelSlot
        {...asRootInjected({ bridge: 'bridge', onIsOpenChange: rootIsOpenChange, onOpenStateChange })}
      />,
    );

    // 内部 <ChatLayout.Panel> 收到组合后的 onIsOpenChange（openTab 自动展开 /
    // closePanel / closePanelForce / autoCloseWhenEmpty 弹回全部写路径均经此回调）。
    const composed = mockPanelRenders.at(-1)?.onIsOpenChange as ((open: boolean) => void) | undefined;
    expect(typeof composed).toBe('function');
    act(() => composed?.(true));
    expect(rootIsOpenChange).toHaveBeenCalledWith(true);
    expect(onOpenStateChange).toHaveBeenCalledWith(true);
    act(() => composed?.(false));
    expect(rootIsOpenChange).toHaveBeenCalledWith(false);
    expect(onOpenStateChange).toHaveBeenCalledWith(false);
    expect(onOpenStateChange).toHaveBeenCalledTimes(2);

    // 业务 props（bridge 等）透传不丢。
    expect(mockPanelRenders.at(-1)?.bridge).toBe('bridge');

    // onOpenStateChange 更换引用：重渲染后组合函数跟随新回调（闭包不过期）。
    const next = jest.fn();
    rerender(
      <ChatLayoutPanelSlot {...asRootInjected({ onIsOpenChange: rootIsOpenChange, onOpenStateChange: next })} />,
    );
    act(() => (mockPanelRenders.at(-1)?.onIsOpenChange as (open: boolean) => void)?.(true));
    expect(next).toHaveBeenCalledWith(true);
  });

  it('未注入 Root setter 时（面板独立渲染等）仅通知 App 侧观察口', () => {
    const onOpenStateChange = jest.fn();
    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', onOpenStateChange })} />);
    act(() => (mockPanelRenders.at(-1)?.onIsOpenChange as (open: boolean) => void)?.(true));
    expect(onOpenStateChange).toHaveBeenCalledWith(true);
  });

  it('slot 私有 props 剥离：dismissOnOutside 不透传给 SDK Panel（缺省 true 行为由真实 SDK 用例看守）', () => {
    mockPanelRenders = [];
    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', dismissOnOutside: false })} />);
    // strip-before-forward：SDK Panel 不感知收起策略，只收到业务 props。
    expect(mockPanelRenders.at(-1)?.dismissOnOutside).toBeUndefined();
    expect(mockPanelRenders.at(-1)?.bridge).toBe('bridge');
  });

  it('目标会话 hydration 前不消费保活信号，hydration 后重建 TaskPanel tab', () => {
    const tab = {
      id: 'task-t1',
      title: 'Task',
      type: 'taskPanel.TaskLoopView',
      params: { taskId: 't1' },
    };
    window.history.pushState({}, '', '/workspace/chat?bot=b2&session=s2');
    const store = useTaskConversationNavigationStore.getState();
    store.rememberTaskTab('t1', tab);
    const navigationId = store.beginNavigation('t1', '/workspace/chat', 's2');
    store.markRouteCommitted(navigationId);

    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge' })} />);

    expect(mockRawHandle.openTab).not.toHaveBeenCalled();
    expect(useTaskConversationNavigationStore.getState().navigation?.id).toBe(navigationId);

    act(() => useTaskConversationNavigationStore.getState().markRouteHydrated(navigationId));

    expect(mockRawHandle.openTab).toHaveBeenCalledWith(tab);
    expect(useTaskConversationNavigationStore.getState().navigation?.id).toBe(navigationId);
  });

  it('目标会话迟到的 closePanelForce 仍被持续抑制，TaskPanel 不会一闪而逝', () => {
    jest.useFakeTimers();
    try {
      const ref = React.createRef<import('@tc-chat/core').PanelHandle>();
      window.history.pushState({}, '', '/workspace/chat?bot=b2&session=s2');
      const store = useTaskConversationNavigationStore.getState();
      store.rememberTaskTab('t1', {
        id: 'task-t1',
        title: 'Task',
        type: 'taskPanel.TaskLoopView',
        params: { taskId: 't1' },
      });
      const navigationId = store.beginNavigation('t1', '/workspace/chat', 's2');
      store.markRouteCommitted(navigationId);
      store.markRouteHydrated(navigationId);
      render(<ChatLayoutPanelSlot ref={ref} {...asRootInjected({ bridge: 'bridge' })} />);

      act(() => ref.current?.closePanelForce());

      expect(mockRawHandle.closePanelForce).not.toHaveBeenCalled();
      expect(useTaskConversationNavigationStore.getState().navigation?.id).toBe(navigationId);
      act(() => jest.advanceTimersByTime(5_000));
      act(() => ref.current?.closePanelForce());
      expect(mockRawHandle.closePanelForce).not.toHaveBeenCalled();
      expect(useTaskConversationNavigationStore.getState().navigation?.id).toBe(navigationId);
    } finally {
      jest.useRealTimers();
    }
  });

  it('用户显式收起 TaskPanel 时释放保活信号，后续不会自动重开', () => {
    const ref = React.createRef<import('@tc-chat/core').PanelHandle>();
    window.history.pushState({}, '', '/workspace/chat?bot=b2&session=s2');
    const store = useTaskConversationNavigationStore.getState();
    store.rememberTaskTab('t1', {
      id: 'task-t1',
      title: 'Task',
      type: 'taskPanel.TaskLoopView',
      params: { taskId: 't1' },
    });
    const navigationId = store.beginNavigation('t1', '/workspace/chat', 's2');
    store.markRouteCommitted(navigationId);
    store.markRouteHydrated(navigationId);
    render(<ChatLayoutPanelSlot ref={ref} {...asRootInjected({ bridge: 'bridge' })} />);

    act(() => ref.current?.closePanel());

    expect(mockRawHandle.closePanel).toHaveBeenCalledTimes(1);
    expect(useTaskConversationNavigationStore.getState().navigation).toBeNull();
  });

  it('普通会话切换仍将 closePanelForce 透传给 SDK Panel，并清理旧任务宿主', () => {
    const ref = React.createRef<import('@tc-chat/core').PanelHandle>();
    const store = useTaskConversationNavigationStore.getState();
    store.rememberOrigin('t1', {
      routeUrl: '/workspace/chat?bot=b1&session=s1',
      sessionId: 's1',
      kind: 'bot',
      botId: 'b1',
    });
    store.rememberTaskTab('t1', {
      id: 'task-t1',
      title: 'Task',
      type: 'taskPanel.TaskLoopView',
      params: { taskId: 't1' },
    });
    const navigationId = store.beginNavigation('t1', '/workspace/chat', 's1');
    store.markRouteCommitted(navigationId);
    store.markRouteHydrated(navigationId);
    render(<ChatLayoutPanelSlot ref={ref} {...asRootInjected({ bridge: 'bridge' })} />);
    window.history.pushState({}, '', '/workspace/chat?bot=b2&session=s2');

    act(() => ref.current?.closePanelForce());

    expect(mockRawHandle.closePanelForce).toHaveBeenCalledTimes(1);
    expect(useTaskConversationNavigationStore.getState().originsByTask).toEqual({});
  });

  it('任务节点跨 Bot/群会话路由时复用同一个 Panel/TaskPanel 实例，不重新挂载或恢复快照', () => {
    const sourceBridge = {
      registerHandler: jest.fn(),
      unregisterHandler: jest.fn(),
    };
    const destinationBridge = {
      registerHandler: jest.fn(),
      unregisterHandler: jest.fn(),
    };
    const tab = {
      id: 'task-t1',
      title: 'Task',
      type: 'taskPanel.TaskLoopView',
      params: { taskId: 't1' },
    };
    const { rerender } = render(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot key="source" {...asRootInjected({ bridge: sourceBridge })} />
      </PersistentPanelHarness>,
    );

    expect(mockPanelMountCount).toBe(1);
    expect(mockPanelUnmountCount).toBe(0);
    expect(sourceBridge.registerHandler).toHaveBeenCalledWith('openPanel', expect.any(Function));
    const originalPanel = screen.getByTestId('mock-persistent-panel-state');
    const stableViewport = originalPanel.parentElement;
    fireEvent.click(originalPanel);
    expect(originalPanel).toHaveTextContent('1');
    const rendersBeforeNavigation = mockPanelRenders.length;

    let navigationId = 0;
    act(() => {
      const store = useTaskConversationNavigationStore.getState();
      store.rememberTaskTab('t1', tab);
      navigationId = store.beginNavigation('t1', '/workspace/collaboration', 's2');
      store.markRouteCommitted(navigationId);
      store.markRouteHydrated(navigationId);
      window.history.pushState({}, '', '/workspace/collaboration?group=g2&session=s2');
    });

    // 模拟旧路由已经卸载、目标路由插槽尚未挂载的过渡帧：Panel 必须仍在 document 内，
    // 且父 DOM 不发生变化，才能保证真实页面切链时右侧副屏没有闪烁/跳动。
    rerender(<PersistentPanelHarness />);
    expect(document.contains(originalPanel)).toBe(true);
    expect(originalPanel.parentElement).toBe(stableViewport);
    expect(mockPanelMountCount).toBe(1);
    expect(mockPanelUnmountCount).toBe(0);
    expect(mockPanelRenders).toHaveLength(rendersBeforeNavigation);

    rerender(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot key="destination" {...asRootInjected({ bridge: destinationBridge })} />
      </PersistentPanelHarness>,
    );

    const movedPanel = screen.getByTestId('mock-persistent-panel-state');
    expect(movedPanel).toBe(originalPanel);
    expect(movedPanel).toHaveTextContent('1');
    expect(mockPanelMountCount).toBe(1);
    expect(mockPanelUnmountCount).toBe(0);
    expect(mockPanelRenders).toHaveLength(rendersBeforeNavigation);
    expect(sourceBridge.unregisterHandler).toHaveBeenCalledWith('openPanel');
    expect(destinationBridge.registerHandler).toHaveBeenCalledWith('openPanel', expect.any(Function));
    expect(mockRawHandle.openTab).not.toHaveBeenCalled();
  });

  it('路由 handoff 的旧页清理发生在目标 URL/hydration 之前时，仍原地保留无快照的 TaskPanel', () => {
    const sourceRef = React.createRef<import('@tc-chat/core').PanelHandle>();
    const destinationRef = React.createRef<import('@tc-chat/core').PanelHandle>();
    const { rerender } = render(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot ref={sourceRef} key="source" {...asRootInjected({ bridge: 'source-bridge' })} />
      </PersistentPanelHarness>,
    );
    const originalPanel = screen.getByTestId('mock-persistent-panel-state');
    const stableViewport = originalPanel.parentElement;
    fireEvent.click(originalPanel);
    const rendersBeforeNavigation = mockPanelRenders.length;

    let navigationId = 0;
    act(() => {
      const store = useTaskConversationNavigationStore.getState();
      navigationId = store.beginNavigation('t1', '/workspace/collaboration', 's2');
      store.markRouteCommitted(navigationId);
    });

    // history.push 内部可能先触发来源会话 effect；此时 URL 甚至还没变，保活不能依赖目标 URL/tab 快照。
    act(() => sourceRef.current?.closePanelForce());
    expect(mockRawHandle.closePanelForce).not.toHaveBeenCalled();
    expect(useTaskConversationNavigationStore.getState().navigation?.tab).toBeNull();

    // 来源插槽先卸载、目标插槽尚未挂载：真实 Panel 仍留在 AppShell viewport 中。
    rerender(<PersistentPanelHarness />);
    expect(document.contains(originalPanel)).toBe(true);
    expect(originalPanel.parentElement).toBe(stableViewport);
    expect(originalPanel).toHaveTextContent('1');
    expect(mockPanelMountCount).toBe(1);
    expect(mockPanelUnmountCount).toBe(0);
    expect(mockPanelRenders).toHaveLength(rendersBeforeNavigation);

    act(() => {
      window.history.pushState({}, '', '/workspace/collaboration?group=g2&session=s2');
    });
    rerender(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot
          ref={destinationRef}
          key="destination"
          {...asRootInjected({ bridge: 'destination-bridge' })}
        />
      </PersistentPanelHarness>,
    );
    act(() => useTaskConversationNavigationStore.getState().markRouteHydrated(navigationId));

    expect(screen.getByTestId('mock-persistent-panel-state')).toBe(originalPanel);
    expect(originalPanel.parentElement).toBe(stableViewport);
    expect(originalPanel).toHaveTextContent('1');
    expect(mockPanelMountCount).toBe(1);
    expect(mockPanelUnmountCount).toBe(0);
    expect(mockPanelRenders).toHaveLength(rendersBeforeNavigation);

    // hydration 后的普通会话跳转不属于本次 handoff，强制关闭恢复原语义。
    act(() => {
      window.history.pushState({}, '', '/workspace/chat?bot=b3&session=s3');
      destinationRef.current?.closePanelForce();
    });
    expect(mockRawHandle.closePanelForce).toHaveBeenCalledTimes(1);
    expect(useTaskConversationNavigationStore.getState().navigation).toBeNull();
  });

  it('目标插槽先挂载、来源插槽后清理时也只转移 DOM，不因提交顺序竞争而重挂载', () => {
    const { rerender } = render(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot key="source" {...asRootInjected({ bridge: 'source-bridge' })} />
      </PersistentPanelHarness>,
    );
    const originalPanel = screen.getByTestId('mock-persistent-panel-state');
    fireEvent.click(originalPanel);

    act(() => {
      const store = useTaskConversationNavigationStore.getState();
      store.rememberTaskTab('t1', {
        id: 'task-t1',
        title: 'Task',
        type: 'taskPanel.TaskLoopView',
        params: { taskId: 't1' },
      });
      const navigationId = store.beginNavigation('t1', '/workspace/collaboration', 's2');
      store.markRouteCommitted(navigationId);
      window.history.pushState({}, '', '/workspace/collaboration?group=g2&session=s2');
    });

    // 模拟并发/过渡提交：目标插槽已经挂载，但旧路由插槽尚未执行 cleanup。
    rerender(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot key="source" {...asRootInjected({ bridge: 'source-bridge' })} />
        <ChatLayoutPanelSlot key="destination" {...asRootInjected({ bridge: 'destination-bridge' })} />
      </PersistentPanelHarness>,
    );
    rerender(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot key="destination" {...asRootInjected({ bridge: 'destination-bridge' })} />
      </PersistentPanelHarness>,
    );

    const movedPanel = screen.getByTestId('mock-persistent-panel-state');
    expect(movedPanel).toBe(originalPanel);
    expect(movedPanel).toHaveTextContent('1');
    expect(mockPanelMountCount).toBe(1);
    expect(mockPanelUnmountCount).toBe(0);
  });

  it('普通会话路由切换不保活旧 Panel，仍按原语义卸载并创建新实例', () => {
    const { rerender } = render(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot key="source" {...asRootInjected({ bridge: 'source-bridge' })} />
      </PersistentPanelHarness>,
    );
    const originalPanel = screen.getByTestId('mock-persistent-panel-state');
    fireEvent.click(originalPanel);
    expect(originalPanel).toHaveTextContent('1');

    window.history.pushState({}, '', '/workspace/chat?bot=b2&session=s2');
    rerender(
      <PersistentPanelHarness>
        <ChatLayoutPanelSlot key="destination" {...asRootInjected({ bridge: 'destination-bridge' })} />
      </PersistentPanelHarness>,
    );

    const replacementPanel = screen.getByTestId('mock-persistent-panel-state');
    expect(replacementPanel).not.toBe(originalPanel);
    expect(replacementPanel).toHaveTextContent('0');
    expect(mockPanelMountCount).toBe(2);
    expect(mockPanelUnmountCount).toBe(1);
  });

  it('Root 注入拖拽结果（dynamicWidth% + containerWidth）时换算 px 落盘（拖宽记忆）', () => {
    localStorage.clear();
    render(
      <ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', dynamicWidth: '45%', containerWidth: 1000 })} />,
    );
    expect(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY)).toBe('450');
  });

  it('拖拽更新时持续跟踪最新宽度（连续拖动记忆的是落点不是起点）', () => {
    localStorage.clear();
    const { rerender } = render(
      <ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', dynamicWidth: '45%', containerWidth: 1000 })} />,
    );
    rerender(
      <ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', dynamicWidth: '56%', containerWidth: 1000 })} />,
    );
    expect(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY)).toBe('560');
  });
});
