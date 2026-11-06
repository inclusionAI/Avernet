import { mapBcsSessionItem, mapGroupListItem, mapSessionDetail, mapSessionListItem } from '@/services/workspace/mappers';
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
      participantDetailsLoaded: false,
      sessions: [],
      lastMessageAt: 1700000100000,
      createdAt: 1700000000000,
      participantCount: 3,
      driverBotUuid: 'bot-uuid-1',
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
      participantDetailsLoaded: false,
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

  it('maps full session participants (mode/name) through the detail mapper', () => {
    const dto = {
      session_id: 'g1:s1',
      group_id: 'g1',
      version: 1,
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
    const view = mapSessionDetail(dto);
    expect(view.participantDetailsLoaded).toBe(true);
    expect(view.participants).toEqual([
      expect.objectContaining({ actorId: 'b1', kind: 'bot', name: 'Alpha', mode: 'auto' }),
      expect.objectContaining({ actorId: 'human_1', kind: 'human', name: '章梧', mode: 'absent' }),
    ]);
    expect(view.participantSummaries).toEqual([
      { actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: undefined },
      { actorId: 'human_1', kind: 'human', name: '章梧', avatarUrl: undefined },
    ]);
  });

  it('session list item exposes identity summaries but never fabricates role/mode', () => {
    const view = mapSessionListItem({
      session_id: 'g1:s1',
      group_id: 'g1',
      status: 'running',
      participants: [
        { actor_id: 'b1', actor_kind: 'bot', name: ' Alpha ', avatar_url: ' /b1.png ', role: 'driver', mode: 'auto' },
      ],
      created_at: 1,
      updated_at: 2,
    });
    expect(view.participants).toEqual([]);
    expect(view.participantDetailsLoaded).toBe(false);
    expect(view.participantSummaries).toEqual([
      { actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: '/b1.png' },
    ]);
    expect(view.participantSummaries?.[0]).not.toHaveProperty('role');
    expect(view.participantSummaries?.[0]).not.toHaveProperty('mode');
  });

  it('summary carries avatar without inventing control fields', () => {
    const dto = {
      session_id: 's1',
      group_id: 'g1',
      version: 1,
      status: 'running' as const,
      created_at: 1,
      updated_at: 2,
      participants: [
        { actor_id: 'b1', actor_kind: 'bot' as const, name: null, avatar_url: ' /b1.png ' },
      ],
    };
    const summary = mapSessionListItem(dto);
    expect(summary.participants).toEqual([]);
    expect(summary.participantSummaries).toEqual([
      { actorId: 'b1', kind: 'bot', name: 'b1', avatarUrl: '/b1.png' },
    ]);
    const detail = mapSessionDetail({
      ...dto,
      participants: [
        {
          ...dto.participants[0],
          name: 'B1',
          role: 'worker',
          mode: 'muted',
          message_view_scope: 'full',
          avatar_url: null,
        },
      ],
    });
    expect(detail.participants[0]).toMatchObject({ role: 'worker', mode: 'muted', messageViewScope: 'full' });
    expect(detail.participantSummaries?.[0].avatarUrl).toBeUndefined();
  });

  it('session summaries use only the session rows, not the group fixture', () => {
    const group = mapGroupListItem({
      group_id: 'g1',
      kind: 'normal',
      status: 'active',
      visibility: 'private',
      originator_actor_id: 'u1',
      participant_count: 1,
      driver_bot_uuid: 'b1',
      strategy: 'chat',
      created_at: 1,
      updated_at: 2,
      participants: [{ actor_id: 'b1', actor_kind: 'bot', name: 'GroupBot', avatar_url: '/b1.png' }],
    });
    const session = mapSessionListItem({
      session_id: 's1',
      group_id: 'g1',
      status: 'running',
      created_at: 1,
      updated_at: 2,
      participants: [{ actor_id: 'b9', actor_kind: 'bot', name: 'SessionBot', avatar_url: '/b9.png' }],
    });
    expect(group.participantSummaries?.map((s) => s.actorId)).toEqual(['b1']);
    expect(session.participantSummaries?.map((s) => s.actorId)).toEqual(['b9']);
  });
});

