/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { ChatHeaderRightPanel } from '@/pages/Workspace/Chat/components/ChatHeaderRightPanel';
import { GroupHistorySidebar } from '@/pages/Workspace/components/GroupChatPane/GroupHistorySidebar';
import { SessionFilesSidebar } from '@/pages/Workspace/components/GroupChatPane/SessionFilesSidebar';
import { GroupMembersPanelSlot } from '@/pages/Workspace/components/GroupMembersPanelSlot';
import { ResizableWorkspaceSidebar } from '@/pages/Workspace/components/ResizableWorkspaceSidebar';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/pages/Workspace/components/GroupChatPane/SessionFilesPanel', () => ({
  SessionFilesPanel: () => <button type="button">文件内容</button>,
}));
jest.mock('@/pages/Workspace/components/MembersPanel', () => ({
  MembersPanel: () => <button type="button">成员内容</button>,
}));

const search = {
  query: { keyword: '', sender: null, todayOnly: false },
  setKeyword: jest.fn(),
  setSender: jest.fn(),
  setTodayOnly: jest.fn(),
  source: 'local' as const,
  results: [],
};

describe('聊天右侧面板的外部点击', () => {
  it.each(['会话详情', '对话历史', '群历史', '群资源', '群成员'])(
    '%s：内部操作保持打开，点击消息区收起且不拦截原点击',
    (kind) => {
      const close = jest.fn();
      const outsideClick = jest.fn();
      render(
        <>
          {kind === '群历史' ? (
            <GroupHistorySidebar search={search} onLocate={jest.fn()} onClose={close} />
          ) : kind === '群资源' ? (
            <SessionFilesSidebar sessionId="s1" participants={[]} onClose={close} />
          ) : kind === '群成员' ? (
            <GroupMembersPanelSlot
              open
              group={{} as GroupView}
              session={null}
              canManage={{ allowed: true }}
              onClose={close}
            />
          ) : (
            <ChatHeaderRightPanel title={kind} onClose={close}>
              <input aria-label="面板输入" />
            </ChatHeaderRightPanel>
          )}
          <button type="button" onClick={outsideClick}>
            消息区
          </button>
        </>,
      );
      const inside =
        kind === '群成员'
          ? screen.getByText('成员内容')
          : kind === '群资源'
          ? screen.getByText('文件内容')
          : kind === '群历史'
          ? screen.getByRole('textbox')
          : screen.getByLabelText('面板输入');
      expect(inside.closest('aside')).toHaveStyle({ width: '380px' });
      fireEvent.pointerDown(inside);
      expect(close).not.toHaveBeenCalled();
      fireEvent.pointerDown(screen.getByText('消息区'));
      fireEvent.click(screen.getByText('消息区'));
      expect(close).toHaveBeenCalledTimes(1);
      expect(outsideClick).toHaveBeenCalledTimes(1);
    },
  );

  it('右栏入口、拖宽手柄、Portal 菜单/弹窗不误关；卸载后移除监听', () => {
    const close = jest.fn();
    const { unmount } = render(
      <>
        <ChatHeaderRightPanel title="详情" onClose={close}>
          <span>内部</span>
        </ChatHeaderRightPanel>
        <button type="button" data-workspace-panel-trigger>
          面板入口
        </button>
        <div role="dialog">
          <button type="button">上传弹窗</button>
        </div>
        <div role="menu">
          <button type="button">菜单项</button>
        </div>
        <div role="listbox">
          <button type="button">下拉选项</button>
        </div>
      </>,
    );
    for (const label of ['内部', '面板入口', '上传弹窗', '菜单项', '下拉选项']) {
      fireEvent.pointerDown(screen.getByText(label));
    }
    fireEvent.pointerDown(screen.getByRole('separator'));
    expect(close).not.toHaveBeenCalled();
    fireEvent.pointerDown(document.body);
    expect(close).toHaveBeenCalledTimes(1);
    unmount();
    fireEvent.pointerDown(document.body);
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('普通左侧列表仍保持常驻', () => {
    render(
      <ResizableWorkspaceSidebar ariaLabel="左侧列表">
        <span>列表</span>
      </ResizableWorkspaceSidebar>,
    );
    fireEvent.pointerDown(document.body);
    expect(screen.getByLabelText('左侧列表')).toBeInTheDocument();
  });
});
