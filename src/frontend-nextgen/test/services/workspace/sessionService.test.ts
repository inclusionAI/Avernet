/** @jest-environment jsdom */
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';

// 使用 auto-mock（不带 factory），避免在 hoisted factory 内引用 jest.fn() —— 与 @jest/globals 一起会触发 TDZ。
// auto-mock 会把 createSession/updateSession/deleteSession/listGroupSessions 替成 jest.fn()，下方强取即可。
jest.mock('@/services/backendApi/collaboration/sessionController');
const sc = sessionController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
  window.localStorage.clear();
});

it('createNewSession maps to SessionView with favorite false', async () => {
  sc.createSession.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: {
      session_id: 'g1:s9',
      group_id: 'g1',
      title: '新会话',
      status: 'running',
      participants: [],
      created_at: 1,
      updated_at: 2,
    },
  });
  const res = await sessionService.createNewSession('g1', '新会话');
  expect(res.ok && res.data).toMatchObject({
    sessionId: 'g1:s9',
    groupId: 'g1',
    title: '新会话',
    favorite: false,
  });
  // acting_bot_id 取当前角色 id（创建请求时 activeIdentityId 为空则不传）。
  expect(sc.createSession).toHaveBeenCalledWith('g1', { title: '新会话', input: undefined });
});

it('createNewSession 以 activeIdentityId 作为 acting_bot_id 传递当前角色', async () => {
  useWorkspaceStore.getState().reset();
  useWorkspaceStore
    .getState()
    .setIdentities([{ id: 'human_327325', kind: 'user', displayName: '当前用户', online: true }], 'human_327325');
  try {
    sc.createSession.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        session_id: 'g1:s10',
        group_id: 'g1',
        title: '',
        status: 'running',
        participants: [],
        created_at: 1,
        updated_at: 2,
      },
    });
    await sessionService.createNewSession('g1', '新会话', '协作目标');
    expect(sc.createSession).toHaveBeenCalledWith('g1', {
      title: '新会话',
      input: { query: '协作目标' },
      acting_bot_id: 'human_327325',
    });
  } finally {
    useWorkspaceStore.getState().reset();
  }
});

it('setFavorite true calls collect API and returns collected status', async () => {
  sc.collectSession.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { collected: true },
  });
  const res = await sessionService.setFavorite('me', 's1', true);
  expect(res.ok && res.data).toBe(true);
  expect(sc.collectSession).toHaveBeenCalledWith('s1', { participant: 'me' });
});

it('setFavorite false calls uncollect API and returns uncollected status', async () => {
  sc.uncollectSession.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { collected: false },
  });
  const res = await sessionService.setFavorite('me', 's1', false);
  expect(res.ok && res.data).toBe(false);
  expect(sc.uncollectSession).toHaveBeenCalledWith('s1', { participant: 'me' });
});

it('getVisibleSessions filters by tab and search, sorts by lastMessageAt desc', () => {
  const sessions = [
    {
      sessionId: 's1',
      groupId: 'g',
      title: 'hello',
      kind: 'chat',
      status: 'running',
      participants: [],
      lastMessageAt: 3,
      createdAt: 1,
      favorite: false,
    },
    {
      sessionId: 's2',
      groupId: 'g',
      title: 'World',
      kind: 'chat',
      status: 'running',
      participants: [],
      lastMessageAt: 8,
      createdAt: 2,
      favorite: false,
    },
  ] as any;
  expect(
    sessionService.getVisibleSessions(sessions, { tab: 'all', search: 'world', favorites: [] }).map((s) => s.sessionId),
  ).toEqual(['s2']);
  expect(
    sessionService
      .getVisibleSessions(sessions, { tab: 'favorite', search: '', favorites: ['s1'] })
      .map((s) => s.sessionId),
  ).toEqual(['s1']);
  expect(
    sessionService.getVisibleSessions(sessions, { tab: 'all', search: '', favorites: [] }).map((s) => s.sessionId),
  ).toEqual(['s2', 's1']);
});

it('deleteSession maps 409 to friendly conflict', async () => {
  sc.deleteSession.mockRejectedValue({ status: 409 });
  const res = await sessionService.deleteSession('s1');
  expect(res.ok).toBe(false);
  expect(!res.ok && res.error.friendlyMessage).toContain('已变更');
});

