/** @jest-environment jsdom */
import { useGroupManagement } from '@/pages/Workspace/hooks/useGroupManagement';
import { channelBindingService } from '@/services/workspace/channelBindingService';
import { groupMemberService } from '@/services/workspace/groupMemberService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/channelBindingService');
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/groupMemberService');
jest.mock('@/services/workspace/invitationService');
jest.mock('sonner', () => ({ toast: { error: () => undefined, success: () => undefined } }));

const channelBinding = channelBindingService as unknown as Record<string, jest.Mock>;

it('does not load dingtalk binding when advanced config is disabled', () => {
  renderHook(() => useGroupManagement('group-1', jest.fn(), false));

  expect(channelBinding.loadGroupDingTalkBinding).not.toHaveBeenCalled();
});

it('loads dingtalk binding when advanced config is enabled', async () => {
  channelBinding.loadGroupDingTalkBinding.mockResolvedValue({ ok: true, data: null });

  renderHook(() => useGroupManagement('group-1', jest.fn(), true));

  await waitFor(() => expect(channelBinding.loadGroupDingTalkBinding).toHaveBeenCalledWith('group-1'));
});

it('退出群成功后清掉该群选中：避免列表刷新纠正把筛选误切到「仅参与临时会话」', async () => {
  // 退出后群对当前身份不再可见；若选中仍指向它，refreshGroups 后 useSelectedGroupDetail
  // 会把「选中群不在 direct 列表」误判为深链临时视角场景 → membership 被错切 session_only。
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.getState().selectGroup('group-1');
  (groupMemberService.leaveGroup as unknown as jest.Mock).mockResolvedValue({ ok: true, data: null });

  const { result } = renderHook(() => useGroupManagement('group-1', jest.fn(), false));
  let ok = false;
  await act(async () => {
    ok = await result.current.leaveGroup('me');
  });

  expect(ok).toBe(true);
  expect(useWorkspaceStore.getState().selectedGroupId).toBeNull();
  expect(useWorkspaceStore.getState().membership).toBe('direct');
});

it('退出失败不清选中', async () => {
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.getState().selectGroup('group-1');
  (groupMemberService.leaveGroup as unknown as jest.Mock).mockResolvedValue({
    ok: false,
    error: { code: 'X', friendlyMessage: 'fail', canRetry: false },
  });

  const { result } = renderHook(() => useGroupManagement('group-1', jest.fn(), false));
  let ok = true;
  await act(async () => {
    ok = await result.current.leaveGroup('me');
  });

  expect(ok).toBe(false);
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('group-1');
});
