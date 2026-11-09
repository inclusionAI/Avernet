/** @jest-environment node */
import type { GroupView } from '@/domain/collaboration';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';
import type { GroupTasksExtension } from '@/extensions/contract';
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { createGroupViaExecute } from '@/services/workspace/groupExecuteService';
import { groupService } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

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

describe('BCS group routing by bcs_grp_ prefix', () => {
  const originalFetch = global.fetch;
  const userMe = { id: 'human-me', kind: 'user' as const, displayName: 'Me', online: true };

  function jsonOk(body: unknown): Response {
    return {
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      json: async () => body,
    } as unknown as Response;
  }

  beforeEach(() => {
    useWorkspaceStore.setState({ bcsGroupIds: {} });
  });
  afterEach(() => {
    global.fetch = originalFetch;
    useWorkspaceStore.setState({ bcsGroupIds: {} });
  });

  it('routes bcs_grp_ group to BCS fetch path even when in-memory marker is empty (fresh tab)', async () => {
    // 模拟新开页 target=_blank 全新挂载:bcsGroupIds 内存标记为空(非持久化),
    // 仅靠 groupId 的 bcs_grp_ 前缀兜底应走 BCS 会话端点(raw-fetch 旁路)。
    // view_bot_id 语义与通用端点一致：当前身份 mine bot_id，供后端按角色视角过滤会话。
    useWorkspaceStore.setState({ bcsGroupIds: {} });
    global.fetch = jest
      .fn<typeof fetch>()
      .mockResolvedValue(jsonOk({ code: 200000, message: 'OK', data: { items: [], offset: 0, limit: 10, total: 0 } }));

    const res = await groupService.loadGroupSessionsOrBcs('bcs_grp_abc', userMe.id);

    expect(sc.listGroupSessions).not.toHaveBeenCalled();
    expect(global.fetch).toHaveBeenCalledTimes(1);
    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string;
    expect(calledUrl).toContain(
      '/openapi/v1/collaboration/groups/bcs_grp_abc/sessions?offset=0&limit=10&view_bot_id=human-me',
    );
    expect(res.ok).toBe(true);
    expect(res.ok && res.data).toMatchObject({ items: [], total: 0, hasMore: false });
    expect(typeof (res.ok && res.data.limit)).toBe('number');
  });

  it('BCS 群无当前身份时降级：URL 不带 view_bot_id', async () => {
    useWorkspaceStore.setState({ bcsGroupIds: {} });
    global.fetch = jest
      .fn<typeof fetch>()
      .mockResolvedValue(jsonOk({ code: 200000, message: 'OK', data: { items: [], offset: 0, limit: 10, total: 0 } }));

    await groupService.loadGroupSessionsOrBcs('bcs_grp_abc', undefined);

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string;
    expect(calledUrl).toContain('/groups/bcs_grp_abc/sessions?offset=0&limit=10');
    expect(calledUrl).not.toContain('view_bot_id');
  });

  it('BCS 群详情路径：sessions 子请求同样携带 view_bot_id（群详情请求保持不变）', async () => {
    useWorkspaceStore.setState({ bcsGroupIds: {} });
    global.fetch = jest.fn<typeof fetch>().mockImplementation(async (url: unknown) => {
      const u = String(url);
      if (u.includes('/sessions')) return jsonOk({ code: 200000, message: 'OK', data: { items: [] } });
      return jsonOk({ code: 200000, message: 'OK', data: { id: 'bcs_grp_abc', name: 'G', participants: [] } });
    });

    const res = await groupService.loadGroupDetailOrBcs('bcs_grp_abc', 'human-me');

    expect(res.ok).toBe(true);
    const urls = (global.fetch as jest.Mock).mock.calls.map((c: unknown[]) => String(c[0]));
    expect(urls.some((u: string) => u.includes('/sessions?') && u.includes('view_bot_id=human-me'))).toBe(true);
    expect(urls.some((u: string) => u.endsWith('/groups/bcs_grp_abc'))).toBe(true);
  });

  it('BCS 群详情字段异常时仍返回可渲染的 GroupView', async () => {
    useWorkspaceStore.setState({ bcsGroupIds: {} });
    global.fetch = jest.fn<typeof fetch>().mockImplementation(async (url: unknown) => {
      const u = String(url);
      if (u.includes('/sessions')) return jsonOk({ code: 200000, message: 'OK', data: { items: [] } });
      return jsonOk({
        code: 200000,
        message: 'OK',
        data: {
          id: 'bcs_grp_legacy',
          label: { value: '异常群名' },
          participants: [{ bot_uuid: { value: 'bot-1' }, bot_name: { value: '异常 Bot' }, role: 'worker' }],
        },
      });
    });

    const res = await groupService.loadGroupDetailOrBcs('bcs_grp_legacy', 'human-me');

    expect(res.ok).toBe(true);
    expect(res.ok && res.data.name).toBe('未命名群');
    expect(res.ok && res.data.participants[0]).toMatchObject({
      actorId: '',
      name: '',
      kind: 'bot',
    });
  });

  it('BCS 群详情回读 human_mention_notify_mode（OpenAPI envelope 形态）', async () => {
    useWorkspaceStore.setState({ bcsGroupIds: {} });
    global.fetch = jest.fn<typeof fetch>().mockImplementation(async (url: unknown) => {
      const u = String(url);
      if (u.includes('/sessions')) return jsonOk({ code: 200000, message: 'OK', data: { items: [] } });
      return jsonOk({
        code: 200000,
        message: 'OK',
        data: { id: 'bcs_grp_abc', name: 'G', participants: [], human_mention_notify_mode: 'driver_bot_only' },
      });
    });

    const res = await groupService.loadGroupDetailOrBcs('bcs_grp_abc', 'human-me');

    expect(res.ok).toBe(true);
    expect(res.ok && res.data.humanMentionNotifyMode).toBe('driver_bot_only');
  });

  it('BCS 历史详情缺省 human_mention_notify_mode 时不伪造默认值', async () => {
    useWorkspaceStore.setState({ bcsGroupIds: {} });
    global.fetch = jest.fn<typeof fetch>().mockImplementation(async (url: unknown) => {
      const u = String(url);
      if (u.includes('/sessions')) return jsonOk({ code: 200000, message: 'OK', data: { items: [] } });
      return jsonOk({ code: 200000, message: 'OK', data: { id: 'bcs_grp_legacy', name: 'G', participants: [] } });
    });

    const res = await groupService.loadGroupDetailOrBcs('bcs_grp_legacy', 'human-me');

    expect(res.ok).toBe(true);
    expect(res.ok && res.data.humanMentionNotifyMode).toBeUndefined();
  });

  it('routes non-prefixed group to generic listGroupSessions path with view_bot_id', async () => {
    sc.listGroupSessions.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { items: [], offset: 0, limit: 10, total: 0 },
    });

    await groupService.loadGroupSessionsOrBcs('g-presets-1', userMe.id);

    expect(sc.listGroupSessions).toHaveBeenCalledWith('g-presets-1', {
      offset: 0,
      limit: 10,
      view_bot_id: 'human-me',
    });
    expect(global.fetch).toBe(originalFetch);
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

  it('participants 回显 message_view_scope 时映射为 messageViewScope，无值时不回显', async () => {
    const detailData = (participants: unknown[]) => ({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        group_id: 'g1',
        name: 'X',
        strategy: 'chat',
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
        status: 'active',
        participants,
        originator_actor_id: 'bot-1',
        updated_at: 1,
        created_at: 1,
      },
    });
    gc.getGroup.mockResolvedValue(
      detailData([
        {
          actor_id: 'human-1',
          actor_kind: 'human',
          name: '成员甲',
          role: 'consultant',
          mode: 'present',
          message_view_scope: 'participant',
        },
        { actor_id: 'human-2', actor_kind: 'human', name: '成员乙', role: 'consultant', mode: 'present' },
      ]),
    );
    sc.listGroupSessions.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { items: [], offset: 0, limit: 50, total: 0 },
    });

    const res = await groupService.loadGroupDetail('g1');
    expect(res.ok).toBe(true);
    const participants = res.ok ? res.data.participants : [];
    expect(participants[0].messageViewScope).toBe('participant');
    expect(participants[1].messageViewScope).toBeUndefined();
  });
});

