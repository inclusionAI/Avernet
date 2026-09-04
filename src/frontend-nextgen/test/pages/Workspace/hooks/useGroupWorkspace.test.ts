/** @jest-environment jsdom */
import { useGroupWorkspace } from '@/pages/Workspace/hooks/useGroupWorkspace';
import { groupService } from '@/services/workspace/groupService';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// auto-mock（不带 factory），避免 hoisted factory 内引用 jest.fn() 触发 @jest/globals 的 TDZ。
// auto-mock 会把 groupService 上的方法替换为 jest.fn()，下面通过 gs 强取并在 beforeEach 设默认实现。
jest.mock('@/services/workspace/groupService');

const gs = groupService as unknown as Record<string, jest.Mock<any>>;

const botIdentity = { id: 'bot-1', kind: 'bot' as const, displayName: 'B1', online: true };

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({ activeIdentityId: 'bot-1', identities: [botIdentity] });
  // 默认实现：getVisibleGroups 透传；权限校验一律放行。
  gs.getVisibleGroups.mockImplementation((xs: any[]) => xs);
  gs.canManageGroup.mockImplementation(() => ({ allowed: true }));
  gs.canDissolveGroup.mockImplementation(() => ({ allowed: true }));
  gs.dissolveGroup.mockResolvedValue({ ok: true, data: null });
});

