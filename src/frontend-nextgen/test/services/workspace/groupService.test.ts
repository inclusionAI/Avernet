/** @jest-environment node */
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { groupService } from '@/services/workspace/groupService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

// auto-mock（不带 factory），避免 hoisted factory 内引用 jest.fn() 触发 @jest/globals 的 TDZ。
// sessionController 同样被 mock 以隔离 listGroupSessions（loadGroupDetail 路径用），本批断言不直接断言其调用。
jest.mock('@/services/backendApi/collaboration/collaborationGroupController');
jest.mock('@/services/backendApi/collaboration/sessionController');

const gc = groupController as unknown as Record<string, jest.Mock<any>>;
const sc = sessionController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
});

const identity = { id: 'bot-1', kind: 'bot' as const, displayName: 'B', online: true };

it('loadGroups passes view_bot_id + q, filters dm items', async () => {
  gc.listGroups.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: {
      items: [
        {
          group_id: 'g1',
          kind: 'normal',
          status: 'active',
          visibility: 'private',
          originator_actor_id: 'bot-1',
          participant_count: 2,
          driver_bot_uuid: 'bot-1',
          strategy: 'chat',
          name: '群A',
          created_at: 1,
          updated_at: 5,
        },
        {
          group_id: 'g2',
          kind: 'dm',
          status: 'active',
          visibility: 'private',
          originator_actor_id: 'bot-1',
          participant_count: 2,
          driver_bot_uuid: 'bot-1',
          strategy: 'chat',
          name: '私聊',
          created_at: 1,
          updated_at: 9,
        },
      ],
      total: 2,
      offset: 0,
      limit: 50,
    },
  });
  const res = await groupService.loadGroups(identity, { q: '群' });
  expect(gc.listGroups).toHaveBeenCalledWith(expect.objectContaining({ view_bot_id: 'bot-1', q: '群' }));
  expect(res.ok && res.data).toHaveLength(1);
  expect(res.ok && res.data[0].groupId).toBe('g1');
});

it('loadGroups passes membership when provided', async () => {
  gc.listGroups.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { items: [], total: 0, offset: 0, limit: 50 },
  });
  await groupService.loadGroups(identity, { membership: 'session_only' });
  expect(gc.listGroups).toHaveBeenCalledWith(
    expect.objectContaining({ kind: 'normal', view_bot_id: 'bot-1', membership: 'session_only' }),
  );
});

it('loadGroups omits membership when not provided', async () => {
  gc.listGroups.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { items: [], total: 0, offset: 0, limit: 50 },
  });
  await groupService.loadGroups(identity);
  expect((gc.listGroups.mock.calls[0][0] as any).membership).toBeUndefined();
});

it('user identity does not pass view_bot_id', async () => {
  gc.listGroups.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { items: [], total: 0, offset: 0, limit: 50 },
  });
  await groupService.loadGroups({ ...identity, id: 'me', kind: 'user' });
  expect((gc.listGroups.mock.calls[0][0] as any).view_bot_id).toBeUndefined();
});

it('getVisibleGroups kind filter + search + sort', () => {
  const groups = [
    {
      groupId: 'g1',
      name: 'Alpha 群',
      kind: 'free_chat',
      status: 'active',
      participants: [],
      sessions: [],
      lastMessageAt: 10,
      createdAt: 1,
      isPublic: false,
      deliveryPolicy: 'send_to_driver',
    },
    {
      groupId: 'g2',
      name: 'beta',
      kind: 'task_master_slave',
      status: 'active',
      participants: [],
      sessions: [],
      lastMessageAt: 5,
      createdAt: 9,
      isPublic: false,
      deliveryPolicy: 'send_to_driver',
    },
    {
      groupId: 'g3',
      name: 'alpha 2',
      kind: 'task_dag',
      status: 'dissolved',
      participants: [],
      sessions: [],
      lastMessageAt: 7,
      createdAt: 3,
      isPublic: false,
      deliveryPolicy: 'send_to_driver',
    },
  ] as any;
  expect(
    groupService.getVisibleGroups(groups, { search: 'alpha', kind: 'all', sort: 'lastActivity' }).map((g) => g.groupId),
  ).toEqual(['g1', 'g3']);
  expect(
    groupService
      .getVisibleGroups(groups, { search: '', kind: 'task_master_slave', sort: 'createdAt' })
      .map((g) => g.groupId),
  ).toEqual(['g2']);
  expect(
    groupService.getVisibleGroups(groups, { search: '', kind: 'all', sort: 'createdAt' }).map((g) => g.groupId),
  ).toEqual(['g2', 'g3', 'g1']);
});

