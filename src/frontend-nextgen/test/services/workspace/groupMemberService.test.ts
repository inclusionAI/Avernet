/** @jest-environment node */
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { groupMemberService } from '@/services/workspace/groupMemberService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationGroupController');
jest.mock('@/services/backendApi/collaboration/sessionController');

const gc = groupController as unknown as Record<string, jest.Mock<any>>;
const sc = sessionController as unknown as Record<string, jest.Mock<any>>;

const detail = {
  group_id: 'g1',
  version: 1,
  kind: 'normal',
  status: 'active',
  visibility: 'private',
  originator_actor_id: 'bot-1',
  participants: [{ actor_id: 'bot-1', actor_kind: 'bot', name: 'Alpha', role: 'driver', mode: 'auto' }],
  driver_bot_uuid: 'bot-1',
  collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
  name: '群A',
  created_at: 1,
  updated_at: 2,
};

describe('groupMemberService', () => {
  beforeEach(() => {
    jest.resetAllMocks();
    gc.getGroup.mockResolvedValue({ code: 20000, message: '', request_id: 'r', data: detail });
    sc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], offset: 0, limit: 50, total: 0 } });
  });

  it('addMember posts participant then reloads group detail', async () => {
    gc.addGroupParticipant.mockResolvedValue({});
    const res = await groupMemberService.addMember('g1', 'bot-2');
    expect(gc.addGroupParticipant).toHaveBeenCalledWith('g1', 'bot-2');
    expect(res.ok).toBe(true);
    expect(res.ok && res.data.groupId).toBe('g1');
  });

  it('removeMember deletes participant then reloads group detail', async () => {
    gc.deleteGroupParticipant.mockResolvedValue({});
    const res = await groupMemberService.removeMember('g1', 'bot-2');
    expect(gc.deleteGroupParticipant).toHaveBeenCalledWith('g1', 'bot-2');
    expect(res.ok).toBe(true);
  });

  it('leaveGroup maps 409 to friendly conflict', async () => {
    gc.deleteGroupParticipant.mockRejectedValue({ status: 409 });
    const res = await groupMemberService.leaveGroup('g1', 'bot-2');
    expect(res.ok).toBe(false);
    expect(!res.ok && res.error.code).toBe('GROUP_CONFLICT');
  });
});
