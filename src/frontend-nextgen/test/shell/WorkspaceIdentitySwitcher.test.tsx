/** @jest-environment jsdom */
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const mockSwitchIdentity = jest.fn();

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({ identity: { avatarUrl: 'https://example.test/user.png' } }),
}));
jest.mock('@/services/workspace/workspaceService', () => ({
  workspaceService: { switchIdentity: (...args: unknown[]) => mockSwitchIdentity(...args) },
}));

const { WorkspaceIdentitySwitcher } =
  require('@/shell/WorkspaceIdentitySwitcher') as typeof import('@/shell/WorkspaceIdentitySwitcher');

describe('WorkspaceIdentitySwitcher', () => {
  it('从全局身份状态切换身份，不在弹层重复展示协作权限入口', async () => {
    useWorkspaceStore.setState({
      identities: [
        { id: 'human-1', kind: 'user', displayName: '验收用户', online: true },
        { id: 'bot-1', kind: 'bot', displayName: '协作 Bot', online: true },
      ],
      activeIdentityId: 'human-1',
    });
    render(<WorkspaceIdentitySwitcher />);

    expect(screen.getByText('工作身份')).toBeInTheDocument();
    expect(
      screen.queryByText('当前协作身份决定在对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围'),
    ).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: '当前协作身份：验收用户' });
    expect(trigger).toHaveClass('rounded-lg', 'border', 'bg-muted/60', 'min-h-9', 'px-2.5', 'py-1.5');
    expect(screen.queryByText('用户')).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.queryByRole('button', { name: '进入协作权限设置' })).not.toBeInTheDocument();
    expect(await screen.findByText('用户')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /协作 Bot/ }));
    expect(mockSwitchIdentity).toHaveBeenCalledWith('bot-1');
  });
});
