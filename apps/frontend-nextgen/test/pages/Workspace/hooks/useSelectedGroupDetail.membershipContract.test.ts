/** @jest-environment jsdom */
import { useSelectedGroupDetail } from '@/pages/Workspace/hooks/useSelectedGroupDetail';
import { getGroup, listGroups } from '@/services/backendApi/collaboration/collaborationGroupController';
import { listGroupSessions } from '@/services/backendApi/collaboration/sessionController';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/backendApi/collaboration/collaborationGroupController');
jest.mock('@/services/backendApi/collaboration/sessionController');

it('obtains membership from the real viewer-scoped list, not an invented Group-detail field', async () => {
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([{ id: 'human_staff-1', kind: 'user', displayName: '我', online: true }], 'human_staff-1');
  const detail = { group_id: 'g1', version: 1, kind: 'normal' as const, status: 'active' as const,
    visibility: 'private' as const, originator_actor_id: 'bot-1',
    participants: [{ actor_id: 'bot-1', actor_kind: 'bot' as const, role: 'driver' as const, mode: 'auto' as const }],
    driver_bot_uuid: 'bot-1', human_mention_notify_mode: 'all' as const,
    collaboration: { strategy: 'chat' as const, delivery_policy: { bot_final_delivery: 'send_to_driver' as const } },
    created_at: 1, updated_at: 1 };
  jest.mocked(getGroup).mockResolvedValue({ code: 20000, data: detail });
  jest.mocked(listGroupSessions).mockResolvedValue({ code: 20000, data: { items: [], offset: 0, limit: 10, total: 0 } });
  const summary = { group_id: 'g1', version: 1, kind: 'normal' as const, status: 'active' as const,
    visibility: 'private' as const, originator_actor_id: 'bot-1', membership: 'session_only' as const,
    participant_count: 1, driver_bot_uuid: 'bot-1', strategy: 'chat' as const, human_mention_notify_mode: 'all',
    participants: [{ actor_id: 'bot-1', actor_kind: 'bot' as const, name: null, avatar_url: null }],
    created_at: 1, updated_at: 1 };
  jest.mocked(listGroups).mockResolvedValue({ code: 20000, data: { items: [summary], total: 1 } });
  const { result } = renderHook(() => useSelectedGroupDetail('g1', [], false, true));
  await waitFor(() => expect(result.current.group?.membership).toBe('session_only'));
  expect(detail).not.toHaveProperty('membership');
  expect(listGroups).toHaveBeenCalledWith(expect.objectContaining({ membership: 'session_only', kind: 'normal' }));
  expect(jest.mocked(listGroups).mock.calls[0][0]).not.toHaveProperty('view_bot_id');
});