describe('participant summaries wiring', () => {
  const groupDetail = (participants: unknown[]) => ({
    code: 20000,
    message: '',
    request_id: 'r',
    data: {
      group_id: 'g1',
      version: 1,
      kind: 'normal' as const,
      name: 'X',
      status: 'active' as const,
      visibility: 'private' as const,
      strategy: 'chat' as const,
      collaboration: { strategy: 'chat' as const, delivery_policy: { bot_final_delivery: 'send_to_driver' as const } },
      participants,
      originator_actor_id: 'bot-1',
      driver_bot_uuid: 'bot-1',
      updated_at: 1,
      created_at: 1,
    },
  });
  const emptySessions = {
    code: 20000,
    message: '',
    request_id: 'r',
    data: { items: [], offset: 0, limit: 50, total: 0 },
  };

  it('loadGroups consumes list summaries with a count fallback and no fabricated control fields', async () => {
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
            driver_bot_uuid: 'bot-1',
            strategy: 'chat',
            name: '群A',
            created_at: 1,
            updated_at: 5,
            participants: [
              { actor_id: 'b1', actor_kind: 'bot', name: 'Bot 1', avatar_url: '/b1.png' },
              { actor_id: 'b2', actor_kind: 'bot', name: 'Bot 2', avatar_url: null },
              { actor_id: 'h1', actor_kind: 'human', name: null, avatar_url: '/h1.png' },
              { actor_id: 'h2', actor_kind: 'human', name: '人二', avatar_url: null },
            ],
          },
        ],
        total: 1,
        offset: 0,
        limit: 50,
      },
    });

    const res = await groupService.loadGroups(identity);

    expect(res.ok).toBe(true);
    const group = res.ok ? res.data[0] : null;
    expect(group?.participantDetailsLoaded).toBe(false);
    expect(group?.participantCount).toBe(4);
    expect(group?.participants).toEqual([]);
    expect(group?.participantSummaries).toEqual([
      { actorId: 'b1', kind: 'bot', name: 'Bot 1', avatarUrl: '/b1.png' },
      { actorId: 'b2', kind: 'bot', name: 'Bot 2', avatarUrl: undefined },
      { actorId: 'h1', kind: 'human', name: 'h1', avatarUrl: '/h1.png' },
      { actorId: 'h2', kind: 'human', name: '人二', avatarUrl: undefined },
    ]);
    expect(group?.participantSummaries?.[0]).not.toHaveProperty('role');
    expect(group?.participantSummaries?.[0]).not.toHaveProperty('mode');
  });

  it('loadGroupDetail derives summaries from the full participants and marks details loaded', async () => {
    gc.getGroup.mockResolvedValue(
      groupDetail([
        {
          actor_id: 'b1',
          actor_kind: 'bot',
          name: 'Alpha',
          role: 'driver',
          mode: 'muted',
          avatar_url: ' /b1.png ',
        },
        { actor_id: 'human_1', actor_kind: 'human', name: '章梧', role: 'consultant', mode: 'present', avatar_url: null },
      ]),
    );
    sc.listGroupSessions.mockResolvedValue(emptySessions);

    const res = await groupService.loadGroupDetail('g1');

    expect(res.ok).toBe(true);
    const group = res.ok ? res.data : null;
    expect(group?.participantDetailsLoaded).toBe(true);
    expect(group?.participantCount).toBe(2);
    expect(group?.participants).toEqual([
      expect.objectContaining({ actorId: 'b1', role: 'driver', mode: 'muted', avatarUrl: '/b1.png' }),
      expect.objectContaining({ actorId: 'human_1', role: 'member', mode: 'present', avatarUrl: undefined }),
    ]);
    expect(group?.participantSummaries).toEqual([
      { actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: '/b1.png' },
      { actorId: 'human_1', kind: 'human', name: '章梧', avatarUrl: undefined },
    ]);
  });

  it('loadGroupDetail marks explicitly empty participant details as loaded', async () => {
    gc.getGroup.mockResolvedValue(groupDetail([]));
    sc.listGroupSessions.mockResolvedValue(emptySessions);

    const res = await groupService.loadGroupDetail('g1');

    expect(res.ok && res.data.participantDetailsLoaded).toBe(true);
    expect(res.ok && res.data.participantSummaries).toEqual([]);
    expect(res.ok && res.data.participantCount).toBe(0);
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
    expect(groupService.canManageGroup(group, 'bot-1')).toEqual({
      allowed: false,
      disabledReason: '仅群主/主节点可管理该协作群',
    });
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
  it('canDissolveGroup: driver bot owner from group list item ok', () => {
    const listGroup = {
      ...group,
      participants: [],
      driverBotUuid: 'bot-driver',
    } as any;
    expect(groupService.canDissolveGroup(listGroup, 'bot-driver')).toEqual({ allowed: true });
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

  it('chat strategy maps originator and preserves initial_session_id', async () => {
    gc.createGroup.mockResolvedValue({
      ...baseDetail,
      data: { ...baseDetail.data, initial_session_id: 'initial-session' },
    });
    gc.getGroup.mockResolvedValue(baseDetail);
    sc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 50 } });
    const res = await groupService.createGroup({
      name: '我的群',
      strategy: 'chat',
      deliveryPolicy: 'send_to_driver',
      originator: 'actor-bot',
      driverBotUuid: 'b1',
      participants: [{ actor_id: 'b1' }],
    });
    expect(gc.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        group_kind: 'normal',
        name: '我的群',
        originator: 'actor-bot',
        driver_bot_uuid: 'b1',
        participants: [{ actor_id: 'b1', role: 'driver' }],
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
      }),
    );
    expect(res.ok).toBe(true);
    expect(res.ok && res.data.initialSessionId).toBe('initial-session');
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
    const yaml = 'participants:\n  writer: {}\n  editor: {}';
    const participantBindings = [
      { binding: 'editor', actor_ids: ['b1'] },
      { binding: 'writer', actor_ids: ['b2'] },
    ];
    await groupService.createGroup({
      name: '我的群',
      strategy: 'state_machine',
      definitionYaml: yaml,
      driverBotUuid: 'b1',
      originator: 'actor-bot',
      participants: [{ actor_id: 'b1' }, { actor_id: 'b2' }],
      participantBindings,
    });
    expect(gc.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        participants: [
          { actor_id: 'b1', role: 'driver' },
          { actor_id: 'b2', role: 'consultant' },
        ],
        collaboration: {
          strategy: 'state_machine',
          definition: { content_yaml: yaml },
          participant_bindings: participantBindings,
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
      originator: 'actor-bot',
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

  it.each([
    ['chat', 'run-driver'],
    ['manager_worker', 'run-manager'],
  ] as const)('preserves the %s initial responder run fields from the create response', async (strategy, runId) => {
    gc.createGroup.mockResolvedValue({
      ...baseDetail,
      data: {
        ...baseDetail.data,
        initial_session_id: 'session-initial',
        initial_run: {
          run_id: runId,
          bot_uuid: 'b1',
          activity_kind: 'group_bootstrap',
          state: 'running',
          started_at: '2026-08-31T00:00:00Z',
        },
      },
    });
    gc.getGroup.mockResolvedValue(baseDetail);
    sc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 50 } });

    const res = await groupService.createGroup({
      name: '协作群',
      strategy,
      driverBotUuid: 'b1',
      originator: 'actor-1',
      participants: [{ actor_id: 'b1' }],
    });

    expect(res).toEqual(
      expect.objectContaining({
        ok: true,
        data: expect.objectContaining({
          initialSessionId: 'session-initial',
          initialRun: expect.objectContaining({ runId, botUuid: 'b1', state: 'running' }),
        }),
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
      originator: 'actor-bot',
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
      originator: 'actor-bot',
      participants: [{ actor_id: 'b1' }],
    });
    expect(res.ok).toBe(false);
    expect((res as { error: { code: string } }).error.code).toBe('GROUP_CONFLICT');
  });
});

