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

function makePicker(friends: CollaborationBotView[]): UseGroupCollaborationPickerResult {
  return {
    tab: 'friends',
    setTab: jest.fn(),
    search: '',
    setSearch: jest.fn(),
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
  expect(option).toHaveClass('bg-muted/50');
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

it('shows selected member bots by name only and uses a compact list typography', () => {
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
      onToggle={jest.fn()}
    />,
  );

  expect(screen.getByTestId('group-participant-selected').querySelector('span.flex.h-5')).toBeNull();
  expect(screen.getByText('Selected Bot').parentElement).toHaveClass('rounded-md', 'py-0.5');
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
