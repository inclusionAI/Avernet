/** @jest-environment jsdom */
import { ChatHeaderRightPanel } from '@/pages/Workspace/Chat/components/ChatHeaderRightPanel';
import { GroupHistorySidebar } from '@/pages/Workspace/components/GroupChatPane/GroupHistorySidebar';
import { searchConversationMessages } from '@/services/workspace/conversationSearchService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { createPortal } from 'react-dom';

describe('conversation-local right panel', () => {
  beforeEach(() => window.localStorage.clear());

  it('展开为对话区内联副屏（无蒙层抽屉），保持拖宽并保留外部点击收起', () => {
    const onClose = jest.fn();
    render(
      <div className="flex">
        <main>会话内容</main>
        <ChatHeaderRightPanel title="会话详情" onClose={onClose}>
          <button type="button">面板操作</button>
        </ChatHeaderRightPanel>
      </div>,
    );
    const panel = screen.getByLabelText('会话详情面板');
    // 内联副屏：随 flex 流挤开对话区，非 absolute 蒙层 + 抽屉。
    expect(panel.parentElement).not.toHaveClass('absolute');
    expect(screen.queryByRole('button', { name: '关闭会话详情面板' })).not.toBeInTheDocument();
    expect(panel).toHaveStyle({ width: '380px' });
    fireEvent.keyDown(screen.getByRole('separator'), { key: 'ArrowLeft' });
    expect(panel).toHaveStyle({ width: '388px' });
    fireEvent.pointerDown(screen.getByRole('button', { name: '面板操作' }));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.pointerDown(screen.getByText('会话内容'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('收起态宽度收 0 且内容退出可交互；重新展开恢复宽度', () => {
    const onClose = jest.fn();
    const view = render(
      <div className="flex">
        <main>会话内容</main>
        <ChatHeaderRightPanel title="历史消息" open={false} onClose={onClose}>
          <input aria-label="面板输入" />
        </ChatHeaderRightPanel>
      </div>,
    );
    const panel = screen.getByLabelText('历史消息面板');
    expect(panel).toHaveStyle({ width: '0px' });
    expect(panel).toHaveClass('opacity-0', 'pointer-events-none');
    // 收起态不挂外部点击收起监听（onDismiss 退位）。
    fireEvent.pointerDown(screen.getByText('会话内容'));
    expect(onClose).not.toHaveBeenCalled();
    view.rerender(
      <div className="flex">
        <main>会话内容</main>
        <ChatHeaderRightPanel title="历史消息" onClose={onClose}>
          <input aria-label="面板输入" />
        </ChatHeaderRightPanel>
      </div>,
    );
    expect(panel).toHaveStyle({ width: '380px' });
    expect(panel).not.toHaveClass('opacity-0');
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
