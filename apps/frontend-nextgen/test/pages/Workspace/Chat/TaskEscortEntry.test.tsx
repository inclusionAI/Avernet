/** @jest-environment jsdom */
import { ChatHeaderActions, type ChatHeaderActionsProps } from '@/pages/Workspace/Chat/components/ChatHeaderActions';
import { ConversationHeaderPanels } from '@/pages/Workspace/Chat/components/ConversationHeaderPanels';
import {
  useChatHeaderPanels,
  type UseChatHeaderPanelsOptions,
} from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';
import { TaskEscortDemoPage } from '@/pages/Demo/TaskEscort';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import type { PanelHandle } from '@tc-chat/core';
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { useState } from 'react';
import { routes } from '../../../../config/routes';

jest.mock('@/domain/conversation/featureFlags', () => ({ HISTORY_SEARCH_ENABLED: true }));

jest.mock('@/services/tasks/taskConversationNavigation', () => {
  const { jest: mockJest } = require('@jest/globals') as typeof import('@jest/globals');
  return {
    markTaskNavigationRouteHydrated: mockJest.fn(),
    shouldPreserveTaskPanelForSession: () => false,
  };
});
// 隔离抽屉内部，验证入口、装配与挂载寿命；真实 DEMO 交互由独立组件套件验证。
jest.mock('@/components/Workspace/TaskEscortDemo', () => {
  const React = require('react') as typeof import('react');
  return {
    TaskEscortDemoPanel: ({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) => {
      const [draft, setDraft] = React.useState('初始演示草稿');
      return open ? (
        <section id="task-escort-demo-panel" aria-label="护航演示面板">
          <input aria-label="演示草稿" value={draft} onChange={(event) => setDraft(event.target.value)} />
          <button type="button" onClick={() => onOpenChange(false)}>关闭护航示例</button>
        </section>
      ) : null;
    },
  };
});

// 实时聊天入口只检查上下文透传；服务加载与可访问性由 TaskEscortPanel 套件验证。
const mockLivePanel = jest.fn<(props: { context: TaskComposerContext | null }) => void>();
jest.mock('@/components/Workspace/TaskEscortPanel', () => ({
  TaskEscortPanel: (props: { open: boolean; context: TaskComposerContext | null; onOpenChange: (open: boolean) => void }) => {
    mockLivePanel(props);
    return props.open ? (
      <section id="task-escort-demo-panel" aria-label="实时任务护航面板">
        <button type="button" onClick={() => props.onOpenChange(false)}>关闭实时护航</button>
      </section>
    ) : null;
  },
}));
const taskComposerContext: TaskComposerContext = {
  sourceType: 'bot', ownerUserId: 'viewer-101', ownerBotId: 'collaborator-bot:asset-owner-202',
  mainSessionId: 'task-session', mainSessionName: '任务会话', parentTaskId: null,
};

function makeSession(overrides: Partial<BotChatSessionView> = {}): BotChatSessionView {
  return {
    sessionId: 'demo-session-a', botId: 'demo-bot-a', title: '示例会话',
    messageCount: 0, gmtModified: '', gmtCreate: '', favorite: false, ...overrides,
  };
}

function makeOptions() {
  const sdkClose = jest.fn<() => void>();
  const sdkForceClose = jest.fn<() => void>();
  const sdkOpen = jest.fn<() => void>();
  const options: UseChatHeaderPanelsOptions = {
    session: makeSession(), messages: [], userId: null,
    panelRef: { current: { closePanel: sdkClose, closePanelForce: sdkForceClose, openPanel: sdkOpen } as unknown as PanelHandle },
    section: 'managed', botId: 'demo-bot-a',
    toggleFavorite: jest.fn<UseChatHeaderPanelsOptions['toggleFavorite']>().mockResolvedValue(true),
    favoritePending: () => false,
    openFileDrawer: jest.fn(), closeFileDrawer: jest.fn(), fileDrawerOpen: false,
  };
  return { options, sdkClose, sdkForceClose, sdkOpen };
}

beforeEach(() => {
  jest.clearAllMocks();
  (jest.requireMock('@/domain/conversation/featureFlags') as { HISTORY_SEARCH_ENABLED: boolean }).HISTORY_SEARCH_ENABLED = true;
});

describe('任务护航聊天入口', () => {
  it('在资源管理右侧、副屏左侧插入入口，转发回调并表达 pressed / expanded 状态', () => {
    const onTogglePanel = jest.fn<ChatHeaderActionsProps['onTogglePanel']>();
    const props: ChatHeaderActionsProps = {
      favorites: { favorite: false, disabled: false, onToggle: jest.fn() },
      openPanel: null, onTogglePanel, fileDrawerOpen: false,
      onToggleFileDrawer: jest.fn(), sidePaneOpen: false, onToggleSidePane: jest.fn(),
    };
    const { rerender } = render(<ChatHeaderActions {...props} />);
    expect(screen.getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      '收藏当前会话', '会话管理', '历史消息', '资源管理', '任务护航', '副屏',
    ]);
    const trigger = screen.getByRole('button', { name: '任务护航' });
    expect(trigger).toHaveAttribute('data-task-escort-trigger');
    expect(trigger).toHaveAttribute('aria-controls', 'task-escort-demo-panel');
    expect(trigger).toHaveAttribute('aria-pressed', 'false');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);
    expect(onTogglePanel).toHaveBeenLastCalledWith('escort');
    rerender(<ChatHeaderActions {...props} openPanel="escort" />);
    expect(trigger).toHaveAttribute('aria-pressed', 'true');
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(trigger);
    expect(onTogglePanel).toHaveBeenCalledTimes(2);
    expect(onTogglePanel).toHaveBeenLastCalledWith('escort');
  });

  it('保留 dev 展示 flags，隐藏历史、资源和副屏时不恢复被禁用的入口', () => {
    (jest.requireMock('@/domain/conversation/featureFlags') as { HISTORY_SEARCH_ENABLED: boolean }).HISTORY_SEARCH_ENABLED = false;
    render(<ChatHeaderActions
      favorites={{ favorite: false, disabled: false, onToggle: jest.fn() }}
      openPanel={null} onTogglePanel={jest.fn()} fileDrawerOpen={false}
      onToggleFileDrawer={jest.fn()} sidePaneOpen={false} onToggleSidePane={jest.fn()}
      showResourceManager={false} showSidePane={false}
    />);
    expect(screen.getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      '收藏当前会话', '会话管理', '任务护航',
    ]);
    expect(screen.queryByRole('button', { name: '历史消息' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '资源管理' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '副屏' })).not.toBeInTheDocument();
  });

  it('独立公开 demo 路由位于 AppLayout 外，不覆盖真实任务护航入口', () => {
    expect(routes).toContainEqual({ path: '/demo/task-escort', component: '@/pages/Demo/TaskEscort' });
    const appRoutes = routes.flatMap((route) => 'routes' in route ? route.routes ?? [] : []);
    expect(appRoutes).not.toContainEqual(expect.objectContaining({ path: '/demo/task-escort' }));
    expect(appRoutes).toContainEqual({ path: '/evolution-factory/task-escort', component: '@/pages/TaskEscort' });
  });

  it('hook 在 escort / detail / history 之间互斥，再次点击 escort 即关闭', () => {
    const { options } = makeOptions();
    const { result } = renderHook(() => useChatHeaderPanels(options));
    act(() => result.current.togglePanel('escort'));
    expect(result.current.openPanel).toBe('escort');
    act(() => result.current.togglePanel('detail'));
    expect(result.current.openPanel).toBe('detail');
    act(() => result.current.togglePanel('history'));
    expect(result.current.openPanel).toBe('history');
    act(() => result.current.togglePanel('escort'));
    act(() => result.current.togglePanel('escort'));
    expect(result.current.openPanel).toBeNull();
  });

  it('复用资源管理的 closePanel 接线，资源与护航双向互斥', () => {
    const { options } = makeOptions();
    const { result } = renderHook(() => {
      const [fileDrawerOpen, setFileDrawerOpen] = useState(false);
      const panels = useChatHeaderPanels({
        ...options, fileDrawerOpen,
        openFileDrawer: () => setFileDrawerOpen(true),
        closeFileDrawer: () => setFileDrawerOpen(false),
      });
      return {
        panels, fileDrawerOpen,
        toggleFileDrawer: () => {
          if (fileDrawerOpen) setFileDrawerOpen(false);
          else { panels.closePanel(); setFileDrawerOpen(true); }
        },
      };
    });
    act(() => result.current.toggleFileDrawer());
    expect(result.current.fileDrawerOpen).toBe(true);
    act(() => result.current.panels.togglePanel('escort'));
    expect(result.current.fileDrawerOpen).toBe(false);
    expect(result.current.panels.openPanel).toBe('escort');
    act(() => result.current.toggleFileDrawer());
    expect(result.current.panels.openPanel).toBeNull();
    expect(result.current.fileDrawerOpen).toBe(true);
    act(() => result.current.toggleFileDrawer());
    expect(result.current.fileDrawerOpen).toBe(false);
  });

  it('Bot 或会话切换均复位 escort，不把护航开合态带到另一会话', () => {
    const { options } = makeOptions();
    const { result, rerender } = renderHook(
      ({ session, botId }) => useChatHeaderPanels({ ...options, session, botId }),
      { initialProps: { session: makeSession(), botId: 'demo-bot-a' } },
    );
    act(() => result.current.togglePanel('escort'));
    rerender({ session: makeSession({ sessionId: 'demo-session-b' }), botId: 'demo-bot-a' });
    expect(result.current.openPanel).toBeNull();
    act(() => result.current.togglePanel('escort'));
    rerender({ session: makeSession({ sessionId: 'demo-session-b', botId: 'demo-bot-b' }), botId: 'demo-bot-b' });
    expect(result.current.openPanel).toBeNull();
  });

  it.each(['detail', 'history', 'escort'] as const)('%s 复用 dev 副屏让位互斥，关闭后恢复副屏且不强清 tabs', (kind) => {
    const { options, sdkClose, sdkForceClose, sdkOpen } = makeOptions();
    const { result } = renderHook(() => useChatHeaderPanels(options));
    act(() => result.current.onSidePaneIsOpenChange(true));
    act(() => result.current.togglePanel(kind));
    expect(result.current.openPanel).toBe(kind);
    expect(sdkClose).toHaveBeenCalledTimes(1);
    expect(sdkForceClose).not.toHaveBeenCalled();
    expect(sdkOpen).not.toHaveBeenCalled();
    // 模拟真实 SDK 的 closePanel 关态回声，面板全收后仍应恢复让位的副屏。
    act(() => result.current.onSidePaneIsOpenChange(false));
    expect(result.current.sidePaneOpen).toBe(false);
    act(() => result.current.closePanel());
    expect(result.current.openPanel).toBeNull();
    expect(sdkOpen).toHaveBeenCalledTimes(1);
    expect(sdkClose).toHaveBeenCalledTimes(1);
    expect(sdkForceClose).not.toHaveBeenCalled();
  });

  it('护航打开后 Monitor 成功重建副屏即关闭护航，保留 SDK tabs', () => {
    const { options, sdkForceClose } = makeOptions();
    const openTab = jest.fn();
    const { result } = renderHook(() => useChatHeaderPanels({
      ...options,
      messages: [{
        id: 'demo-panel-message', role: 'assistant', status: 'history',
        content: `<AixUI type="panel" cardId="demo-card" tab='{"id":"demo-task","title":"演示任务","closable":true}' params="{}"></AixUI>`,
      } as never],
      panelRef: { current: { ...options.panelRef.current, openTab } as unknown as PanelHandle },
    }));
    act(() => result.current.togglePanel('escort'));
    act(() => result.current.toggleSidePane());
    expect(openTab).toHaveBeenCalledTimes(1);
    expect(openTab).toHaveBeenCalledWith(expect.objectContaining({ id: 'demo-task', type: 'aix-card-panel' }));
    // 互斥以 SDK 的真实开态为准，而不是仅依赖 Monitor 乐观镜像。
    act(() => result.current.onSidePaneIsOpenChange(true));
    expect(result.current.sidePaneOpen).toBe(true);
    expect(result.current.openPanel).toBeNull();
    expect(sdkForceClose).not.toHaveBeenCalled();
  });

  it('SDK 自动展开时对护航、detail、history 统一应用 dev 互斥语义', () => {
    const { options } = makeOptions();
    const { result } = renderHook(() => useChatHeaderPanels(options));
    act(() => result.current.togglePanel('escort'));
    act(() => result.current.onSidePaneIsOpenChange(true));
    expect(result.current.openPanel).toBeNull();
    expect(result.current.sidePaneOpen).toBe(true);
    for (const kind of ['detail', 'history'] as const) {
      act(() => result.current.togglePanel(kind));
      act(() => result.current.onSidePaneIsOpenChange(true));
      expect(result.current.openPanel).toBeNull();
      expect(options.closeFileDrawer).toHaveBeenCalled();
    }
  });

  it('SDK 容器尚未挂载时打开护航也复位副屏镜像', () => {
    const { options } = makeOptions();
    const { result } = renderHook(() => useChatHeaderPanels({ ...options, panelRef: { current: null } }));
    act(() => result.current.onSidePaneIsOpenChange(true));
    act(() => result.current.togglePanel('escort'));
    expect(result.current.sidePaneOpen).toBe(false);
    expect(result.current.openPanel).toBe('escort');
  });

  it('护航不展开内联侧栏，详情/历史收起时保留最近内容以完成 dev 过渡', () => {
    const { options } = makeOptions();
    const { result } = renderHook(() => useChatHeaderPanels(options));
    const base = result.current;
    const session = makeSession();
    const { rerender } = render(<ConversationHeaderPanels headerPanels={{ ...base, openPanel: 'history' }} session={session} taskComposerContext={taskComposerContext} />);
    const sidebar = screen.getByLabelText('历史消息面板');
    expect(sidebar).not.toHaveStyle({ width: '0px' });
    rerender(<ConversationHeaderPanels headerPanels={{ ...base, openPanel: 'escort' }} session={session} taskComposerContext={taskComposerContext} />);
    expect(screen.getByLabelText('历史消息面板')).toBe(sidebar);
    expect(sidebar).toHaveStyle({ width: '0px' });
    expect(screen.queryByLabelText('会话详情面板')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '实时任务护航面板' })).toBeInTheDocument();
    rerender(<ConversationHeaderPanels headerPanels={{ ...base, openPanel: 'detail' }} session={session} taskComposerContext={taskComposerContext} />);
    expect(screen.getByLabelText('会话详情面板')).not.toHaveStyle({ width: '0px' });
    expect(screen.queryByRole('region', { name: '实时任务护航面板' })).not.toBeInTheDocument();
  });

  it('原样透传 composer 上下文而不从 session/URL 推断身份，关闭使用同一回调', () => {
    const { options } = makeOptions();
    const { result } = renderHook(() => useChatHeaderPanels(options));
    const closePanel = jest.fn();
    const base = { ...result.current, openPanel: 'escort' as const, closePanel };
    const session = makeSession({ botId: 'session-bot:other-owner' });
    window.history.replaceState(null, '', '/workspace/chat?userId=forged&ownerId=forged');
    const { rerender } = render(<ConversationHeaderPanels headerPanels={base} session={session} taskComposerContext={taskComposerContext} />);
    expect(mockLivePanel.mock.calls.at(-1)?.[0].context).toBe(taskComposerContext);
    expect(screen.queryByRole('region', { name: '护航演示面板' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭实时护航' }));
    expect(closePanel).toHaveBeenCalledTimes(1);
    rerender(<ConversationHeaderPanels headerPanels={{ ...base, openPanel: null }} session={session} taskComposerContext={taskComposerContext} />);
    expect(screen.queryByRole('region', { name: '实时任务护航面板' })).not.toBeInTheDocument();
    expect(mockLivePanel.mock.calls.at(-1)?.[0].context).toBe(taskComposerContext);
    const nextContext = { ...taskComposerContext, ownerUserId: 'viewer-303', ownerBotId: 'bot-b:owner-b' };
    rerender(<ConversationHeaderPanels headerPanels={base} session={session} taskComposerContext={nextContext} />);
    expect(mockLivePanel.mock.calls.at(-1)?.[0].context).toBe(nextContext);
    rerender(<ConversationHeaderPanels headerPanels={base} session={session} taskComposerContext={null} />);
    expect(mockLivePanel.mock.calls.at(-1)?.[0].context).toBeNull();
  });

  it('独立宿主支持品牌注入，复用同一入口并保留本地草稿与只读聊天', () => {
    render(<TaskEscortDemoPage brandName="TeamClaw" />);
    expect(screen.getByText('TeamClaw')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '示例消息（只读，不会发送）' })).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: '发送已禁用（仅演示）' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '收藏当前会话' }));
    expect(screen.getByRole('button', { name: '取消收藏当前会话' })).toHaveAttribute('aria-pressed', 'true');
    const trigger = screen.getByRole('button', { name: '任务护航' });
    fireEvent.click(trigger);
    expect(screen.getByRole('region', { name: '护航演示面板' })).toBeInTheDocument();
    expect(document.querySelector('[data-demo="task-escort"]')).toHaveAttribute('data-escort-open', 'true');
    fireEvent.change(screen.getByRole('textbox', { name: '演示草稿' }), { target: { value: '宿主演示草稿' } });
    fireEvent.click(trigger);
    expect(screen.queryByRole('region', { name: '护航演示面板' })).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.getByRole('textbox', { name: '演示草稿' })).toHaveValue('宿主演示草稿');
    fireEvent.click(screen.getByRole('button', { name: '资源管理' }));
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('complementary', { name: '会话资源（示例）' })).toBeInTheDocument();
  });
});
