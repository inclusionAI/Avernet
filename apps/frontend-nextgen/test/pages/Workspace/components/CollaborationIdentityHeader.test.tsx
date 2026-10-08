/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import type { WorkspaceIdentitySwitcherModel } from '@/hooks/useWorkspaceIdentitySwitcherModel';
import { CollaborationIdentityHeader } from '@/pages/Workspace/components/CollaborationIdentityHeader';
import type { Identity } from '@/services/workspace/workspaceModel';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const mockSwitchIdentity = jest.fn<(identityId: string) => void>();
const mockUseWorkspaceIdentitySwitcherModel = jest.fn<() => WorkspaceIdentitySwitcherModel>();

jest.mock('@/hooks/useWorkspaceIdentitySwitcherModel', () => ({
  useWorkspaceIdentitySwitcherModel: () => mockUseWorkspaceIdentitySwitcherModel(),
}));

const identities: Identity[] = [
  {
    id: 'human_fengtai',
    name: '我',
    kind: 'user',
    avatar: '我',
    status: 'available',
  },
  {
    id: 'bot-1',
    name: '协作 Bot',
    kind: 'bot',
    avatar: 'B',
  },
];

const model: WorkspaceIdentitySwitcherModel = {
  identities,
  activeIdentityId: 'human_fengtai',
  userAvatarUrl: 'https://example.test/avatar.png',
  humanIdentityStatus: 'ready',
  identityListLoading: false,
  switchIdentity: mockSwitchIdentity,
};

describe('CollaborationIdentityHeader', () => {
  beforeEach(() => {
    extendCapabilities({
      getBotRegistrationEnabled: () => ({ status: 'available', value: false }),
    });
    mockSwitchIdentity.mockReset();
    mockUseWorkspaceIdentitySwitcherModel.mockReset().mockReturnValue(model);
  });

  it('展示当前协作身份并可切换到 Bot 身份', async () => {
    render(<CollaborationIdentityHeader />);

    expect(screen.getByText('当前协作身份')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '切换工作身份' })).toBeInTheDocument();
    expect(screen.getByText('用户 ID：fengtai')).toBeInTheDocument();
    const header = screen.getByRole('region', { name: '当前协作身份' });
    expect(header).toHaveClass('px-3');
    expect(header).not.toHaveClass('px-6');
    const identityCard = screen.getByRole('button', { name: '当前协作身份：我' });
    expect(identityCard).toHaveClass('bg-background');
    expect(identityCard).not.toHaveClass('bg-muted/40');
    expect(identityCard).toHaveClass('h-auto', 'min-h-10', 'gap-2', 'px-4', 'py-2');
    expect(screen.getByRole('img', { name: '我' })).toHaveStyle({ width: '24px', height: '24px' });
    expect(screen.getByText('我')).toHaveClass('text-xs');

    fireEvent.click(screen.getByRole('button', { name: '切换工作身份' }));
    fireEvent.click(await screen.findByRole('button', { name: /协作 Bot/ }));

    expect(mockSwitchIdentity).toHaveBeenCalledWith('bot-1');
  });

  it('展示 model 透传的身份列表加载状态', () => {
    mockUseWorkspaceIdentitySwitcherModel.mockReturnValue({
      ...model,
      identities: [],
      activeIdentityId: null,
      identityListLoading: true,
    });

    render(<CollaborationIdentityHeader />);

    expect(screen.getByRole('button', { name: '协作身份加载中' })).toHaveClass('min-h-10', 'gap-2', 'px-4', 'py-2');
  });
});
