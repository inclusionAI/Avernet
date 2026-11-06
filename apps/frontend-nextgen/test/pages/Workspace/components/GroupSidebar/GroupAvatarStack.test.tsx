/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { GroupAvatarStack } from '@/pages/Workspace/components/GroupSidebar/GroupAvatarStack';
import { getDisplayParticipants, getParticipantDisplayCount } from '@/domain/collaboration/participantPresentation';
import { mapGroupListItem } from '@/services/workspace/mappers';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';

test('summary avatars render in stored order with the remaining count', () => {
  render(<GroupAvatarStack participants={[
    { actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: '/alpha.png' },
    { actorId: 'b2', kind: 'bot', name: 'Beta' },
  ]} totalCount={4} size={20} />);
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', '/alpha.png');
  expect(screen.getByRole('img', { name: 'Beta' })).toHaveAttribute('src', generateBotAvatar('b2'));
  expect(screen.getByText('2+')).toBeInTheDocument();
  expect(screen.getByLabelText('成员 4 人')).toBeInTheDocument();
});

const fourSummaries = [
  { actor_id: 'b1', actor_kind: 'bot' as const, name: 'Bot 1', avatar_url: '/b1.png' },
  { actor_id: 'b2', actor_kind: 'bot' as const, name: 'Bot 2', avatar_url: null },
  { actor_id: 'h1', actor_kind: 'human' as const, name: null, avatar_url: ' /h1.png ' },
  { actor_id: 'h2', actor_kind: 'human' as const, name: '人二', avatar_url: '   ' },
];

function mapWithCount(participant_count: number | undefined) {
  return mapGroupListItem({
    group_id: 'g-chain',
    kind: 'normal' as const,
    status: 'active' as const,
    visibility: 'private' as const,
    originator_actor_id: 'u1',
    participant_count,
    driver_bot_uuid: 'b1',
    strategy: 'chat' as const,
    created_at: 1,
    updated_at: 2,
    participants: fourSummaries,
  });
}

function renderMappedStack(participant_count: number | undefined) {
  const group = mapWithCount(participant_count);
  const displayParticipants = getDisplayParticipants(group);
  const totalCount = getParticipantDisplayCount(group.participantCount, displayParticipants);
  render(<GroupAvatarStack participants={displayParticipants} totalCount={totalCount} size={20} />);
}

test('a group list DTO without a count falls back to its four summary members end to end', () => {
  renderMappedStack(undefined);
  expect(screen.getByLabelText('成员 4 人')).toBeInTheDocument();
  expect(screen.getByText('2+')).toBeInTheDocument();
  // 摘要顺序即服务端顺序：只取前两名成员头像（含 human 根相对头像）。
  expect(screen.getByRole('img', { name: 'Bot 1' })).toHaveAttribute('src', '/b1.png');
  expect(screen.getByRole('img', { name: 'Bot 2' })).toHaveAttribute('src', generateBotAvatar('b2'));
});

test('an explicit zero participant count stays zero instead of falling back to the summary size', () => {
  renderMappedStack(0);
  expect(screen.getByLabelText('成员 0 人')).toBeInTheDocument();
  expect(screen.queryByText('2+')).not.toBeInTheDocument();
});
