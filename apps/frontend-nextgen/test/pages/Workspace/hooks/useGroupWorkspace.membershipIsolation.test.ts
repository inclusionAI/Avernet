/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { useGroupWorkspace } from '@/pages/Workspace/hooks/useGroupWorkspace';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { baseGroup } from '../components/GroupSidebar/GroupSidebar.testUtils';

jest.mock('@/services/workspace/groupService');
const human = { id: 'human_staff-1', kind: 'user' as const, displayName: '我', online: true };
const direct = { ...baseGroup, groupId: 'fixed', participantCount: 0, membership: 'direct' as const };

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([human], human.id);
  jest.mocked(groupService.getVisibleGroups).mockImplementation((groups) => groups);
  jest.mocked(groupService.canManageGroup).mockReturnValue({ allowed: false });
  jest.mocked(groupService.canDissolveGroup).mockReturnValue({ allowed: false });
  jest.mocked(groupService.loadGroupParticipants).mockResolvedValue([]);
});

it.each([true, false])('does not render old fixed groups in the new session-only category while loading or after a failure (success=%s)', async (success) => {
  let resolve!: (result: Awaited<ReturnType<typeof groupService.loadGroups>>) => void;
  const pending = new Promise<Awaited<ReturnType<typeof groupService.loadGroups>>>((r) => { resolve = r; });
  jest.mocked(groupService.loadGroups).mockResolvedValueOnce({ ok: true, data: [direct] }).mockReturnValueOnce(pending);
  const { result } = renderHook(() => useGroupWorkspace());
  await waitFor(() => expect(result.current.groups.map((group) => group.groupId)).toEqual(['fixed']));
  act(() => useWorkspaceStore.getState().setMembership('session_only'));
  expect(result.current.membership).toBe('session_only');
  expect(result.current.groups).toEqual([]);
  const temporary: GroupView = { ...direct, groupId: 'temporary', membership: 'session_only' };
  await act(async () => resolve(success ? { ok: true, data: [temporary] } : {
    ok: false, error: { code: 'FAILED', friendlyMessage: '加载失败', canRetry: true },
  }));
  expect(result.current.groups.map((group) => group.groupId)).toEqual(success ? ['temporary'] : []);
});

it('keeps the same-identity selected detail during a category reload without exposing its old row in the new category', async () => {
  useWorkspaceStore.setState({ selectedGroupId: direct.groupId, selectedSessionId: 'current-session' });
  let resolve!: (result: Awaited<ReturnType<typeof groupService.loadGroups>>) => void;
  const pending = new Promise<Awaited<ReturnType<typeof groupService.loadGroups>>>((r) => { resolve = r; });
  jest.mocked(groupService.loadGroups).mockResolvedValueOnce({ ok: true, data: [direct] }).mockReturnValueOnce(pending);
  jest.mocked(groupService.loadGroupDetailOrBcs).mockResolvedValue({ ok: true, data: direct });
  const { result } = renderHook(() => useGroupWorkspace());
  await waitFor(() => expect(result.current.selectedGroup?.groupId).toBe('fixed'));
  act(() => useWorkspaceStore.getState().setMembership('session_only'));
  expect(result.current.groups).toEqual([]);
  expect(result.current.selectedGroup?.groupId).toBe('fixed');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('current-session');
  await act(async () => resolve({ ok: true, data: [] }));
});
