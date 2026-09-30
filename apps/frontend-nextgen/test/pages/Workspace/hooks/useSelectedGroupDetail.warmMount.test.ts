/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { useGroupWorkspace } from '@/pages/Workspace/hooks/useGroupWorkspace';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// auto-mock（不带 factory），避免 hoisted factory 内引用 jest.fn() 触发 @jest/globals 的 TDZ。
jest.mock('@/services/workspace/groupService');

const gs = groupService as unknown as Record<string, jest.Mock<any>>;

const userIdentity = { id: 'u1', kind: 'user' as const, displayName: '我', online: true };

/** 模拟暖重挂载：store 已记住 selectedGroupId/membership，而列表 hook 局部 state 为空。 */
function warmStore() {
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({
    identities: [userIdentity],
    activeIdentityId: 'u1',
    view: 'group',
    selectedGroupId: 'g1',
    membership: 'direct',
    expandedGroupIds: { g1: true },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const groupG1 = {
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
} as unknown as GroupView;

beforeEach(() => {
  jest.clearAllMocks();
  gs.getVisibleGroups.mockImplementation((xs: any[]) => xs);
  gs.canManageGroup.mockImplementation(() => ({ allowed: true }));
  gs.canDissolveGroup.mockImplementation(() => ({ allowed: true }));
});

describe('useSelectedGroupDetail warm remount（经 useGroupWorkspace 真实组合）', () => {
  it('列表请求进行中不翻转 membership；稳定后深链纠正仍生效', async () => {
    warmStore();
    const pending = deferred<{ ok: true; data: GroupView[] }>();
    gs.loadGroups.mockReturnValue(pending.promise);

    renderHook(() => useGroupWorkspace());
    // 冲刷挂载 effect：首个 flush 中 isGroupsLoading 闭包仍为 false（loadGroups 同步置位在同一 flush）。
    await act(async () => {
      await Promise.resolve();
    });

    // 请求 in-flight 期间，记住的 direct 视角不得被误翻转为 session_only。
    expect(useWorkspaceStore.getState().membership).toBe('direct');

    // 列表稳定后 g1 不在列表中 → 深链纠正应把 membership 切到 session_only。
    await act(async () => {
      pending.resolve({ ok: true, data: [] });
      await Promise.resolve();
    });
    await waitFor(() => expect(useWorkspaceStore.getState().membership).toBe('session_only'));
  });

  it('列表包含选中群时 membership 保持 direct', async () => {
    warmStore();
    gs.loadGroups.mockResolvedValue({ ok: true, data: [groupG1] });

    renderHook(() => useGroupWorkspace());
    await waitFor(() => expect(useWorkspaceStore.getState().isGroupsLoading).toBe(false));
    // 等待恢复标记解除的 setTimeout(0) 宏任务与后续重渲染。
    await act(async () => {
      await new Promise<void>((r) => {
        setTimeout(r, 10);
      });
    });

    expect(useWorkspaceStore.getState().membership).toBe('direct');
  });
});