describe('createGroupViaExecute 经 groupTasks 扩展槽位委派（2026-11 collab-form Task 7A）', () => {
  // 全局 setup（test/setup/registerExtensionSlots.ts）注册的默认槽位对象：null 化用例后按原对象恢复，
  // 保证槽位改写不外泄（覆盖通道见 test/setup/registerExtensionSlots.ts 头注释）。
  const baseline = getExtensionSlots();
  const resetToCollabNull = () => {
    registerExtensionSlots({ groupTasks: null, groupServiceBotRouting: null, taskSessionNavigationObserver: null, taskPanelBootstrap: null, sessionTaskPanelTab: null });
  };
  const smInput = {
    name: '我的群',
    strategy: 'state_machine' as const,
    definitionYaml: 'participants:\n  - alpha\nroles:\n  - driver',
    driverBotUuid: 'b1',
    originator: 'actor-bot',
    participants: [{ actor_id: 'b1' }],
  };

  afterEach(() => {
    registerExtensionSlots(baseline);
  });

  it('槽位 null（collab 形态）返回 GROUP_EXECUTE_UNAVAILABLE：零任务引擎请求且不回落普通建群', async () => {
    const originalFetch = global.fetch;
    global.fetch = jest.fn<typeof fetch>();
    try {
      resetToCollabNull();

      const res = await groupService.createGroupViaExecute({ ...smInput }, 'u-1');

      expect(res).toEqual({
        ok: false,
        error: {
          code: 'GROUP_EXECUTE_UNAVAILABLE',
          friendlyMessage: '当前站点不支持以任务执行方式创建协作群。',
          canRetry: false,
        },
      });
      expect(gc.createGroup).not.toHaveBeenCalled();
      expect(global.fetch).not.toHaveBeenCalled();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('槽位在位时按 (input, ownerUserId, 回落普通建群) 委派执行器并透传其结果', async () => {
    const sentinel: DomainResult<GroupView> = { ok: true, data: { groupId: 'g-slot' } as never };
    const slotExecute = jest.fn<typeof createGroupViaExecute>().mockResolvedValue(sentinel);
    const stubTasks: GroupTasksExtension | null = baseline.groupTasks;
    if (!stubTasks) throw new Error('jest 全局 setup 应已注册非空 groupTasks 占位槽位');
    registerExtensionSlots({ ...baseline, groupTasks: { ...stubTasks, createGroupViaExecute: slotExecute } });

    const res = await groupService.createGroupViaExecute(smInput, 'u-1');

    expect(slotExecute).toHaveBeenCalledTimes(1);
    expect(slotExecute.mock.calls[0][0]).toMatchObject({ strategy: 'state_machine', name: '我的群' });
    expect(slotExecute.mock.calls[0][1]).toBe('u-1');
    expect(res).toBe(sentinel);
    // 回落函数接线普通建群链路：slot 收到的第三参直接调通 createGroupApi。
    gc.createGroup.mockResolvedValue({
      code: 20000, message: '', request_id: 'r', data: { group_id: 'g-plain', name: '我的群', participants: [] },
    });
    await slotExecute.mock.calls[0][2]({ ...smInput, strategy: 'chat' });
    expect(gc.createGroup).toHaveBeenCalledTimes(1);
  });

  it('Open 真实槽位（槽位内为既有 execute 建群器）：保留执行器守护与普通建群回落', async () => {
    const stubTasks: GroupTasksExtension | null = baseline.groupTasks;
    if (!stubTasks) throw new Error('jest 全局 setup 应已注册非空 groupTasks 占位槽位');
    registerExtensionSlots({
      ...baseline,
      groupTasks: { ...stubTasks, createGroupViaExecute: createGroupViaExecute },
    });

    // state_machine + 服务 Bot 路由 tags → 既有 executor 守护（GROUP_ROUTE_UNSUPPORTED，不回落）。
    const guarded = await groupService.createGroupViaExecute(
      { ...smInput, participants: [{ actor_id: 'b1', tags: ['verify'] }] },
      'u-1',
    );
    expect(guarded).toEqual({
      ok: false,
      error: expect.objectContaining({ code: 'GROUP_ROUTE_UNSUPPORTED' }),
    });
    expect(gc.createGroup).not.toHaveBeenCalled();

    // 非 state_machine → executor 内置回落 → 注入的 fallback 即普通 createGroup 链路。
    gc.createGroup.mockResolvedValue({
      code: 20000, message: '', request_id: 'r', data: { group_id: 'g-plain', name: '我的群', participants: [] },
    });
    gc.getGroup.mockResolvedValue({
      code: 20000, message: '', request_id: 'r',
      data: {
        group_id: 'g-plain', name: '我的群', status: 'active', visibility: 'private',
        originator_actor_id: 'actor-bot', participants: [], driver_bot_uuid: 'b1',
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
        created_at: 1, updated_at: 1,
      },
    });
    sc.listGroupSessions.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 50 } });
    const fellBack = await groupService.createGroupViaExecute({ ...smInput, strategy: 'chat' }, 'u-1');
    expect(gc.createGroup).toHaveBeenCalledTimes(1);
    expect(fellBack).toMatchObject({ ok: true, data: { groupId: 'g-plain' } });
  });
});