describe('loadGroupSessions pagination', () => {
  it('dedupes concurrent session list requests with the same arguments', async () => {
    sc.listGroupSessions.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { items: [], offset: 0, limit: 10, total: 0 },
    });

    const [first, second] = await Promise.all([
      groupService.loadGroupSessionsOrBcs('g1', 'bot-1'),
      groupService.loadGroupSessionsOrBcs('g1', 'bot-1'),
    ]);

    expect(sc.listGroupSessions).toHaveBeenCalledTimes(1);
    expect(first).toEqual(second);
  });

  it('defaults to the first 10 sessions and preserves page metadata', async () => {
    sc.listGroupSessions.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          {
            session_id: 's1',
            group_id: 'g1',
            title: '会话一',
            status: 'running',
            created_at: 1,
            updated_at: 2,
          },
        ],
        total: 12,
        offset: 0,
        limit: 10,
      },
    });

    const res = await groupService.loadGroupSessions('g1', 'bot-1');

    expect(sc.listGroupSessions).toHaveBeenCalledWith('g1', { offset: 0, limit: 10, view_bot_id: 'bot-1' });
    expect(res).toEqual({
      ok: true,
      data: {
        items: [expect.objectContaining({ sessionId: 's1', groupId: 'g1', title: '会话一' })],
        offset: 0,
        limit: 10,
        total: 12,
        hasMore: true,
      },
    });
  });

  it('passes an explicit offset and limit for loading the next page', async () => {
    sc.listGroupSessions.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { items: [], total: 12, offset: 10, limit: 10 },
    });

    const res = await groupService.loadGroupSessions('g1', undefined, { offset: 10, limit: 10 });

    expect(sc.listGroupSessions).toHaveBeenCalledWith('g1', { offset: 10, limit: 10 });
    expect(res.ok && res.data).toMatchObject({ offset: 10, limit: 10, total: 12, hasMore: true });
  });
});

describe('loadGroupDetail', () => {
  it('passes view_bot_id to listGroupSessions when provided', async () => {
    gc.getGroup.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        group_id: 'g1',
        name: 'X',
        strategy: 'chat',
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
        status: 'active',
        participants: [],
        originator_actor_id: 'bot-1',
        updated_at: 1,
        created_at: 1,
      },
    });
    sc.listGroupSessions.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { items: [], offset: 0, limit: 50, total: 0 },
    });
    await groupService.loadGroupDetail('g1', 'bot-1');
    expect(sc.listGroupSessions).toHaveBeenCalledWith(
      'g1',
      expect.objectContaining({ offset: 0, limit: 50, view_bot_id: 'bot-1' }),
    );
  });

  it('omits view_bot_id when not provided', async () => {
    gc.getGroup.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        group_id: 'g1',
        name: 'X',
        strategy: 'chat',
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
        status: 'active',
        participants: [],
        originator_actor_id: 'bot-1',
        updated_at: 1,
        created_at: 1,
      },
    });
    sc.listGroupSessions.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { items: [], offset: 0, limit: 50, total: 0 },
    });
    await groupService.loadGroupDetail('g1');
    expect(sc.listGroupSessions).toHaveBeenCalledWith('g1', { offset: 0, limit: 50 });
  });
});

describe('policy', () => {
  const group = {
    groupId: 'g1',
    ownerUserId: 'me-owner',
    participants: [{ actorId: 'bot-1', role: 'member' }],
    status: 'active',
  } as any;
  it('canManageGroup: originator user ok, member bot denied', () => {
    expect(groupService.canManageGroup(group, 'me-owner')).toEqual({ allowed: true });
    expect(groupService.canManageGroup(group, 'bot-1')).toMatchObject({ allowed: false });
    expect(
      groupService.canManageGroup(
        { ...group, participants: [{ actorId: 'bot-1', role: 'driver' as const }] } as never,
        'bot-1',
      ),
    ).toEqual({ allowed: true });
    expect(
      groupService.canManageGroup(
        { ...group, participants: [{ actorId: 'bot-1', role: 'manager' as const }] } as never,
        'bot-1',
      ),
    ).toEqual({ allowed: true });
  });
  it('canDissolveGroup: dissolved group denied', () => {
    expect(groupService.canDissolveGroup({ ...group, status: 'dissolved' }, 'me-owner')).toMatchObject({
      allowed: false,
      disabledReason: '该协作群已解散',
    });
  });
});

