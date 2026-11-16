/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import { useGroupSessions } from '@/pages/Workspace/hooks/useGroupSessions';
import { useGroupChatHeaderPanels } from '@/pages/Workspace/hooks/useGroupChatHeaderPanels';
import { groupService } from '@/services/workspace/groupService';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { SessionView } from '@/domain/collaboration';
import type { PanelHandle } from '@tc-chat/core';

const selectionChanges: (string | null)[] = [];
const mockStarted = jest.fn();
const mockCancelled = jest.fn();
// Observe bootstrap lifetimes at the real group selection / header integration seam.
jest.mock('@/hooks/useSessionTaskPanelBootstrap', () => ({
  useSessionTaskPanelBootstrap: (options: { sessionId: string | null }) => {
    require('react').useEffect(() => {
      if (!options.sessionId) return;
      mockStarted(options.sessionId);
      return () => mockCancelled(options.sessionId);
    }, [options.sessionId]);
    return { status: 'idle' };
  },
}));
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/sessionService');
jest.mock('@/hooks/useSessionTaskPanelTab', () => ({ useSessionTaskPanelTab: () => null }));
jest.mock('@/services/tasks/panel/taskPanelHistoryAdapter', () => ({ createGroupTaskHistoryReader: () => null }));

const fixture = (gid: string, id = `${gid}-s1`): SessionView => ({
  sessionId: id, groupId: gid, title: id, kind: 'chat', status: 'running',
  participants: [], createdAt: 1, lastMessageAt: 1, favorite: false,
});
const panelRef = { current: null as PanelHandle | null };
beforeEach(() => {
  jest.clearAllMocks();
  selectionChanges.length = 0;
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({ activeIdentityId: 'me', selectedGroupId: 'g1' });
  (sessionService.getVisibleSessions as jest.Mock).mockImplementation(xs => xs);
  (sessionService.getSessionDetail as jest.Mock).mockResolvedValue({ ok: false, error: { friendlyMessage: 'missing' } });
});

function useGroupPanel() {
  const groupId = useWorkspaceStore(s => s.selectedGroupId);
  const sessions = useGroupSessions(groupId, ['g1', 'g2']);
  const selectedId = sessions.selectedSession?.sessionId ?? null;
  useEffect(() => { selectionChanges.push(selectedId); }, [selectedId]);
  useGroupChatHeaderPanels({
    session: sessions.selectedSession, taskRecoveryReady: sessions.isSessionSelectionReady,
    userId: 'viewer', messages: [], panelRef, toggleFavorite: sessions.toggleFavorite,
  });
  return sessions;
}

it.each(['present', 'removed', 'failed'] as const)(
  'waits for the selected-group refresh before task recovery (%s)', async outcome => {
    let resolveRefresh!: (value: unknown) => void;
    let g2Calls = 0;
    (groupService.loadGroupSessionsOrBcs as jest.Mock).mockImplementation(async (gid: string) => {
      if (gid === 'g2' && ++g2Calls > 1) return new Promise(resolve => { resolveRefresh = resolve; });
      return { ok: true, data: [fixture(gid)] };
    });
    const { result } = renderHook(useGroupPanel);
    await waitFor(() => expect(result.current.sessionsByGroupId.g2).toHaveLength(1));
    await waitFor(() => expect(mockStarted).toHaveBeenCalledWith('g1-s1'));
    mockStarted.mockClear(); mockCancelled.mockClear();
    selectionChanges.length = 0;
    act(() => result.current.openSession('g2', 'g2-s1'));
    // The cached selection must never start recovery before invalidation/refresh.
    expect(mockStarted).not.toHaveBeenCalled();
    // Public session selection/cache behavior is unchanged; only task recovery waits.
    expect(selectionChanges).toEqual(['g2-s1', null]);
    expect(useWorkspaceStore.getState().selectedSessionId).toBe('g2-s1');
    expect(g2Calls).toBe(2);
    // Ordinary session details still load while task recovery is gated.
    expect(sessionService.getSessionDetail).toHaveBeenCalledWith('g2-s1');
    expect(result.current.isSessionSelectionReady).toBe(false);
    expect(mockCancelled).toHaveBeenCalledWith('g1-s1');
    await act(async () => resolveRefresh(outcome === 'failed'
      ? { ok: false, error: { friendlyMessage: 'load failed' } }
      : { ok: true, data: outcome === 'present' ? [fixture('g2')] : [] }));
    expect(mockStarted.mock.calls).toEqual(outcome === 'present' ? [['g2-s1']] : []);
    expect(mockCancelled).not.toHaveBeenCalledWith('g2-s1');
    expect(selectionChanges).toEqual(outcome === 'present' ? ['g2-s1', null, 'g2-s1'] : ['g2-s1', null]);
  },
);

it('keeps normal same-group switching and cancels recovery on leaving the group', async () => {
  (groupService.loadGroupSessionsOrBcs as jest.Mock).mockImplementation(async (gid: string) => ({
    ok: true, data: [fixture(gid), fixture(gid, `${gid}-s2`)],
  }));
  const { result } = renderHook(useGroupPanel);
  await waitFor(() => expect(mockStarted).toHaveBeenCalledWith('g1-s1'));
  mockStarted.mockClear(); mockCancelled.mockClear();
  const calls = (groupService.loadGroupSessionsOrBcs as jest.Mock).mock.calls.length;
  act(() => result.current.openSession('g1', 'g1-s2'));
  expect(mockStarted.mock.calls).toEqual([['g1-s2']]);
  expect(mockCancelled).toHaveBeenCalledWith('g1-s1');
  expect((groupService.loadGroupSessionsOrBcs as jest.Mock).mock.calls).toHaveLength(calls);
  act(() => useWorkspaceStore.getState().selectGroup(null));
  expect(mockCancelled).toHaveBeenCalledWith('g1-s2');
});

it('does not recover from an old identity response while the new identity is loading', async () => {
  let resolveOld!: (value: unknown) => void;
  let resolveNew!: (value: unknown) => void;
  let g2Calls = 0;
  (groupService.loadGroupSessionsOrBcs as jest.Mock).mockImplementation(async (gid: string, identity: string) => {
    if (identity === 'next') return new Promise(resolve => {
      if (gid === 'g2') resolveNew = resolve;
      else resolve({ ok: true, data: [] });
    });
    if (gid === 'g2' && ++g2Calls > 1) return new Promise(resolve => { resolveOld = resolve; });
    return { ok: true, data: [fixture(gid)] };
  });
  const { result } = renderHook(useGroupPanel);
  await waitFor(() => expect(result.current.sessionsByGroupId.g2).toHaveLength(1));
  mockStarted.mockClear();
  act(() => result.current.openSession('g2', 'g2-s1'));
  act(() => useWorkspaceStore.setState({ activeIdentityId: 'next' }));
  await act(async () => resolveOld({ ok: true, data: [fixture('g2')] }));
  expect(mockStarted).not.toHaveBeenCalled();
  await act(async () => resolveNew({ ok: true, data: [fixture('g2')] }));
  expect(mockStarted.mock.calls).toEqual([['g2-s1']]);
});
