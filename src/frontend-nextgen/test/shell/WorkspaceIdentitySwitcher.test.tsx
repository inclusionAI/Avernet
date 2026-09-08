/** @jest-environment jsdom */
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const mockSwitchIdentity = jest.fn();

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: {
      userId: 'external-user-1',
      displayName: '开源用户',
      avatarUrl: 'https://example.test/user.png',
      online: true,
    },
  }),
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
        { id: 'human_external-user-1', kind: 'user', displayName: '验收用户', online: true },
        { id: 'bot-1', kind: 'bot', displayName: '协作 Bot', online: true },
      ],
      activeIdentityId: 'human_external-user-1',
    });
    render(<WorkspaceIdentitySwitcher />);

    expect(screen.getByText('工作身份')).toBeInTheDocument();
    expect(
      screen.queryByText('当前工作身份决定你以个人或指定 Bot 身份使用工作区各项功能，并影响各菜单中可查看的数据和可执行的操作。'),
    ).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: '当前协作身份：开源用户' });
    expect(trigger).toHaveClass('rounded-lg', 'border', 'bg-muted/60', 'min-h-9', 'px-2.5', 'py-1.5');
    expect(screen.getByText('开源用户')).toBeInTheDocument();
    expect(screen.queryByText('验收用户')).not.toBeInTheDocument();
    expect(screen.getByText('用户')).toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.queryByRole('button', { name: '进入协作权限设置' })).not.toBeInTheDocument();
    expect(await screen.findAllByText('用户')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /协作 Bot/ }));
    expect(mockSwitchIdentity).toHaveBeenCalledWith('bot-1');
  });

  it('只为 ID 匹配的 human identity 覆盖认证名称，不覆盖其他用户身份', () => {
    useWorkspaceStore.setState({
      identities: [
        { id: 'human_other-user', kind: 'user', displayName: '其他用户', online: true },
        { id: 'bot-1', kind: 'bot', displayName: '协作 Bot', online: true },
      ],
      activeIdentityId: 'human_other-user',
    });
    render(<WorkspaceIdentitySwitcher />);

    expect(screen.getByRole('button', { name: '当前协作身份：其他用户' })).toBeInTheDocument();
    expect(screen.getByText('其他用户')).toBeInTheDocument();
    expect(screen.queryByText('开源用户')).not.toBeInTheDocument();
  });

  it('Bot 工作身份选中态展示通用 BOT 标识，弹层保留 Bot 类型细分', async () => {
    useWorkspaceStore.setState({
      identities: [
        { id: 'human-1', kind: 'user', displayName: '验收用户', online: true },
        { id: 'bot-1', kind: 'bot', displayName: '协作 Bot', botType: 'personal', online: true },
      ],
      activeIdentityId: 'bot-1',
    });
    render(<WorkspaceIdentitySwitcher />);

    expect(screen.getByText('BOT')).toBeInTheDocument();
    expect(screen.queryByText('个人 Bot')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：协作 Bot' }));
    expect(await screen.findByText('个人 Bot')).toBeInTheDocument();
  });
});
