/** @jest-environment jsdom */
import { PrimaryNavContext, type PrimaryNavApi } from '@/components/Workspace/PrimaryNav';
import { GroupChatPane, type GroupChatPaneProps } from '@/pages/Workspace/components/GroupChatPane';
import type { PanelHandle } from '@tc-chat/core';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { MemoryRouter } from 'react-router-dom';

const noop = () => undefined;

function renderEmptyPane(onOpenSessionList?: () => void, primaryNavApi: PrimaryNavApi | null = null) {
  const props: GroupChatPaneProps = {
    group: null,
    session: null,
    chat: {
      messages: [],
      setMessages: noop,
      setMessage: noop,
      removeMessage: noop,
      onRequest: noop,
      onReload: noop,
      abort: noop,
      isRequesting: false,
      isDefaultMessagesRequesting: false,
      connectionStatus: 'disconnected',
      retryCount: 0,
      reconnect: noop,
      isConnected: false,
      isConnecting: false,
    },
    supportState: { phase: 'ready', error: null },
    connectionStatus: 'disconnected',
    send: noop,
    submitPanelMessage: noop,
    submitTaskExecutionMessage: noop,
    abortBot: () => Promise.resolve(),
    abortingBotIds: new Set(),
    reconnect: noop,
    reloadHistory: noop,
    canManageGroup: { allowed: false },
    activePanel: 'none',
    onTogglePanel: noop,
    favorites: { favorite: false, disabled: true, onToggle: noop },
    sidePaneOpen: false,
    onToggleSidePane: noop,
    onRequestDissolve: noop,
    onRequestShareGroup: async () => ({ ok: true, data: { invitationUrl: '' } }),
    onRequestShareSession: async () => ({ ok: true, data: { invitationUrl: '' } }),
    panelRef: createRef<PanelHandle>(),
    onOpenSessionList,
  };
  return render(
    <MemoryRouter>
      <PrimaryNavContext.Provider value={primaryNavApi}>
        <GroupChatPane {...props} />
      </PrimaryNavContext.Provider>
    </MemoryRouter>,
  );
}

describe('GroupChatPane 未选群时的窄屏入口', () => {
  it('保留同排的一级导航与群列表入口，并分别触发对应回调', () => {
    const onOpenSessionList = jest.fn();
    const api: PrimaryNavApi = {
      openNav: jest.fn(),
      mountNavEntry: jest.fn(),
      unmountNavEntry: jest.fn(),
    };
    const { unmount } = renderEmptyPane(onOpenSessionList, api);

    const nav = screen.getByRole('button', { name: '打开导航' });
    const list = screen.getByRole('button', { name: '打开会话列表' });
    expect(nav.parentElement).toBe(list.parentElement);
    expect(nav.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(list.parentElement).toHaveClass('lg:hidden');
    expect(screen.getByText('欢迎进入协作群对话现场')).toBeInTheDocument();

    fireEvent.click(nav);
    expect(api.openNav).toHaveBeenCalledTimes(1);
    expect(onOpenSessionList).not.toHaveBeenCalled();
    fireEvent.click(list);
    expect(onOpenSessionList).toHaveBeenCalledTimes(1);
    expect(api.mountNavEntry).toHaveBeenCalledTimes(1);
    unmount();
    expect(api.unmountNavEntry).toHaveBeenCalledTimes(1);
  });

  it('壳外仍可打开群列表，不渲染一级导航入口', () => {
    const onOpenSessionList = jest.fn();
    renderEmptyPane(onOpenSessionList);
    expect(screen.queryByRole('button', { name: '打开导航' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开会话列表' }));
    expect(onOpenSessionList).toHaveBeenCalledTimes(1);
  });

  it('未提供列表回调时不渲染无效入口', () => {
    renderEmptyPane();
    expect(screen.queryByRole('button', { name: '打开会话列表' })).not.toBeInTheDocument();
    expect(screen.getByText('欢迎进入协作群对话现场')).toBeInTheDocument();
  });
});
