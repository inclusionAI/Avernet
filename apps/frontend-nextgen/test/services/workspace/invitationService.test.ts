/** @jest-environment node */
import * as invitationController from '@/services/backendApi/collaboration/collaborationInvitationController';
import type { DomainError } from '@/services/workspace/identityService';
import { invitationService } from '@/services/workspace/invitationService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

// 使用 auto-mock（不带 factory），避免在 hoisted factory 内引用 jest.fn() —— 与 @jest/globals 一起会触发 TDZ。
// auto-mock 会把 createGroupInvitation/createSessionInvitation/acceptInvitation 替成 jest.fn()，下方强取即可。
jest.mock('@/services/backendApi/collaboration/collaborationInvitationController');
const ic = invitationController as unknown as Record<string, jest.Mock<any>>;
type FailureResult = { ok: false; error: DomainError };

describe('invitationService', () => {
  beforeEach(() => { jest.clearAllMocks(); useErrorNotifyStore.getState().reset(); });
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
    expect(res.ok && res.data.invitationUrl).toBe('http://localhost:8000/workspace/invite/groups/tk');
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
    expect(res.ok && res.data.invitationUrl).toBe('http://localhost:8000/workspace/invite/sessions/tk');
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

  it.each(['group', 'session'] as const)('getAcceptPageState reads %s membership without accepting', async (type) => {
    ic.previewInvitation.mockResolvedValueOnce({
      code: 20000,
      data: { target_type: type, target_id: type === 'group' ? 'g1' : 's1', group_id: 'g1', already_joined: true },
    });
    const res = await invitationService.getAcceptPageState('tk');
    expect(ic.previewInvitation).toHaveBeenCalledWith('tk');
    expect(res).toEqual({ ok: true, data: {
      isValid: true, targetType: type, targetId: type === 'group' ? 'g1' : 's1', groupId: 'g1', alreadyJoined: true,
    } });
    expect(ic.acceptInvitation).not.toHaveBeenCalled();
    expect(ic.createGroupInvitation).not.toHaveBeenCalled();
  });

  it.each([400, 403, 410, 500])('preflight %s cancels only its default notification before inline handling', async (status) => {
    const notify = useErrorNotifyStore.getState();
    notify.enqueue({ toastKey: 'invitation-preview', message: 'preview failed' });
    notify.enqueue({ toastKey: 'unrelated-request', message: 'other failure' });
    ic.previewInvitation.mockRejectedValueOnce({ status, toastKey: 'invitation-preview' });
    const result = await invitationService.getAcceptPageState('tk');
    expect(result.ok).toBe(false);
    expect(useErrorNotifyStore.getState().queue).toEqual([
      expect.objectContaining({ toastKey: 'invitation-preview', cancelled: true }),
      expect.objectContaining({ toastKey: 'unrelated-request' }),
    ]);
    expect(useErrorNotifyStore.getState().queue[1].cancelled).not.toBe(true);
    expect(ic.acceptInvitation).not.toHaveBeenCalled();
  });

  it.each([400, 404, 410])('preflight %s remains invalid, not a nonmember result', async (status) => {
    ic.previewInvitation.mockRejectedValueOnce({ status });
    expect(await invitationService.getAcceptPageState('tk')).toMatchObject({
      ok: false, error: { code: 'INVITATION_INVALID', canRetry: false },
    });
  });

  it.each([401, 403])('preflight %s remains an authentication failure', async (status) => {
    ic.previewInvitation.mockRejectedValueOnce({ status });
    expect(await invitationService.getAcceptPageState('tk')).toMatchObject({
      ok: false, error: { code: 'INVITATION_UNAUTHENTICATED', canRetry: false },
    });
  });

  it('transient preview failure can be retried rather than confirming an unchecked invitation', async () => {
    ic.previewInvitation.mockRejectedValueOnce({ status: 500 });
    expect(await invitationService.getAcceptPageState('tk')).toMatchObject({
      ok: false, error: { code: 'INVITATION_PREVIEW_FAILED', canRetry: true },
    });
  });

  it.each([
    undefined,
    { target_type: 'group', target_id: 'g1', group_id: 'g1' },
    { target_type: 'session', target_id: 's1', already_joined: false },
    { target_type: 'bot', target_id: 'b1', group_id: 'g1', already_joined: true },
  ])('rejects incomplete preview data instead of guessing membership', async (data) => {
    ic.previewInvitation.mockResolvedValueOnce({ code: 20000, data });
    expect(await invitationService.getAcceptPageState('tk')).toMatchObject({
      ok: false, error: { code: 'INVITATION_PREVIEW_FAILED' },
    });
  });

  it('getAcceptPageState rejects empty token as invalid', async () => {
    ic.acceptInvitation.mockClear();
    ic.createGroupInvitation.mockClear();
    const res = await invitationService.getAcceptPageState('');
    expect(res.ok).toBe(false);
    expect((res as FailureResult).error.code).toBe('INVITATION_INVALID');
    expect(ic.acceptInvitation).not.toHaveBeenCalled();
    expect(ic.previewInvitation).not.toHaveBeenCalled();
  });
});
