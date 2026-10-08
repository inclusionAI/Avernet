/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { useSessionManagement } from '@/pages/Workspace/hooks/useSessionManagement';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/workspace/sessionService');
jest.mock('@/services/workspace/invitationService');

const ss = sessionService as unknown as Record<string, jest.Mock<any>>;

const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: '会话',
  kind: 'chat',
  status: 'running',
  participants: [],
  lastMessageAt: 0,
  createdAt: 0,
  favorite: false,
};

const refreshed: SessionView = {
  ...session,
  participants: [{ actorId: 'b2', kind: 'bot', name: 'Beta', role: 'member', mode: 'auto' }],
};

beforeEach(() => {
  jest.resetAllMocks();
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession(null);
});

it('keeps the current session selected after adding a member', async () => {
  ss.addMember.mockResolvedValue({ ok: true, data: refreshed });
  const { result } = renderHook(() => useSessionManagement(session, jest.fn()));

  const ok = await act(() => result.current.addMember('b2'));

  expect(ok).toBe(true);
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s1');
});

it('keeps the current session selected after removing a member', async () => {
  ss.removeMember.mockResolvedValue({ ok: true, data: refreshed });
  const { result } = renderHook(() => useSessionManagement(session, jest.fn()));

  const ok = await act(() => result.current.removeMember('b2'));

  expect(ok).toBe(true);
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s1');
});