type SummaryDto = {
  actor_id: string;
  actor_kind: 'human' | 'bot';
  name: string | null;
  avatar_url?: string | null;
};

describe('group list participant summaries and count', () => {
  const groupWith = (participant_count: number | undefined, participants?: SummaryDto[]) => ({
    group_id: 'g-count',
    kind: 'normal' as const,
    status: 'active' as const,
    visibility: 'private' as const,
    originator_actor_id: 'u1',
    participant_count,
    driver_bot_uuid: 'b1',
    strategy: 'chat' as const,
    created_at: 1,
    updated_at: 2,
    ...(participants ? { participants } : {}),
  });
  const fourSummaries = [
    { actor_id: 'b1', actor_kind: 'bot' as const, name: 'Bot 1', avatar_url: '/b1.png' },
    { actor_id: 'b2', actor_kind: 'bot' as const, name: 'Bot 2', avatar_url: null },
    { actor_id: 'h1', actor_kind: 'human' as const, name: null, avatar_url: ' /h1.png ' },
    { actor_id: 'h2', actor_kind: 'human' as const, name: '人二', avatar_url: '   ' },
  ];

  it('keeps a valid count (including explicit 0) and falls back to the summary size otherwise', () => {
    expect(mapGroupListItem(groupWith(undefined, fourSummaries)).participantCount).toBe(4);
    expect(mapGroupListItem(groupWith(NaN, fourSummaries)).participantCount).toBe(4);
    expect(mapGroupListItem(groupWith(-1, fourSummaries)).participantCount).toBe(4);
    expect(mapGroupListItem(groupWith(0, fourSummaries)).participantCount).toBe(0);
  });

  it('maps human/bot avatars and blank names without confusing the three shapes', () => {
    const withPeople = mapGroupListItem(groupWith(4, fourSummaries));
    expect(withPeople.participants).toEqual([]);
    expect(withPeople.participantDetailsLoaded).toBe(false);
    expect(withPeople.participantSummaries).toEqual([
      { actorId: 'b1', kind: 'bot', name: 'Bot 1', avatarUrl: '/b1.png' },
      { actorId: 'b2', kind: 'bot', name: 'Bot 2', avatarUrl: undefined },
      { actorId: 'h1', kind: 'human', name: 'h1', avatarUrl: '/h1.png' },
      { actorId: 'h2', kind: 'human', name: '人二', avatarUrl: undefined },
    ]);

    const explicitEmpty = mapGroupListItem(groupWith(undefined, []));
    expect(explicitEmpty.participantSummaries).toEqual([]);
    expect(explicitEmpty.participantCount).toBe(0);

    const omitted = mapGroupListItem(groupWith(undefined));
    expect(omitted.participantSummaries).toBeUndefined();
    expect(omitted.participantCount).toBe(0);
  });

  it('falls back to 0 when neither count nor participant details exist', () => {
    expect(mapGroupListItem(groupWith(undefined)).participantCount).toBe(0);
  });
});

it('异常协作群列表字段降级为可渲染值，避免高级配置打开时白屏', () => {
  const view = mapGroupListItem({
    group_id: 'g-legacy',
    kind: 'normal',
    status: 'active',
    visibility: 'private',
    originator_actor_id: 'u1',
    participant_count: { value: 2 },
    driver_bot_uuid: { value: 'bot-1' },
    strategy: { value: 'chat' },
    name: { value: '异常群' },
    created_at: { value: 1 },
    updated_at: { value: 2 },
  } as any);

  expect(view).toMatchObject({
    groupId: 'g-legacy',
    name: '未命名群',
    kind: 'free_chat',
    participantCount: 0,
    driverBotUuid: '',
    createdAt: 0,
    lastMessageAt: 0,
  });
});

