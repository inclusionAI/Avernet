/** @jest-environment node */
import * as invitationController from '@/services/backendApi/collaboration/collaborationInvitationController';
import type { DomainError } from '@/services/workspace/identityService';
import { invitationService } from '@/services/workspace/invitationService';
import { describe, expect, it, jest } from '@jest/globals';

// 使用 auto-mock（不带 factory），避免在 hoisted factory 内引用 jest.fn() —— 与 @jest/globals 一起会触发 TDZ。
// auto-mock 会把 createGroupInvitation/createSessionInvitation/acceptInvitation 替成 jest.fn()，下方强取即可。
jest.mock('@/services/backendApi/collaboration/collaborationInvitationController');
const ic = invitationController as unknown as Record<string, jest.Mock<any>>;
type FailureResult = { ok: false; error: DomainError };

describe('invitationService', () => {
  it('createGroupShare returns invitationUrl with token', async () => {
    ic.createGroupInvitation.mockResolvedValue({
      code: 20100,
      message: '',
      request_id: 'r',
      data: {
        token: 'tk',
        target_type: 'group',
        target_id: 'g1',
        state: 'pending',
        created_at: 1,
      },
    });
    const res = await invitationService.createGroupShare('g1');
    expect(res.ok && res.data.invitationUrl).toBe('http://localhost:8000/workspace/invite/tk?type=group');
  });

  it('createSessionShare uses session invitation and returns a token-only url', async () => {
    ic.createSessionInvitation.mockResolvedValue({
      code: 20100,
      message: '',
      request_id: 'r',
      data: {
        token: 'tk',
        target_type: 'session',
        target_id: 's9',
        state: 'pending',
        created_at: 1,
      },
    });
    const res = await invitationService.createSessionShare('s9');
    expect(ic.createSessionInvitation).toHaveBeenCalledWith('s9', { expires_in_seconds: 86400 });
    expect(res.ok && res.data.invitationUrl).toBe('http://localhost:8000/workspace/invite/tk?type=session');
  });

  it('acceptInvitation success → ok true, 410 → invalid friendlyMessage', async () => {
    ic.acceptInvitation
      .mockResolvedValueOnce({
        code: 20000,
        message: '',
        request_id: 'r',
        data: { target_type: 'group', target_id: 'g1', joined: true, already_joined: false },
      })
      .mockRejectedValueOnce({ status: 410, message: 'expired' });
    const ok = await invitationService.acceptInvitation('tk');
    expect(ok).toEqual({ ok: true, data: { targetType: 'group', targetId: 'g1', alreadyJoined: false } });
    const bad = await invitationService.acceptInvitation('tk2');
    expect(bad.ok).toBe(false);
    expect(!bad.ok && bad.error.friendlyMessage).toContain('失效');
  });

  it('acceptInvitation 409 alreadyJoined → ok true with alreadyJoined true', async () => {
    // 后端在已加入时返回 409，会以 thrown error 形式冒泡；按 status 归一为「已加入」成功。
    ic.acceptInvitation.mockRejectedValueOnce({ status: 409, message: 'already joined' });
    const res = await invitationService.acceptInvitation('tk');
    expect(res).toEqual({ ok: true, data: { targetType: null, targetId: '', alreadyJoined: true } });
  });

  it('acceptInvitation 401/403 → unauthenticated friendlyMessage', async () => {
    ic.acceptInvitation.mockRejectedValueOnce({ status: 401, message: 'no auth' });
    const res = await invitationService.acceptInvitation('tk');
    expect(res.ok).toBe(false);
    expect(!res.ok && res.error.friendlyMessage).toContain('登录');
    expect(!res.ok && res.error.code).toBe('INVITATION_UNAUTHENTICATED');
  });

  it('acceptInvitation 400/404 → invalid friendlyMessage', async () => {
    ic.acceptInvitation.mockRejectedValueOnce({ status: 404, message: 'not found' });
    const res = await invitationService.acceptInvitation('tk');
    expect((res as FailureResult).error.code).toBe('INVITATION_INVALID');
  });

  it('getAcceptPageState never calls backend before user confirms', async () => {
    ic.acceptInvitation.mockClear();
    ic.createGroupInvitation.mockClear();
    const res = await invitationService.getAcceptPageState('tk');
    expect(res).toEqual({ ok: true, data: { isValid: true } });
    expect(ic.acceptInvitation).not.toHaveBeenCalled();
    expect(ic.createGroupInvitation).not.toHaveBeenCalled();
  });

  it('getAcceptPageState rejects empty token as invalid', async () => {
    ic.acceptInvitation.mockClear();
    ic.createGroupInvitation.mockClear();
    const res = await invitationService.getAcceptPageState('');
    expect(res.ok).toBe(false);
    expect((res as FailureResult).error.code).toBe('INVITATION_INVALID');
    expect(ic.acceptInvitation).not.toHaveBeenCalled();
  });
});
