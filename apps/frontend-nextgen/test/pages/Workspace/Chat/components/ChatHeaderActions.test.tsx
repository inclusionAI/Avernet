/** @jest-environment jsdom */
import { ChatHeaderActions } from '@/pages/Workspace/Chat/components/ChatHeaderActions';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

const baseProps = {
  favorites: { favorite: false, disabled: false, onToggle: jest.fn() },
  openPanel: null,
  onTogglePanel: jest.fn(),
  fileDrawerOpen: false,
  onToggleFileDrawer: jest.fn(),
  sidePaneOpen: false,
  onToggleSidePane: jest.fn(),
};

describe('ChatHeaderActions', () => {
  it('好友 Coding Bot 兼容模式隐藏资源管理和副屏，保留基础会话操作', () => {
    render(<ChatHeaderActions {...baseProps} showResourceManager={false} showSidePane={false} />);

    expect(screen.getByRole('button', { name: '收藏当前会话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '会话管理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '历史消息' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '资源管理' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '副屏' })).not.toBeInTheDocument();
  });
});