const memberDetail = {
  session_id: 's1',
  group_id: 'g1',
  title: '会话',
  status: 'running',
  participants: [
    { actor_id: 'b1', actor_kind: 'bot', name: 'Alpha', role: 'driver', mode: 'muted' },
    { actor_id: 'human_1', actor_kind: 'human', name: '章梧', role: 'consultant', mode: 'present' },
  ],
  created_at: 1,
  updated_at: 2,
};

it('updateMemberMode PATCHes mode and returns refreshed session with participants', async () => {
  sc.updateSessionMemberMode.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: {
      actor_id: 'b1',
      actor_kind: 'bot',
      name: 'Alpha',
      role: 'driver',
      mode: 'muted',
    },
  });
  sc.getSession.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: memberDetail,
  });
  const res = await sessionService.updateMemberMode('s1', 'b1', 'muted');
  expect(sc.updateSessionMemberMode).toHaveBeenCalledWith('s1', 'b1', { mode: 'muted' });
  expect(sc.getSession).toHaveBeenCalledWith('s1');
  expect(res.ok && res.data.participants).toEqual([
    expect.objectContaining({ actorId: 'b1', mode: 'muted' }),
    expect.objectContaining({ actorId: 'human_1', mode: 'present' }),
  ]);
});

it('updateMemberMode maps failure to friendly error', async () => {
  sc.updateSessionMemberMode.mockRejectedValue(new Error('boom'));
  const res = await sessionService.updateMemberMode('s1', 'human_1', 'present');
  expect(res.ok).toBe(false);
  expect(!res.ok && res.error.friendlyMessage).toContain('更新会话成员状态失败');
});

it('getSessionDetail returns SessionView with participants', async () => {
  sc.getSession.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: {
      session_id: 's1',
      group_id: 'g1',
      title: '会话',
      status: 'running',
      participants: [
        { actor_id: 'b1', actor_kind: 'bot', name: 'Alpha', role: 'driver', mode: 'muted' },
        { actor_id: 'human_1', actor_kind: 'human', name: '章梧', role: 'consultant', mode: 'present' },
      ],
      created_at: 1,
      updated_at: 2,
    },
  });
  const res = await sessionService.getSessionDetail('s1');
  expect(sc.getSession).toHaveBeenCalledWith('s1');
  expect(res.ok && res.data.participants).toEqual([
    expect.objectContaining({ actorId: 'b1', mode: 'muted' }),
    expect.objectContaining({ actorId: 'human_1', mode: 'present' }),
  ]);
});

it('addMember POSTs participant and returns refreshed session', async () => {
  sc.addSessionParticipant.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { actor_id: 'bot-2', actor_kind: 'bot', name: 'Beta', role: 'member', mode: 'present' },
  });
  sc.getSession.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: memberDetail,
  });
  const res = await sessionService.addMember('s1', 'bot-2');
  expect(sc.addSessionParticipant).toHaveBeenCalledWith('s1', 'bot-2');
  expect(sc.getSession).toHaveBeenCalledWith('s1');
  expect(res.ok && res.data.participants).toEqual([
    expect.objectContaining({ actorId: 'b1' }),
    expect.objectContaining({ actorId: 'human_1' }),
  ]);
});

it('removeMember DELETEs participant and returns refreshed session', async () => {
  sc.deleteSessionParticipant.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { deleted: true },
  });
  sc.getSession.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: memberDetail,
  });
  const res = await sessionService.removeMember('s1', 'bot-2');
  expect(sc.deleteSessionParticipant).toHaveBeenCalledWith('s1', 'bot-2');
  expect(sc.getSession).toHaveBeenCalledWith('s1');
  expect(res.ok).toBe(true);
});

it('leaveSession maps 409 to friendly conflict', async () => {
  sc.deleteSessionParticipant.mockRejectedValue({ status: 409 });
  const res = await sessionService.leaveSession('s1', 'human_1');
  expect(res.ok).toBe(false);
  expect(!res.ok && res.error.code).toBe('SESSION_CONFLICT');
});
