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

  it('v2 身份行：紧凑 chip（头像 + 名称 + (短ID) + 用户|Bot 分段），点击打开列表可切换 Bot 身份', async () => {
    render(<CollaborationIdentityHeader />);

    // v2（dmore artboard-010）：无「当前协作身份」标题区与独立切换按钮，整行即触发器。
    expect(screen.queryByText('当前协作身份')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '切换工作身份' })).not.toBeInTheDocument();
    const header = screen.getByRole('region', { name: '当前协作身份' });
    expect(header).toHaveClass('h-12', 'px-3');
    const identityCard = screen.getByRole('button', { name: '当前协作身份：我' });
    // 宽度占满侧栏（w-full），气泡按触发宽度展开。
    expect(identityCard).toHaveClass('h-8', 'w-full');
    expect(identityCard).toHaveTextContent('我');
    expect(identityCard).toHaveTextContent('(fengtai)');
    // 16px 头像 + 分段指示（用户高亮为白胶囊）。
    expect(screen.getByRole('img', { name: '我' })).toHaveStyle({ width: '16px', height: '16px' });
    expect(identityCard.querySelector('[aria-hidden="true"] > span:nth-child(1)')).toHaveTextContent('用户');

    fireEvent.click(identityCard);
    // 协作群菜单语境:弹层标题为「切换协作身份」、副提示写实际用途、说明图标移除;
    // 其他场景缺省「切换工作身份」/「切换后对工作区全局生效」且保留说明图标。
    expect(await screen.findByText('切换协作身份')).toBeInTheDocument();
    expect(screen.queryByText('切换工作身份')).not.toBeInTheDocument();
    expect(screen.getByText('选择参与协作群会话的身份')).toBeInTheDocument();
    expect(screen.queryByLabelText('协作身份说明')).not.toBeInTheDocument();
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

    expect(screen.getByRole('button', { name: '协作身份加载中' })).toHaveClass('h-8', 'w-full');
  });
});

it('read-only header has identity details but no switchable button or popover', () => {
  mockUseWorkspaceIdentitySwitcherModel.mockReturnValue(model);
  render(<CollaborationIdentityHeader readOnly />);
  expect(screen.getByText('当前协作身份')).toBeInTheDocument();
  expect(screen.getByText('用户 ID：fengtai')).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
