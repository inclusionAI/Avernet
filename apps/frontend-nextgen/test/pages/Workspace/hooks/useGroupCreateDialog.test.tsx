/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { useGroupCreateDialog } from '@/pages/Workspace/hooks/useGroupCreateDialog';
import { useGroupWorkspace } from '@/pages/Workspace/hooks/useGroupWorkspace';
import { useOpenDefaultGroupSession } from '@/pages/Workspace/hooks/useOpenDefaultGroupSession';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/groupService');

const groups = jest.mocked(groupService);
const userIdentity = { id: 'me', kind: 'user' as const, displayName: '我', online: true };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const createdGroup: GroupView = {
  groupId: 'g-created',
  name: '新协作群',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 1,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
  initialSessionId: 's-created',
  initialRun: {
    runId: 'run-created',
    botUuid: 'bot-driver',
    activityKind: 'group_bootstrap',
    state: 'running',
    startedAt: '2026-09-16T00:00:00Z',
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({ activeIdentityId: 'me', identities: [userIdentity] });
  groups.getVisibleGroups.mockImplementation((items: GroupView[]) => items);
  groups.loadGroupParticipants.mockResolvedValue(null);
  groups.canManageGroup.mockReturnValue({ allowed: true });
  groups.canDissolveGroup.mockReturnValue({ allowed: true });
});

afterEach(() => {
  jest.useRealTimers();
});

it('创建成功后先刷新群列表，再选中新群并打开响应中的初始会话', async () => {
  const refresh = deferred<void>();
  const { result } = renderHook(() =>
    useGroupCreateDialog({
      refreshGroups: () => refresh.promise,
      selectGroup: (groupId) => useWorkspaceStore.getState().selectGroup(groupId),
      openSessionForGroup: async (groupId, preferredSessionId) => {
        const store = useWorkspaceStore.getState();
        if (store.selectedGroupId !== groupId) store.selectGroup(groupId);
        useWorkspaceStore.getState().selectSession(preferredSessionId ?? null);
      },
    }),
  );

  let completion!: Promise<void>;
  act(() => {
    completion = result.current.handleCreated(createdGroup);
  });

  await act(async () => {
    await Promise.resolve();
  });

  expect(useWorkspaceStore.getState().selectedGroupId).toBeNull();
  expect(useWorkspaceStore.getState().selectedSessionId).toBeNull();
  expect(useWorkspaceStore.getState().pendingGroupBootstrap).toEqual({
    groupId: 'g-created',
    sessionId: 's-created',
    run: createdGroup.initialRun,
  });

  await act(async () => {
    refresh.resolve();
    await completion;
  });

  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g-created');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s-created');
});

function useCreationWorkspace() {
  const workspace = useGroupWorkspace();
  const dialog = useGroupCreateDialog({
    refreshGroups: workspace.refreshGroups,
    selectGroup: workspace.onSelectGroup,
    openSessionForGroup: useOpenDefaultGroupSession(),
  });
  return { workspace, dialog };
}

it.each(['direct', 'session_only'] as const)(
  '用户在 %s 筛选下创建群后，等待固定群列表加载再打开默认会话',
  async (membership) => {
    useWorkspaceStore.setState({ membership });
    groups.loadGroups.mockResolvedValue({ ok: true, data: [] });
    const { result } = renderHook(useCreationWorkspace);
    await waitFor(() => expect(result.current.workspace.isLoadingGroups).toBe(false));
    if (membership === 'session_only') {
      act(() => useWorkspaceStore.getState().selectGroup('g-temporary'));
    }

    const refresh = deferred<Awaited<ReturnType<typeof groupService.loadGroups>>>();
    groups.loadGroups.mockImplementation(async (...[, opts]: Parameters<typeof groupService.loadGroups>) =>
      opts?.membership === 'direct' ? refresh.promise : { ok: true, data: [] },
    );
    let completion!: Promise<void>;
    act(() => {
      completion = result.current.dialog.handleCreated(createdGroup);
    });

    expect(useWorkspaceStore.getState().membership).toBe('direct');
    expect(groups.loadGroups).toHaveBeenLastCalledWith(userIdentity, { membership: 'direct' });
    expect(useWorkspaceStore.getState().selectedGroupId).toBeNull();
    expect(useWorkspaceStore.getState().selectedSessionId).toBeNull();

    await act(async () => {
      refresh.resolve({ ok: true, data: [createdGroup] });
      await completion;
    });
    expect(result.current.workspace.selectedGroup?.groupId).toBe('g-created');
    expect(result.current.workspace.groups.map((group) => group.groupId)).toContain('g-created');
    expect(useWorkspaceStore.getState().selectedSessionId).toBe('s-created');
    expect(useWorkspaceStore.getState().expandedGroupIds['g-created']).toBe(true);
    expect(useWorkspaceStore.getState().membership).toBe('direct');
  },
);

it('创建群后忽略旧临时会话筛选的迟到响应，保持新群及默认会话选中', async () => {
  useWorkspaceStore.setState({ membership: 'session_only' });
  const staleList = deferred<Awaited<ReturnType<typeof groupService.loadGroups>>>();
  groups.loadGroups.mockImplementation(async (...[, opts]: Parameters<typeof groupService.loadGroups>) =>
    opts?.membership === 'session_only' ? staleList.promise : { ok: true, data: [createdGroup] },
  );
  const { result } = renderHook(useCreationWorkspace);
  let completion!: Promise<void>;
  act(() => {
    completion = result.current.dialog.handleCreated(createdGroup);
  });
  expect(useWorkspaceStore.getState().membership).toBe('direct');
  await act(async () => {
    await completion;
  });

  await act(async () => {
    staleList.resolve({ ok: true, data: [] });
  });
  expect(result.current.workspace.selectedGroup?.groupId).toBe('g-created');
  expect(useWorkspaceStore.getState().membership).toBe('direct');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s-created');
});

it.each(['direct', 'session_only'] as const)(
  '在 %s 筛选下创建群时清除旧搜索，并忽略搜索的迟到响应',
  async (membership) => {
    jest.useFakeTimers();
    useWorkspaceStore.setState({ membership });
    const staleSearch = deferred<Awaited<ReturnType<typeof groupService.loadGroups>>>();
    groups.loadGroups.mockImplementation(async (...[, opts]: Parameters<typeof groupService.loadGroups>) =>
      opts?.q ? staleSearch.promise : { ok: true, data: [createdGroup] },
    );
    const { result } = renderHook(useCreationWorkspace);
    await act(async () => { await Promise.resolve(); });
    act(() => result.current.workspace.setGroupSearchText('不匹配新群的搜索词'));
    await act(async () => { jest.advanceTimersByTime(300); });
    expect(groups.loadGroups).toHaveBeenLastCalledWith(userIdentity, {
      membership, q: '不匹配新群的搜索词',
    });

    let completion!: Promise<void>;
    act(() => { completion = result.current.dialog.handleCreated(createdGroup); });
    const searchAfterCreation = useWorkspaceStore.getState().groupSearchText;
    await act(async () => {
      await completion;
    });
    await act(async () => {
      staleSearch.resolve({ ok: true, data: [] });
      jest.advanceTimersByTime(300);
    });
    await act(async () => { jest.runOnlyPendingTimers(); });
    expect(searchAfterCreation).toBe('');
    expect(result.current.workspace.selectedGroup?.groupId).toBe('g-created');
    expect(useWorkspaceStore.getState().membership).toBe('direct');
    expect(useWorkspaceStore.getState().selectedSessionId).toBe('s-created');
  },
);
