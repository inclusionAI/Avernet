/** @jest-environment jsdom */
import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import type { BotCatalogViewModel } from '@/domain/collaborationSquare/types';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

jest.mock('@/components/CollaborationSquare/SquareSearchBar', () => ({
  __esModule: true,
  default: () => <div data-testid="square-search" />,
}));
jest.mock('@/components/CollaborationSquare/BotCard', () => ({
  __esModule: true,
  default: () => <div data-testid="bot-card" />,
}));
jest.mock('@/components/CollaborationSquare/BotProfileModal', () => ({
  BotProfileModal: () => null,
}));
jest.mock('@/components/CollaborationSquare/SquareIdentityPicker', () => ({
  SquareIdentityPicker: () => <div data-testid="square-identity-picker" />,
}));

function buildVm(overrides: Partial<BotCatalogViewModel> = {}): BotCatalogViewModel {
  return {
    activeActor: null,
    bots: [],
    busyKeys: [],
    query: '',
    mode: 'name',
    loading: false,
    error: null,
    hasMore: false,
    loadingMore: false,
    loadMoreError: null,
    setQuery: jest.fn(),
    setMode: jest.fn(),
    reload: jest.fn(),
    loadMore: jest.fn(),
    primaryAction: jest.fn(),
    share: jest.fn(),
    openProfile: jest.fn(),
    closeProfile: jest.fn(),
    selectedBotId: null,
    botProfile: null,
    detailLoading: false,
    copyBotId: jest.fn(),
    ...overrides,
  };
}

describe('PublicBotCatalogPanel identity picker', () => {
  it('嵌入模块级身份选择器，替代原全局身份提示卡', () => {
    render(<PublicBotCatalogPanel vm={buildVm()} scrollRootRef={{ current: null }} />);

    expect(screen.getByTestId('square-identity-picker')).toBeInTheDocument();
    expect(screen.getByTestId('square-search')).toBeInTheDocument();
    expect(screen.queryByText(/当前工作身份/)).not.toBeInTheDocument();
    expect(screen.queryByText(/左上角工作身份切换/)).not.toBeInTheDocument();
  });

  it('身份选择器位于搜索栏之前（提示卡原位置）', () => {
    const { container } = render(<PublicBotCatalogPanel vm={buildVm()} scrollRootRef={{ current: null }} />);

    const picker = screen.getByTestId('square-identity-picker');
    const search = screen.getByTestId('square-search');
    expect(container.contains(picker)).toBe(true);
    expect(picker.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
});
