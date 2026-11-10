/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';

test('failed source falls back by ID and a new URL is retried', () => {
  const { rerender } = render(<BotDisplayAvatar botId="b1" name="Alpha" avatarUrl="/old.png" />);
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', generateBotAvatar('b1'));
  rerender(<BotDisplayAvatar botId="b1" name="Renamed" avatarUrl="/new.png" />);
  expect(screen.getByRole('img', { name: 'Renamed' })).toHaveAttribute('src', '/new.png');
});
test('two image failures terminate in a local icon', () => {
  const { container } = render(<BotDisplayAvatar botId="b1" name="Alpha" avatarUrl="/bad.png" />);
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByRole('img', { name: 'Alpha' })).toBeInTheDocument();
});
test('renaming without a URL keeps the ID-derived default source', () => {
  const { rerender } = render(<BotDisplayAvatar botId="b1" name="Alpha" />);
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', generateBotAvatar('b1'));
  rerender(<BotDisplayAvatar botId="b1" name="Renamed" />);
  expect(screen.getByRole('img', { name: 'Renamed' })).toHaveAttribute('src', generateBotAvatar('b1'));
});
test('switching away from a failed bot retries with the new bot URL', () => {
  const { rerender } = render(<BotDisplayAvatar botId="b1" name="Alpha" avatarUrl="/b1.png" />);
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', generateBotAvatar('b1'));
  rerender(<BotDisplayAvatar botId="b2" name="Beta" avatarUrl="/b2.png" />);
  expect(screen.getByRole('img', { name: 'Beta' })).toHaveAttribute('src', '/b2.png');
});

test('a collaboration ID uses the same default avatar as its Bot management ID', () => {
  render(<BotDisplayAvatar botId="b1:owner" name="Alpha" />);
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', generateBotAvatar('b1'));
});
