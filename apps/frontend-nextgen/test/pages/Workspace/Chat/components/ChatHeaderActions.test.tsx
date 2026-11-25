/** @jest-environment jsdom */
import { ChatHeaderActions } from '@/pages/Workspace/Chat/components/ChatHeaderActions';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

// 历史消息入口由 HISTORY_SEARCH_ENABLED 控制(默认 false 隐藏,后端检索就绪后置 true 恢复):
// 本套件按「开关开」态覆盖按钮行为,开关默认值由 featureFlags 单测守护。
jest.mock('@/domain/conversation/featureFlags', () => ({ HISTORY_SEARCH_ENABLED: true }));

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