describe('createGroup', () => {
  const baseDetail = {
    code: 20000,
    message: '',
    request_id: 'r',
    data: {
      group_id: 'g-new',
      version: 1,
      kind: 'normal',
      status: 'active',
      visibility: 'private',
      originator_actor_id: 'b1',
      participants: [{ actor_id: 'b1', actor_kind: 'bot', role: 'driver', mode: 'auto', name: 'Alpha' }],
      driver_bot_uuid: 'b1',
      collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
      name: '我的群',
      created_at: 1,
      updated_at: 1,
    },
  };

  it('chat strategy maps to GroupCreateChatBody with delivery_policy', async () => {
    gc.createGroup.mockResolvedValue(baseDetail);
    gc.getGroup.mockResolvedValue(baseDetail);
    sc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 50 } });
    const res = await groupService.createGroup({
      name: '我的群',
      strategy: 'chat',
      deliveryPolicy: 'send_to_driver',
      driverBotUuid: 'b1',
      participants: [{ actor_id: 'b1' }],
    });
    expect(gc.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        group_kind: 'normal',
        name: '我的群',
        driver_bot_uuid: 'b1',
        participants: [{ actor_id: 'b1', role: 'driver' }],
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
      }),
    );
    expect(res.ok).toBe(true);
  });

  it('state_machine strategy assembles definition.content_yaml + participant_bindings', async () => {
    gc.createGroup.mockResolvedValue({
      ...baseDetail,
      data: {
        ...baseDetail.data,
        collaboration: { strategy: 'state_machine', definition: { definition_id: 'd1', version: 1 } },
      },
    });
    gc.getGroup.mockResolvedValue({
      ...baseDetail,
      data: {
        ...baseDetail.data,
        collaboration: { strategy: 'state_machine', definition: { definition_id: 'd1', version: 1 } },
      },
    });
    gc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 50 } });
    sc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 50 } });
    const yaml = 'participants:\n  - alpha\nroles:\n  - driver';
    await groupService.createGroup({
      name: '我的群',
      strategy: 'state_machine',
      definitionYaml: yaml,
      driverBotUuid: 'b1',
      participants: [{ actor_id: 'b1' }],
    });
    expect(gc.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        collaboration: {
          strategy: 'state_machine',
          definition: { content_yaml: yaml },
          participant_bindings: [{ binding: 'role-1', actor_ids: ['b1'] }],
        },
      }),
    );
  });

  it('manager_worker maps driver to manager and other members to worker', async () => {
    gc.createGroup.mockResolvedValue(baseDetail);
    gc.getGroup.mockResolvedValue(baseDetail);
    sc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 50 } });

    await groupService.createGroup({
      name: '任务群',
      strategy: 'manager_worker',
      driverBotUuid: 'b1',
      participants: [{ actor_id: 'b1' }, { actor_id: 'b2' }],
    });

    expect(gc.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        participants: [
          { actor_id: 'b1', role: 'manager' },
          { actor_id: 'b2', role: 'worker' },
        ],
      }),
    );
  });

  it('400 maps to friendlyMessage inline', async () => {
    gc.createGroup.mockRejectedValue({ status: 400, message: 'YAML 校验不通过: invalid role' });
    const res = await groupService.createGroup({
      name: '我的群',
      strategy: 'state_machine',
      definitionYaml: 'a:\n  b',
      driverBotUuid: 'b1',
      participants: [{ actor_id: 'b1' }],
    });
    expect(res.ok).toBe(false);
    expect((res as { error: { friendlyMessage: string } }).error.friendlyMessage).toMatch(/YAML 校验不通过/);
  });

  it('409 maps to GROUP_CONFLICT', async () => {
    gc.createGroup.mockRejectedValue({ status: 409 });
    const res = await groupService.createGroup({
      name: '我的群',
      strategy: 'chat',
      driverBotUuid: 'b1',
      participants: [{ actor_id: 'b1' }],
    });
    expect(res.ok).toBe(false);
    expect((res as { error: { code: string } }).error.code).toBe('GROUP_CONFLICT');
  });
});
