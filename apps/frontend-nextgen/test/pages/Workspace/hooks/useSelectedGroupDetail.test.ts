/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { useSelectedGroupDetail } from '@/pages/Workspace/hooks/useSelectedGroupDetail';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/groupService');
const gs = groupService as unknown as Record<string, jest.Mock<any>>;

const g9Group: GroupView = {
  groupId: 'g9', name: 'G9', kind: 'free_chat', status: 'active', participants: [], sessions: [],
  lastMessageAt: 1, createdAt: 1, participantCount: 1, isPublic: false, deliveryPolicy: 'send_to_driver',
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  useWorkspaceStore.getState().resetWorkspace();
  jest.clearAllMocks();
  useWorkspaceStore.setState({ activeIdentityId: 'me', membership: 'direct' });
});

describe('useSelectedGroupDetail', () => {
  it('loads a selected group by id when it is outside the current sidebar page', async () => {
    gs.loadGroupDetailOrBcs.mockResolvedValue({ ok: true, data: g9Group });
    const { result } = renderHook(() => useSelectedGroupDetail('g9', [], false, true));

    await waitFor(() => expect(result.current.group).toEqual(g9Group));
    expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledWith('g9', undefined);
    expect(useWorkspaceStore.getState().membership).toBe('direct');
  });

  it('does not infer membership from a paginated list miss', async () => {
    gs.loadGroupDetailOrBcs.mockResolvedValue({ ok: false, error: { friendlyMessage: 'missing' } });
    renderHook(() => useSelectedGroupDetail('g9', [], false, true));
    await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalled());
    expect(useWorkspaceStore.getState().membership).toBe('direct');
  });

  it('does not load while the current identity list is still restoring', async () => {
    renderHook(() => useSelectedGroupDetail('g9', [], false, false));
    await act(async () => { await Promise.resolve(); });
    expect(gs.loadGroupDetailOrBcs).not.toHaveBeenCalled();
  });

  it('does not reload when the selected group is already listed', () => {
    renderHook(() => useSelectedGroupDetail('g9', [g9Group], false, true));
    expect(gs.loadGroupDetailOrBcs).not.toHaveBeenCalled();
  });

  it('preserves an explicitly supplied session-only filter while displaying a direct group', async () => {
    useWorkspaceStore.setState({ membership: 'session_only' });
    gs.loadGroupDetailOrBcs.mockResolvedValue({ ok: true, data: { ...g9Group, membership: 'direct' } });
    const { result } = renderHook(() => useSelectedGroupDetail('g9', [], false, true));
    await waitFor(() => expect(result.current.group?.groupId).toBe('g9'));
    expect(useWorkspaceStore.getState().membership).toBe('session_only');
  });

  it('does not repeat detail requests when unrelated sidebar rows change', async () => {
    gs.loadGroupDetailOrBcs.mockResolvedValue({ ok: true, data: g9Group });
    const { result, rerender } = renderHook(({ list }) => useSelectedGroupDetail('g9', list, false, true), {
      initialProps: { list: [] as GroupView[] },
    });
    await waitFor(() => expect(result.current.group).toEqual(g9Group));
    rerender({ list: [{ ...g9Group, groupId: 'other' }] });
    expect(result.current.group).toEqual(g9Group);
    expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledTimes(1);
  });

  it('ignores a late response after selecting another group and immediately hides old detail', async () => {
    const pending = deferred<{ ok: true; data: GroupView }>();
    gs.loadGroupDetailOrBcs.mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce({ ok: true, data: { ...g9Group, groupId: 'g10' } });
    const { result, rerender } = renderHook(({ id }) => useSelectedGroupDetail(id, [], false, true), {
      initialProps: { id: 'g9' },
    });
    rerender({ id: 'g10' });
    await waitFor(() => expect(result.current.group?.groupId).toBe('g10'));
    await act(async () => { pending.resolve({ ok: true, data: g9Group }); });
    expect(result.current.group?.groupId).toBe('g10');
  });

  it('clears an accepted fallback immediately on identity change and rejects the old response', async () => {
    const pending = deferred<{ ok: true; data: GroupView }>();
    useWorkspaceStore.setState({ identities: [
      { id: 'me', kind: 'user', displayName: 'Me', online: true },
      { id: 'bot2', kind: 'bot', displayName: 'Bot', online: true },
    ] });
    gs.loadGroupDetailOrBcs.mockResolvedValueOnce({ ok: true, data: g9Group })
      .mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() => useSelectedGroupDetail('g9', [], false, true));
    await waitFor(() => expect(result.current.group).toEqual(g9Group));
    act(() => useWorkspaceStore.setState({ activeIdentityId: 'bot2' }));
    expect(result.current.group).toBeNull();
    expect(gs.loadGroupDetailOrBcs).toHaveBeenLastCalledWith('g9', 'bot2');
    await act(async () => { pending.resolve({ ok: true, data: { ...g9Group, name: 'Bot scope' } }); });
    expect(result.current.group?.name).toBe('Bot scope');
  });

  it('invalidates in-flight detail on identity change, even for the same group', async () => {
    const old = deferred<{ ok: true; data: GroupView }>();
    gs.loadGroupDetailOrBcs.mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce({ ok: false, error: { friendlyMessage: 'forbidden' } });
    const { result } = renderHook(() => useSelectedGroupDetail('g9', [], false, true));
    act(() => useWorkspaceStore.setState({ activeIdentityId: 'other-user' }));
    await waitFor(() => expect(result.current.error).toBe('forbidden'));
    await act(async () => { old.resolve({ ok: true, data: g9Group }); });
    expect(result.current.group).toBeNull();
    expect(result.current.error).toBe('forbidden');
  });

  it.each(['dissolved', 'wrong-group'] as const)('rejects a %s detail instead of displaying another group', async (reason) => {
    gs.loadGroupDetailOrBcs.mockResolvedValue({ ok: true, data: {
      ...g9Group, ...(reason === 'dissolved' ? { status: 'dissolved' } : { groupId: 'other' }),
    } });
    const { result } = renderHook(() => useSelectedGroupDetail('g9', [], false, true));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.group).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('supports retry after a failure without clearing group/session selection', async () => {
    useWorkspaceStore.setState({ selectedGroupId: 'g9', selectedSessionId: 's9' });
    gs.loadGroupDetailOrBcs.mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({ ok: true, data: g9Group });
    const { result } = renderHook(() => useSelectedGroupDetail('g9', [], false, true));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.group).toEqual(g9Group));
    expect(useWorkspaceStore.getState()).toMatchObject({ selectedGroupId: 'g9', selectedSessionId: 's9' });
  });

});