describe('human_mention_notify_mode（@Human 通知来源）', () => {
  const sessionsPage = { code: 20000, message: '', request_id: 'r', data: { items: [], total: 0, offset: 0, limit: 50 } };
  const detailWith = (mode: unknown, omit = false) => ({
    code: 20000,
    message: '',
    request_id: 'r',
    data: {
      group_id: 'g9',
      version: 1,
      kind: 'normal' as const,
      name: 'X',
      status: 'active' as const,
      visibility: 'private' as const,
      originator_actor_id: 'bot-1',
      participants: [],
      driver_bot_uuid: 'bot-1',
      collaboration: {
        strategy: 'chat' as const,
        delivery_policy: { bot_final_delivery: 'send_to_driver' as const },
      },
      created_at: 1,
      updated_at: 1,
      ...(omit ? {} : { human_mention_notify_mode: mode }),
    },
  });

  it.each(['all', 'driver_bot_only', 'none'] as const)(
    'GET 群详情将 human_mention_notify_mode=%s 映射进 GroupView.humanMentionNotifyMode',
    async (mode) => {
      gc.getGroup.mockResolvedValue(detailWith(mode));
      sc.listGroupSessions.mockResolvedValue(sessionsPage);

      const res = await groupService.loadGroupDetail('g9');

      expect(gc.getGroup).toHaveBeenCalledWith('g9');
      expect(res.ok && res.data.humanMentionNotifyMode).toBe(mode);
    },
  );

  it('旧版后端详情缺省该字段时映射 undefined（展示层按服务端存量默认 all 兜底）', async () => {
    gc.getGroup.mockResolvedValue(detailWith(undefined, true));
    sc.listGroupSessions.mockResolvedValue(sessionsPage);

    const res = await groupService.loadGroupDetail('g9');

    expect(res.ok && res.data.humanMentionNotifyMode).toBeUndefined();
  });

  it('详情返回未知枚举值不写入领域对象（防后端枚举漂移）', async () => {
    gc.getGroup.mockResolvedValue(detailWith('sometimes'));
    sc.listGroupSessions.mockResolvedValue(sessionsPage);

    const res = await groupService.loadGroupDetail('g9');

    expect(res.ok && res.data.humanMentionNotifyMode).toBeUndefined();
  });

  it.each(['all', 'driver_bot_only', 'none'] as const)(
    'PATCH 仅携带 human_mention_notify_mode=%s（未提交字段省略，服务端保持原值）',
    async (mode) => {
      gc.updateGroup.mockResolvedValue(detailWith(mode));
      gc.getGroup.mockResolvedValue(detailWith(mode));
      sc.listGroupSessions.mockResolvedValue(sessionsPage);

      const res = await groupService.updateGroup('g9', { humanMentionNotifyMode: mode });

      expect(gc.updateGroup).toHaveBeenCalledTimes(1);
      expect(gc.updateGroup).toHaveBeenCalledWith('g9', { human_mention_notify_mode: mode });
      expect(res.ok && res.data.humanMentionNotifyMode).toBe(mode);
    },
  );

  it('PATCH 群名称时不携带通知来源（省略即保持服务端原值）', async () => {
    gc.updateGroup.mockResolvedValue(detailWith('none'));
    gc.getGroup.mockResolvedValue(detailWith('none'));
    sc.listGroupSessions.mockResolvedValue(sessionsPage);

    await groupService.updateGroup('g9', { name: '新群名' });

    expect(gc.updateGroup).toHaveBeenCalledWith('g9', { name: '新群名' });
  });

  it('PATCH 409 映射 GROUP_CONFLICT（沿用既有运行时错误处理）', async () => {
    gc.updateGroup.mockRejectedValue({ status: 409 });

    const res = await groupService.updateGroup('g9', { humanMentionNotifyMode: 'none' });

    expect(res.ok).toBe(false);
    expect((res as { error: { code: string } }).error.code).toBe('GROUP_CONFLICT');
  });
});
