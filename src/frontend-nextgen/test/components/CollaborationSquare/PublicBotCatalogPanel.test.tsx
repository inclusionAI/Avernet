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

describe('PublicBotCatalogPanel identity display', () => {
  it('uses the authenticated name only when the active human id matches', () => {
    const { rerender } = render(
      <PublicBotCatalogPanel
        vm={buildVm()}
        scrollRootRef={{ current: null }}
        activeIdentity={{ id: 'human_447147', name: '447147', kind: 'user' }}
        authenticatedUserId="447147"
        authenticatedUserName="风太"
      />,
    );
    expect(screen.getByText('当前工作身份：风太')).toBeInTheDocument();

    rerender(
      <PublicBotCatalogPanel
        vm={buildVm()}
        scrollRootRef={{ current: null }}
        activeIdentity={{ id: 'human_447148', name: '其他用户', kind: 'user' }}
        authenticatedUserId="447147"
        authenticatedUserName="风太"
      />,
    );
    expect(screen.getByText('当前工作身份：其他用户')).toBeInTheDocument();
    expect(screen.queryByText('当前工作身份：风太')).not.toBeInTheDocument();
  });

  it('does not replace a Bot identity whose compound id contains the human id', () => {
    render(
      <PublicBotCatalogPanel
        vm={buildVm()}
        scrollRootRef={{ current: null }}
        activeIdentity={{ id: 'bot_xxx:447147', name: '协作 Bot', kind: 'bot' }}
        authenticatedUserId="447147"
        authenticatedUserName="风太"
      />,
    );
    expect(screen.getByText('当前工作身份：协作 Bot')).toBeInTheDocument();
    expect(screen.queryByText('当前工作身份：风太')).not.toBeInTheDocument();
  });
});
