/** @jest-environment jsdom */
import { GroupParticipantPicker } from '@/pages/Workspace/components/Modals/GroupParticipantPicker';
import type { UseGroupCollaborationPickerResult } from '@/pages/Workspace/hooks/useGroupCollaborationPicker';
import type { CollaborationBotView } from '@/services/workspace/collaborationCandidateService';
import { jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const unknownBot = {
  id: 'b2:900003',
  name: 'b2:900003',
  online: false,
  status: 'hidden',
  reachability: 'reachable',
  visibility: 'private',
  isFriend: true,
  detailsResolved: false,
} as unknown as CollaborationBotView;

function makePicker(
  friends: CollaborationBotView[],
  overrides: Partial<UseGroupCollaborationPickerResult> = {},
): UseGroupCollaborationPickerResult {
  return {
    tab: 'friends',
    setTab: jest.fn(),
    search: '',
    setSearch: jest.fn(),
    filterMode: 'name',
    setFilterMode: jest.fn(),
    friends,
    mine: [],
    candidates: [],
    isLoadingFriends: false,
    isLoadingMine: false,
    isLoadingCandidates: false,
    isLoadingMore: false,
    friendsHasMore: false,
    mineHasMore: false,
    candidatesHasMore: false,
    retry: jest.fn(),
    loadMore: jest.fn(),
    recommendations: [],
    isLoadingRecommend: false,
    recommendError: undefined,
    recommendSource: null,
    recommendByGoal: jest.fn(),
    retryRecommend: jest.fn(),
    ...overrides,
  };
}

it('renders an unresolved collaboration friend as a disabled unknown option', () => {
  const onToggle = jest.fn();

  render(
    <GroupParticipantPicker
      picker={makePicker([unknownBot])}
      selectedIds={[]}
      selectedOptions={[]}
      showMineTab={false}
      onToggle={onToggle}
    />,
  );

  const option = screen.getByRole('button', { name: /b2:900003/ });
  expect(option).toBeDisabled();
  expect(option).toHaveClass('bg-muted/40');
  expect(screen.getByText('未知')).toBeInTheDocument();

  fireEvent.click(option);
  expect(onToggle).not.toHaveBeenCalled();
});

it('fills the remaining modal height without extending beneath the footer', () => {
  render(
    <GroupParticipantPicker
      picker={makePicker([
        {
          id: 'b1',
          name: 'Alpha',
          online: true,
          status: 'online',
          reachability: 'reachable',
          visibility: 'public',
          isFriend: true,
        },
      ])}
      selectedIds={[]}
      selectedOptions={[]}
      showMineTab={false}
      fillAvailableHeight
      onToggle={jest.fn()}
    />,
  );

  expect(screen.getByTestId('group-participant-picker')).toHaveClass('flex', 'min-h-0', 'flex-1', 'flex-col');
  expect(screen.getByTestId('group-participant-list').parentElement).toHaveClass('min-h-0', 'flex-1');
  expect(screen.getByTestId('group-participant-list')).toHaveClass('h-full', 'max-h-none', 'pb-2');
});

it('keeps the bot list readable when many selected bots wrap into multiple rows', () => {
  const selectedOptions = Array.from({ length: 14 }, (_, index) => ({
    id: `bot-${index}`,
    name: `Bot ${index}`,
  }));

  render(
    <GroupParticipantPicker
      picker={makePicker([
        {
          id: 'candidate-1',
          name: 'Candidate 1',
          online: true,
          status: 'online',
          reachability: 'reachable',
          visibility: 'public',
          isFriend: true,
        },
      ])}
      selectedIds={selectedOptions.map((bot) => bot.id)}
      selectedOptions={selectedOptions}
      showMineTab={false}
      fillAvailableHeight
      onToggle={jest.fn()}
    />,
  );

  expect(screen.getByTestId('group-participant-selected')).toHaveClass('max-h-24', 'overflow-y-auto');
  expect(screen.getByTestId('group-participant-list')).toHaveClass('min-h-[180px]');
});

it('keeps named selected member chips removable and uses compact list typography', () => {
  const onToggle = jest.fn();
  render(
    <GroupParticipantPicker
      picker={makePicker([
        {
          id: 'candidate-1',
          name: 'Candidate 1',
          online: true,
          status: 'online',
          reachability: 'reachable',
          visibility: 'public',
          isFriend: true,
        },
      ])}
      selectedIds={['selected-1']}
      selectedOptions={[{ id: 'selected-1', name: 'Selected Bot' }]}
      showMineTab={false}
      onToggle={onToggle}
    />,
  );

  expect(screen.getByRole('group', { name: 'Selected Bot已选成员' })).toContainElement(
    screen.getByText('Selected Bot'),
  );
  fireEvent.click(screen.getByRole('button', { name: '移除Selected Bot' }));
  expect(onToggle).toHaveBeenCalledWith('selected-1');
  expect(screen.getByText('Candidate 1')).toHaveClass('text-xs');
});

it('does not show the redundant collaboration Bot range hint', () => {
  render(
    <GroupParticipantPicker
      picker={{ ...makePicker([]), tab: 'candidates', candidates: [] }}
      selectedIds={[]}
      selectedOptions={[]}
      showMineTab={false}
      onToggle={jest.fn()}
    />,
  );

  expect(screen.queryByText('可协作 Bot 范围：公开 Bot 与已接受好友的集合。')).not.toBeInTheDocument();
});

it('shows the filter mode switch only on the candidates tab and switches modes', () => {
  const setFilterMode = jest.fn();
  const friendsPicker = makePicker([]);
  const { rerender } = render(
    <GroupParticipantPicker
      picker={friendsPicker}
      selectedIds={[]}
      selectedOptions={[]}
      showMineTab={false}
      onToggle={jest.fn()}
    />,
  );
  expect(screen.queryByText('按名称筛选')).not.toBeInTheDocument();
  expect(screen.queryByText('按画像筛选')).not.toBeInTheDocument();

  rerender(
    <GroupParticipantPicker
      picker={makePicker([], { tab: 'candidates', setFilterMode })}
      selectedIds={[]}
      selectedOptions={[]}
      showMineTab={false}
      onToggle={jest.fn()}
    />,
  );
  expect(screen.getByText('按名称筛选')).toBeInTheDocument();
  fireEvent.click(screen.getByText('按画像筛选'));
  expect(setFilterMode).toHaveBeenCalledWith('profile');
});

it('renders profile recommendations with short profile summaries', () => {
  render(
    <GroupParticipantPicker
      picker={makePicker([], {
        tab: 'candidates',
        filterMode: 'profile',
        search: '值班',
        recommendations: [
          {
            id: 'b1',
            name: '值班助手',
            online: true,
            status: 'online',
            reachability: 'reachable',
            visibility: 'public',
            recommendScore: 0.9,
            shortProfile: '负责值班告警响应',
          },
        ],
      })}
      selectedIds={[]}
      selectedOptions={[]}
      showMineTab={false}
      onToggle={jest.fn()}
    />,
  );

  expect(screen.getByText('值班助手')).toBeInTheDocument();
  expect(screen.getByText('负责值班告警响应')).toBeInTheDocument();
});

it('shows the empty recommendation hint when the profile search has no candidates', () => {
  render(
    <GroupParticipantPicker
      picker={makePicker([], {
        tab: 'candidates',
        filterMode: 'profile',
        search: '值班',
        recommendations: [],
      })}
      selectedIds={[]}
      selectedOptions={[]}
      showMineTab={false}
      onToggle={jest.fn()}
    />,
  );

  expect(screen.getByText('暂无与该目标相关的可协作 Bot')).toBeInTheDocument();
  expect(screen.getByText('清除关键词后恢复原可协作 Bot 列表。')).toBeInTheDocument();
});

it('shows the recommendation error with a retry action', () => {
  const retryRecommend = jest.fn();
  render(
    <GroupParticipantPicker
      picker={makePicker([], {
        tab: 'candidates',
        filterMode: 'profile',
        search: '值班',
        recommendError: '推荐可协作 Bot 失败，请稍后重试。',
        retryRecommend,
      })}
      selectedIds={[]}
      selectedOptions={[]}
      showMineTab={false}
      onToggle={jest.fn()}
    />,
  );

  expect(screen.getByText('推荐可协作 Bot 失败，请稍后重试。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(retryRecommend).toHaveBeenCalled();
});
