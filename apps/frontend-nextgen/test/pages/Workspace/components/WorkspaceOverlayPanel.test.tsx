/** @jest-environment jsdom */
import { ChatHeaderRightPanel } from '@/pages/Workspace/Chat/components/ChatHeaderRightPanel';
import { GroupHistorySidebar } from '@/pages/Workspace/components/GroupChatPane/GroupHistorySidebar';
import { searchConversationMessages } from '@/services/workspace/conversationSearchService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { createPortal } from 'react-dom';

describe('conversation-local right panel', () => {
  beforeEach(() => window.localStorage.clear());

  it('overlays the conversation with a blurred backdrop and preserves resizing', () => {
    const onClose = jest.fn();
    render(
      <div className="relative isolate flex">
        <main>会话内容</main>
        <ChatHeaderRightPanel title="会话详情" onClose={onClose}>
          <button type="button">面板操作</button>
        </ChatHeaderRightPanel>
      </div>,
    );
    const panel = screen.getByLabelText('会话详情面板');
    const backdrop = screen.getByRole('button', { name: '关闭会话详情面板' });
    expect(panel.parentElement).toHaveClass('absolute', 'inset-0');
    expect(backdrop).toHaveClass('backdrop-blur-sm');
    expect(panel).toHaveClass('max-w-full');
    fireEvent.keyDown(screen.getByRole('separator'), { key: 'ArrowLeft' });
    expect(panel).toHaveStyle({ width: '388px' });
    fireEvent.pointerDown(screen.getByRole('button', { name: '面板操作' }));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.pointerDown(backdrop);
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('keeps portaled controls interactive and dismisses when navigation is used', () => {
    const onClose = jest.fn();
    render(
      <>
        <button type="button">切换会话</button>
        <ChatHeaderRightPanel title="历史消息" onClose={onClose}>
          {createPortal(<div role="dialog">内部弹层</div>, document.body)}
        </ChatHeaderRightPanel>
      </>,
    );
    fireEvent.pointerDown(screen.getByRole('dialog'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.pointerDown(screen.getByRole('button', { name: '切换会话' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes group history after selecting a message so the highlighted conversation is visible', () => {
    const onClose = jest.fn();
    const onLocate = jest.fn();
    const query = { keyword: '', sender: null, todayOnly: false };
    const { items } = searchConversationMessages([{ id: 'm1', role: 'user', content: '目标消息', status: 'history' }], query);
    render(
      <GroupHistorySidebar
        search={{ query, source: 'local', results: items, setKeyword: jest.fn(), setSender: jest.fn(), setTodayOnly: jest.fn() }}
        onClose={onClose}
        onLocate={onLocate}
      />,
    );
    fireEvent.click(screen.getByText('目标消息'));
    expect(onLocate).toHaveBeenCalledWith('m1');
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