describe('useGroupWorkspace', () => {
  it('searches group list with 300ms debounce per identity', async () => {
    jest.useFakeTimers();
    gs.loadGroups.mockResolvedValue({
      ok: true,
      data: [
        {
          groupId: 'g1',
          name: 'Alpha',
          kind: 'free_chat',
          status: 'active',
          participants: [],
          sessions: [],
          lastMessageAt: 1,
          createdAt: 1,
          isPublic: false,
          deliveryPolicy: 'send_to_driver',
        },
      ],
    });
    const { result } = renderHook(() => useGroupWorkspace());
    await act(async () => {
      await Promise.resolve();
    });
    expect(gs.loadGroups).toHaveBeenCalledWith(botIdentity, { membership: 'direct' });

    act(() => result.current.setGroupSearchText('new'));
    act(() => result.current.setGroupSearchText('newer'));
    await act(async () => {
      jest.advanceTimersByTime(350);
      await Promise.resolve();
    });
    expect(gs.loadGroups).toHaveBeenCalledTimes(2);
    expect(gs.loadGroups.mock.calls[1][1]).toEqual({ q: 'newer', membership: 'direct' });
    jest.useRealTimers();
  });

  it('onSelectGroup loads detail then selects group', async () => {
    gs.loadGroups.mockResolvedValue({ ok: true, data: [] });
    gs.loadGroupDetailOrBcs.mockResolvedValue({
      ok: true,
      data: {
        groupId: 'g9',
        name: 'X',
        kind: 'free_chat',
        status: 'active',
        participants: [],
        sessions: [],
      } as any,
    });
    const { result } = renderHook(() => useGroupWorkspace());
    await waitFor(() => expect(gs.loadGroups).toHaveBeenCalled());
    gs.loadGroupDetailOrBcs.mockClear();

    await act(async () => {
      await result.current.onSelectGroup('g9');
    });
    expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledWith('g9', 'bot-1');
    expect(useWorkspaceStore.getState().selectedGroupId).toBe('g9');
  });

  it('direct selectedGroupId still loads group detail so chat pane can render', async () => {
    gs.loadGroups.mockResolvedValue({ ok: true, data: [] });
    gs.loadGroupDetailOrBcs.mockResolvedValue({
      ok: true,
      data: {
        groupId: 'g9',
        name: 'X',
        kind: 'free_chat',
        status: 'active',
        participants: [],
        sessions: [],
      } as any,
    });
    const { result } = renderHook(() => useGroupWorkspace());
    act(() => {
      useWorkspaceStore.getState().selectGroup('g9');
    });
    await waitFor(() => expect(result.current.selectedGroup?.groupId).toBe('g9'));
    expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledWith('g9', 'bot-1');
    expect(result.current.groups).toEqual([]);
  });

  it('missing group under direct filter auto switches role filter to session_only', async () => {
    gs.loadGroups.mockResolvedValue({ ok: true, data: [] });
    gs.loadGroupDetailOrBcs.mockResolvedValue({
      ok: true,
      data: {
        groupId: 'g9',
        name: 'X',
        kind: 'free_chat',
        status: 'active',
        participants: [],
        sessions: [],
      } as any,
    });
    renderHook(() => useGroupWorkspace());
    await waitFor(() => expect(gs.loadGroups).toHaveBeenCalled());
    act(() => {
      useWorkspaceStore.getState().selectGroup('g9');
    });
    await waitFor(() => expect(useWorkspaceStore.getState().membership).toBe('session_only'));
  });

  it('switch identity triggers loadGroups for new identity and clears selection', async () => {
    gs.loadGroups.mockResolvedValue({ ok: true, data: [] });
    renderHook(() => useGroupWorkspace());
    await waitFor(() => expect(gs.loadGroups).toHaveBeenCalled());

    act(() => {
      useWorkspaceStore.getState().setActiveIdentity('bot-2');
    });
    await waitFor(() => {
      const last = gs.loadGroups.mock.calls.at(-1)?.[0] as { id: string } | undefined;
      expect(last?.id).toBe('bot-2');
    });
    expect(useWorkspaceStore.getState().selectedGroupId).toBeNull();
  });

  it('switching membership triggers loadGroups with that membership value', async () => {
    gs.loadGroups.mockResolvedValue({ ok: true, data: [] });
    const { result } = renderHook(() => useGroupWorkspace());
    await waitFor(() => expect(gs.loadGroups).toHaveBeenCalled());

    act(() => {
      useWorkspaceStore.getState().setMembership('session_only');
    });
    await waitFor(() => {
      const last = gs.loadGroups.mock.calls.at(-1);
      expect(last?.[1]).toMatchObject({ membership: 'session_only' });
    });
    expect(result.current.membership).toBe('session_only');
  });

  it('dissolveGroup shows toast on failure and redirects selection on success', async () => {
    const { toast } = jest.requireActual('sonner') as typeof import('sonner');
    const toastSuccess = jest.spyOn(toast, 'success').mockImplementation(() => 'ok' as any);
    const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
    gs.loadGroups.mockResolvedValue({ ok: true, data: [] });
    gs.dissolveGroup.mockResolvedValueOnce({ ok: true, data: null });
    const { result } = renderHook(() => useGroupWorkspace());
    act(() => {
      useWorkspaceStore.getState().selectGroup('g1');
    });
    await act(async () => {
      await result.current.dissolveGroup('g1');
    });
    expect(toastSuccess).toHaveBeenCalled();
    expect(useWorkspaceStore.getState().selectedGroupId).toBeNull();

    gs.dissolveGroup.mockResolvedValueOnce({
      ok: false,
      error: {
        code: 'GROUP_DISSOLVE_FAILED',
        friendlyMessage: '解散协作群失败，请稍后重试。',
        canRetry: false,
      },
    });
    await act(async () => {
      await result.current.dissolveGroup('g1');
    });
    expect(toastError).toHaveBeenCalledWith('解散协作群失败，请稍后重试。');
    toastSuccess.mockRestore();
    toastError.mockRestore();
  });

  it('mutes loadGroups-failure toast when unauthenticated under oauth-provider', async () => {
    const { toast } = jest.requireActual('sonner') as typeof import('sonner');
    const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    // externalAuthStore.status 默认 'unknown'（非 authenticated）→ shouldMuteNonAuthedToast() 为 true。
    gs.loadGroups.mockResolvedValue({
      ok: false,
      error: { code: 'GROUPS_LOAD_FAILED', friendlyMessage: '加载协作群失败，请稍后重试。', canRetry: true },
    });
    renderHook(() => useGroupWorkspace());
    await waitFor(() => expect(gs.loadGroups).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    expect(toastError).not.toHaveBeenCalled();
    toastError.mockRestore();
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
  });
});
