/** @jest-environment jsdom */

import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import { ChatEngineVersionNotice } from '@/pages/Workspace/Chat/components/ChatEngineVersionNotice';
import { expect, jest, test } from '@jest/globals';
import { fireEvent, render, screen } from '@testing-library/react';

const observation = {
  detection: { status: 'known' as const },
  current: { raw_version: '1.2.0' },
  release_target: { status: 'available' as const, release_id: 'REL-2' },
  comparison: 'older' as const,
  restart_hint: 'recommended' as const,
  context: { bot_id: 'bot-1', stage: 'draft', checked_at: '2026-10-20T00:00:00Z' },
};

test('renders only a confirmed update and scopes dismissal through its owner', () => {
  const onOpen = jest.fn();
  const onDismiss = jest.fn();
  const { rerender } = render(
    <ChatEngineVersionNotice state={{ kind: 'ready', observation }} onOpen={onOpen} onDismiss={onDismiss} />,
  );
  fireEvent.click(screen.getByRole('button', { name: '引擎有更新' }));
  fireEvent.click(screen.getByRole('button', { name: '关闭当前会话的引擎更新提示' }));
  expect(onOpen).toHaveBeenCalledTimes(1);
  expect(onDismiss).toHaveBeenCalledTimes(1);

  rerender(
    <ChatEngineVersionNotice
      state={
        {
          kind: 'ready',
          observation: { ...observation, detection: { status: 'read_failed' }, comparison: 'unknown' },
        } as BotEngineVersionState
      }
      onOpen={onOpen}
      onDismiss={onDismiss}
    />,
  );
  expect(screen.queryByTestId('chat-engine-version-notice')).toBeNull();
});


test.each([
  { kind: 'idle' }, { kind: 'loading' }, { kind: 'not_applicable' },
  { kind: 'error', message: 'Not found' }, { kind: 'select_instance', instances: [] },
  { kind: 'ready', observation: { ...observation, comparison: 'same', restart_hint: 'none' } },
  { kind: 'ready', observation: { ...observation, comparison: 'unknown' } },
])('does not prompt for an unconfirmed upgrade: %j', (state) => {
  const { container } = render(<ChatEngineVersionNotice state={state as BotEngineVersionState} onOpen={jest.fn()} onDismiss={jest.fn()} />);
  expect(container.childElementCount).toBe(0);
});
