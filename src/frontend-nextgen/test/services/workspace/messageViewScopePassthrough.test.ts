import * as invitationController from '@/services/backendApi/collaboration/collaborationInvitationController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { buildCreateGroupBody } from '@/services/workspace/groupCreateRequest';
import { invitationService } from '@/services/workspace/invitationService';
import { sessionService } from '@/services/workspace/sessionService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@/services/backendApi/collaboration/collaborationInvitationController');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const sc = sessionController as unknown as Record<string, jest.Mock<any>>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ic = invitationController as unknown as Record<string, jest.Mock<any>>;

const sessionDto = {
  session_id: 's1',
  group_id: 'g1',
  title: '会话',
  status: 'running',
  participants: [],
  created_at: 1,
  updated_at: 1,
};

beforeEach(() => {
  jest.clearAllMocks();
  sc.createSession.mockResolvedValue({ code: 20100, message: '', request_id: 'r', data: sessionDto });
  sc.getSession.mockResolvedValue({ code: 20000, message: '', request_id: 'r', data: sessionDto });
  sc.updateSessionMemberMode.mockResolvedValue({ code: 20000, message: '', request_id: 'r', data: null });
  ic.acceptInvitation.mockResolvedValue({
    code: 20000,
    message: '',
    request_id: 'r',
    data: { target_type: 'group', target_id: 'g1', joined: true },
  });
});

describe('createNewSession 透传 message_view_scope', () => {
  it('传 scope 时请求体携带 message_view_scope', async () => {
    await sessionService.createNewSession('g1', undefined, undefined, 'participant');
    const body = sc.createSession.mock.calls[0][1] as Record<string, unknown>;
    expect(body.message_view_scope).toBe('participant');
  });

  it('不传 scope 时请求体不出现该键（后端继承/默认规则生效）', async () => {
    await sessionService.createNewSession('g1');
    const body = sc.createSession.mock.calls[0][1] as Record<string, unknown>;
    expect(Object.prototype.hasOwnProperty.call(body, 'message_view_scope')).toBe(false);
  });
});

describe('updateMemberMode 透传 message_view_scope', () => {
  it('传 scope 时 PATCH body 同时携带 mode 与 scope', async () => {
    await sessionService.updateMemberMode('s1', 'human_1', 'present', 'participant');
    expect(sc.updateSessionMemberMode).toHaveBeenCalledWith('s1', 'human_1', {
      mode: 'present',
      message_view_scope: 'participant',
    });
  });

  it('不传 scope 时 body 仅含 mode（旧调用不变）', async () => {
    await sessionService.updateMemberMode('s1', 'b1', 'muted');
    expect(sc.updateSessionMemberMode).toHaveBeenCalledWith('s1', 'b1', { mode: 'muted' });
  });
});

describe('acceptInvitation 透传 message_view_scope', () => {
  it('传 scope 时 body 携带字段', async () => {
    await invitationService.acceptInvitation('tk', 'participant');
    expect(ic.acceptInvitation).toHaveBeenCalledWith('tk', { message_view_scope: 'participant' });
  });

  it('不传 scope 时第二参为 undefined（wire body 为 {}）', async () => {
    await invitationService.acceptInvitation('tk');
    expect(ic.acceptInvitation).toHaveBeenCalledWith('tk', undefined);
  });
});

describe('buildCreateGroupBody 将 scope 挂在指定参与者条目', () => {
  it('human 条目携带 message_view_scope，bot 条目不携带', () => {
    const body = buildCreateGroupBody({
      name: '群',
      strategy: 'chat',
      driverBotUuid: 'bot_1',
      originator: 'human_1',
      participants: [{ actor_id: 'human_1', message_view_scope: 'participant' }, { actor_id: 'bot_1' }],
    });
    expect(body.participants[0]).toEqual({
      actor_id: 'human_1',
      role: 'consultant',
      message_view_scope: 'participant',
    });
    expect(Object.prototype.hasOwnProperty.call(body.participants[1], 'message_view_scope')).toBe(false);
  });
});
