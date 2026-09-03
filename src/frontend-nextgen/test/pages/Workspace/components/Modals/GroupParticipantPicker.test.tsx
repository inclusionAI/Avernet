/** @jest-environment jsdom */
import { GroupParticipantPicker } from '@/pages/Workspace/components/Modals/GroupParticipantPicker';
import type { UseGroupCollaborationPickerResult } from '@/pages/Workspace/hooks/useGroupCollaborationPicker';
import type { CollaborationBotView } from '@/services/workspace/collaborationCandidateService';
import { jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const unknownBot = {
  id: 'b2:327325',
  name: 'b2:327325',
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

  const option = screen.getByRole('button', { name: /b2:327325/ });
  expect(option).toBeDisabled();
  expect(option).toHaveClass('bg-muted/50');
  expect(screen.getByText('未知')).toBeInTheDocument();

  fireEvent.click(option);
  expect(onToggle).not.toHaveBeenCalled();
});
