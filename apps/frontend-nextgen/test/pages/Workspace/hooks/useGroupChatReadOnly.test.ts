/** @jest-environment jsdom */
import { useGroupChatProvider } from '@/pages/Workspace/hooks/useGroupChatProvider';
import { useMessageAreaSkeleton } from '@/pages/Workspace/hooks/useMessageAreaSkeleton';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { SessionView } from '@/domain/collaboration';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { renderHook } from '@testing-library/react';

jest.mock('@tc-chat/adapters', () => ({ GroupChatProvider: class {} }));
const session: SessionView = {
  sessionId: 's1', groupId: 'g1', title: 'test', kind: 'chat', status: 'running',
  participants: [{ actorId: 'human_1', kind: 'human', name: 'Me', role: 'member', mode: 'absent' }],
  lastMessageAt: 0, createdAt: 0, favorite: false,
};
beforeEach(() => useWorkspaceStore.getState().resetWorkspace());

it('gates the authenticated Human regardless of active Bot view, then resumes on join', () => {
  const { result, rerender, unmount } = renderHook(
    ({ current }) => useGroupChatProvider(current, 'bot_2', '1'),
    { initialProps: { current: session } },
  );
  const absent = result.current;
  expect(absent?.liveEnabled).toBe(false);
  rerender({ current: { ...session, participants: [{ ...session.participants[0], mode: 'present' }] } });
  const present = result.current;
  expect(present?.liveEnabled).toBe(true);
  expect(absent?.liveEnabled).toBe(false);
  rerender({ current: session });
  expect(present?.liveEnabled).toBe(false);
  expect(result.current?.liveEnabled).toBe(false);
  unmount();
});

it('waits for member details but does not treat a missing Human as absent', () => {
  const { result, rerender } = renderHook(
    ({ current }) => useGroupChatProvider(current, 'human_1', '1'),
    { initialProps: { current: { ...session, participants: [] } as SessionView } },
  );
  expect(result.current?.liveEnabled).toBe(false);
  rerender({ current: { ...session, participants: [{ actorId: 'bot_2', kind: 'bot', name: 'Bot', role: 'member', mode: 'auto' }] } });
  expect(result.current?.liveEnabled).toBe(true);
});

it.each([{ messages: [] }, { messages: [{ id: 'm1', role: 'user', content: 'history' }] }])(
  'shows completed HTTP history, including empty history, without a connection (%j)', ({ messages }) => {
    const { result, rerender } = renderHook(
      ({ ready }) => useMessageAreaSkeleton({ messages: messages as never, status: 'disconnected', historyOnlyReady: ready }),
      { initialProps: { ready: false } },
    );
    expect(result.current).toBe(true);
    rerender({ ready: true });
    expect(result.current).toBe(false);
  },
);
