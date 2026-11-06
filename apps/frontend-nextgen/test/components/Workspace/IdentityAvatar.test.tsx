/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { IdentityAvatar } from '@/components/Workspace/IdentitySelector/IdentitySelectorParts';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import type { Identity } from '@/services/workspace/workspaceModel';

const bot: Identity = { id: 'b1', kind: 'bot', name: 'Alpha', avatar: 'A' };
test('legacy initial becomes the same fixed Bot default', () => {
  render(<IdentityAvatar identity={bot} />);
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', generateBotAvatar('b1'));
});
test('avatar URL changes clear a previous image error', () => {
  const { rerender } = render(<IdentityAvatar identity={{ ...bot, avatar: '/old.png' }} />);
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  rerender(<IdentityAvatar identity={{ ...bot, avatar: '/new.png' }} />);
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', '/new.png');
});
test('Human uses the caller-provided avatar', () => {
  render(
    <IdentityAvatar
      identity={{ id: 'u1', kind: 'user', name: 'User', avatar: '' }}
      userAvatarUrl="https://example.test/user.png"
    />,
  );
  expect(screen.getByRole('img', { name: 'User' })).toHaveAttribute('src', 'https://example.test/user.png');
});
