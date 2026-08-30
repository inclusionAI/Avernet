import { mapGroupListItem, mapSessionListItem } from '@/services/workspace/mappers';
import { describe, expect, it } from '@jest/globals';

describe('mappers', () => {
  it('maps normal group list item → GroupView', () => {
    const dto = {
      group_id: 'g1',
      version: 1,
      kind: 'normal' as const,
      status: 'active' as const,
      visibility: 'public' as const,
      membership: 'direct' as const,
      originator_actor_id: 'prop@1',
      participant_count: 3,
      driver_bot_uuid: 'bot-uuid-1',
      strategy: 'chat' as const,
      created_at: 1700000000000,
      updated_at: 1700000100000,
      name: '我的群',
    };
    expect(mapGroupListItem(dto)).toEqual({
      groupId: 'g1',
      name: '我的群',
      kind: 'free_chat',
      status: 'active',
      isPublic: true,
      participants: [],
      sessions: [],
      lastMessageAt: 1700000100000,
      createdAt: 1700000000000,
      participantCount: 3,
      deliveryPolicy: 'send_to_driver',
      // dto.membership 存在时 mapper 原样透传（mappers.ts:67）
      membership: 'direct',
    });
  });

  it('maps manager_worker strategy → task_master_slave, state_machine → task_dag', () => {
    const base = {
      group_id: 'g2',
      version: 1,
      kind: 'normal' as const,
      status: 'active' as const,
      visibility: 'private' as const,
      membership: 'direct' as const,
      originator_actor_id: 'prop@1',
      participant_count: 2,
      driver_bot_uuid: 'b1',
      created_at: 1,
      updated_at: 1,
    };
    expect(mapGroupListItem({ ...base, strategy: 'manager_worker' }).kind).toBe('task_master_slave');
    expect(mapGroupListItem({ ...base, strategy: 'state_machine' }).kind).toBe('task_dag');
  });

  it('maps session list item → SessionView with favorite default false', () => {
    const dto = {
      session_id: 'g1:s1',
      group_id: 'g1',
      title: '会话一',
      kind: 'chat' as const,
      status: 'running' as const,
      created_at: 1,
      updated_at: 2,
    };
    expect(mapSessionListItem(dto)).toEqual({
      sessionId: 'g1:s1',
      groupId: 'g1',
      title: '会话一',
      kind: 'chat',
      status: 'running',
      participants: [],
      lastMessageAt: 2,
      createdAt: 1,
      favorite: false,
    });
  });

  it('maps session participant_count when list item does not carry participants', () => {
    const dto = {
      session_id: 'g1:s1',
      group_id: 'g1',
      title: '会话一',
      kind: 'chat' as const,
      status: 'running' as const,
      participant_count: 3,
      created_at: 1,
      updated_at: 2,
    };
    expect(mapSessionListItem(dto).participantCount).toBe(3);
  });

  it('maps session participants (mode/name) when DTO carries them', () => {
    const dto = {
      session_id: 'g1:s1',
      group_id: 'g1',
      title: '会话一',
      kind: 'chat' as const,
      status: 'running' as const,
      participants: [
        { actor_id: 'b1', actor_kind: 'bot' as const, name: 'Alpha', role: 'driver' as const, mode: 'auto' as const },
        {
          actor_id: 'human_1',
          actor_kind: 'human' as const,
          name: '章梧',
          role: 'consultant' as const,
          mode: 'absent' as const,
        },
      ],
      created_at: 1,
      updated_at: 2,
    };
    const view = mapSessionListItem(dto);
    expect(view.participants).toEqual([
      expect.objectContaining({ actorId: 'b1', kind: 'bot', name: 'Alpha', mode: 'auto' }),
      expect.objectContaining({ actorId: 'human_1', kind: 'human', name: '章梧', mode: 'absent' }),
    ]);
  });
});
