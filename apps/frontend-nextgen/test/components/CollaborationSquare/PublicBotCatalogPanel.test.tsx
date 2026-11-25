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

const bots = [
  {
    id: 'bot-a:900003',
    name: 'Bot A',
    ownerName: '示例用户',
    description: '描述',
    capabilities: [],
    relationshipStatus: 'none' as const,
  },
];

function buildVm(overrides: Partial<BotCatalogViewModel> = {}): BotCatalogViewModel {
  return {
    activeActor: { type: 'human', id: '900003' },
    bots,
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
    botProfileError: null,
    detailLoading: false,
    copyBotId: jest.fn(),
    ...overrides,
  };
}

describe('PublicBotCatalogPanel 纯内容网格', () => {
  it('不再内嵌搜索栏与身份选择器（工具栏已上移协作广场页头）', () => {
    render(<PublicBotCatalogPanel vm={buildVm()} scrollRootRef={{ current: null }} />);

    expect(screen.queryByTestId('square-search')).not.toBeInTheDocument();
    expect(screen.queryByTestId('square-identity-picker')).not.toBeInTheDocument();
    expect(screen.queryByText(/当前工作身份/)).not.toBeInTheDocument();
  });

  it('按 dmore 网格渲染 Bot 卡片：16px 间距、三列断点', () => {
    const { container } = render(
      <PublicBotCatalogPanel vm={buildVm()} scrollRootRef={{ current: null }} />,
    );

    const cards = screen.getAllByTestId('bot-card');
    expect(cards).toHaveLength(bots.length);
    const grid = cards[0].parentElement;
    expect(grid).toHaveClass('grid', 'gap-4', 'md:grid-cols-2', 'xl:grid-cols-3');
    expect(container.contains(cards[0])).toBe(true);
  });

  it('智能 name_fallback 时在结果上方展示「名称匹配」提示；semantic 命中不展示', () => {
    const { rerender } = render(
      <PublicBotCatalogPanel vm={buildVm({ mode: 'smart', searchResultMode: 'name_fallback' })} scrollRootRef={{ current: null }} />,
    );
    expect(screen.getByText('未命中语义匹配，已按名称匹配展示结果')).toBeInTheDocument();

    rerender(
      <PublicBotCatalogPanel vm={buildVm({ mode: 'smart', searchResultMode: 'semantic' })} scrollRootRef={{ current: null }} />,
    );
    expect(screen.queryByText('未命中语义匹配，已按名称匹配展示结果')).not.toBeInTheDocument();
  });
});
