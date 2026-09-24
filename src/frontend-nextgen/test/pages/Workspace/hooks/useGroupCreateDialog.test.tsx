/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { useGroupCreateDialog } from '@/pages/Workspace/hooks/useGroupCreateDialog';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

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
  useWorkspaceStore.getState().resetWorkspace();
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
