/** @jest-environment jsdom */
import { SquarePageShell } from '@/components/CollaborationSquare/SquarePageShell';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { fireEvent, render } from '@testing-library/react';
import type { ReactNode } from 'react';

jest.mock('@umijs/max', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));
jest.mock('@/hooks/useCollaborationSquare', () => ({ useCollaborationSquare: jest.fn() }));
jest.mock('@/components/CollaborationSquare/BotCard', () => () => <div>Bot card</div>);
jest.mock('@/components/CollaborationSquare/GroupCard', () => () => <div>Group card</div>);
jest.mock('@/components/CollaborationSquare/BotProfileModal', () => ({ BotProfileModal: () => null }));
jest.mock('@/components/CollaborationSquare/GroupMembersModal', () => ({ GroupMembersModal: () => null }));
jest.mock('@/components/CollaborationSquare/SquareSearchBar', () => () => <div>Search</div>);

const mockedUseCollaborationSquare = useCollaborationSquare as jest.MockedFunction<typeof useCollaborationSquare>;

function squareState(loadMore: jest.Mock) {
  return {
    visibleBots: [
      {
        id: 'bot-1',
        name: 'Bot 1',
        ownerName: 'Owner',
        description: '',
        capabilities: [],
        relationshipStatus: 'none' as const,
      },
    ],
    visibleGroups: [],
    hasMore: true,
    loading: false,
    loadingMore: false,
    error: null,
    loadMoreError: null,
    loadMore,
    load: jest.fn(),
    botQuery: '',
    groupQuery: '',
    botSearchMode: 'name' as const,
    setQuery: jest.fn(),
    setBotSearchMode: jest.fn(),
    busyKeys: [],
    share: jest.fn(),
    primaryBotAction: jest.fn(),
    createGroupSession: jest.fn(),
    openGroupMembers: jest.fn(),
    selectedBotId: null,
    botProfile: null,
    detailLoading: false,
    closeBotProfile: jest.fn(),
    copyBotId: jest.fn(),
    selectedGroupId: null,
    selectedGroup: null,
    groupMembers: [],
    closeGroupMembers: jest.fn(),
  } as unknown as ReturnType<typeof useCollaborationSquare>;
}

describe('SquarePageShell pagination trigger', () => {
  test('滚动容器距离底部 420px 内时触发下一页加载', () => {
    const loadMore = jest.fn();
    mockedUseCollaborationSquare.mockReturnValue(squareState(loadMore));
    const { container } = render(<SquarePageShell resource="bot" />);
    const scrollRoot = container.querySelector('main');
    expect(scrollRoot).not.toBeNull();
    Object.defineProperties(scrollRoot, {
      scrollHeight: { configurable: true, value: 1800 },
      clientHeight: { configurable: true, value: 800 },
      scrollTop: { configurable: true, value: 600 },
    });

    fireEvent.scroll(scrollRoot as HTMLElement);

    expect(loadMore).toHaveBeenCalledTimes(1);
  });

  test('距离列表底部超过预取范围时不触发加载', () => {
    const loadMore = jest.fn();
    mockedUseCollaborationSquare.mockReturnValue(squareState(loadMore));
    const { container } = render(<SquarePageShell resource="bot" />);
    const scrollRoot = container.querySelector('main');
    Object.defineProperties(scrollRoot, {
      scrollHeight: { configurable: true, value: 2400 },
      clientHeight: { configurable: true, value: 800 },
      scrollTop: { configurable: true, value: 500 },
    });

    fireEvent.scroll(scrollRoot as HTMLElement);

    expect(loadMore).not.toHaveBeenCalled();
  });
});