it('异常 BCS 会话字段降级为可渲染值', () => {
  const view = mapBcsSessionItem(
    {
      session_id: { value: 's1' },
      group_id: { value: 'g1' },
      session_title: { value: '异常会话' },
      participants: [{ bot_uuid: { value: 'b1' }, bot_name: { value: 'Bot' }, actor_kind: { value: 'bot' } }],
      participant_count: { value: 1 },
      created_at: { value: 1 },
      updated_at: { value: 2 },
    } as any,
    'fallback-group',
  );

  expect(view).toMatchObject({
    sessionId: '',
    groupId: 'fallback-group',
    title: '未命名会话',
    participants: [],
    participantSummaries: [{ actorId: '', name: '', kind: 'bot' }],
    participantDetailsLoaded: false,
    lastMessageAt: 0,
    createdAt: 0,
  });
});

describe('BCS raw session participants', () => {
  const rawSession = (participants?: unknown[]) => ({
    session_id: 's1',
    group_id: 'g1',
    participants,
    created_at: 1,
    updated_at: 2,
  });

  it('keeps full participants when every row has a valid role and mode', () => {
    const view = mapBcsSessionItem(
      rawSession([
        { bot_uuid: 'b1', bot_name: 'Alpha', actor_kind: 'bot', role: 'driver', mode: 'muted', avatar_url: ' /b1.png ' },
        { actor_id: 'human_1', name: '章梧', actor_kind: 'human', role: 'consultant', mode: 'present' },
      ]) as never,
      'g1',
    );

    expect(view.participantDetailsLoaded).toBe(true);
    expect(view.participants).toEqual([
      expect.objectContaining({ actorId: 'b1', kind: 'bot', name: 'Alpha', role: 'driver', mode: 'muted', avatarUrl: '/b1.png' }),
      expect.objectContaining({ actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' }),
    ]);
    expect(view.participantSummaries).toEqual([
      { actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: '/b1.png' },
      { actorId: 'human_1', kind: 'human', name: '章梧', avatarUrl: undefined },
    ]);
  });

  it('keeps only identity summaries for V1 lightweight rows (no observer/auto fabrication)', () => {
    const view = mapBcsSessionItem(
      rawSession([
        { bot_uuid: 'b1', bot_name: 'Alpha', actor_kind: 'bot' },
        { actor_id: 'human_1', name: '章梧', actor_kind: 'human', avatar_url: '/h1.png' },
      ]) as never,
      'g1',
    );

    expect(view.participantDetailsLoaded).toBe(false);
    expect(view.participants).toEqual([]);
    expect(view.participantSummaries).toEqual([
      { actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: undefined },
      { actorId: 'human_1', kind: 'human', name: '章梧', avatarUrl: '/h1.png' },
    ]);
  });

  it('drops full participants when only part of the rows carry control fields', () => {
    const view = mapBcsSessionItem(
      rawSession([
        { bot_uuid: 'b1', role: 'driver', mode: 'auto' },
        { bot_uuid: 'b2' },
      ]) as never,
      'g1',
    );

    expect(view.participantDetailsLoaded).toBe(false);
    expect(view.participants).toEqual([]);
    expect(view.participantSummaries?.map((s) => s.actorId)).toEqual(['b1', 'b2']);
  });

  it('distinguishes an explicit empty list from an omitted field', () => {
    const explicitEmpty = mapBcsSessionItem(rawSession([]) as never, 'g1');
    expect(explicitEmpty.participantSummaries).toEqual([]);
    expect(explicitEmpty.participantDetailsLoaded).toBe(false);

    const omitted = mapBcsSessionItem(rawSession() as never, 'g1');
    expect(omitted.participantSummaries).toBeUndefined();
    expect(omitted.participantDetailsLoaded).toBe(false);
  });
});
