import {
  bindInviteCode,
  friendlyInviteCodeMessage,
  getMyInviteCodeBinding,
  InviteCodeServiceError,
  normalizeCode,
} from '@/services/collaboration/inviteCodeService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

const successResponse = (data: unknown) =>
  Promise.resolve({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  } as Response);

const failingResponse = (status: number, data: unknown) =>
  Promise.resolve({
    ok: false,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  } as Response);

const envelope = (code: number, data: unknown, message = 'OK') => ({ code, message, data, request_id: 'r-1' });

describe('inviteCodeService', () => {
  beforeEach(() => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    useExternalAuthStore.getState().reset();
    useErrorNotifyStore.getState().reset();
    useInviteCodeGateStore.getState().reset();
  });

  afterEach(() => {
    jest.restoreAllMocks();
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
  });

  describe('normalizeCode', () => {
    it('移除所有空白并转大写（后端 normalize_code 兜底，两端一致）', () => {
      expect(normalizeCode(' abc 123 ')).toBe('ABC123');
      expect(normalizeCode('abc123')).toBe('ABC123');
      expect(normalizeCode('  ')).toBe('');
      expect(normalizeCode('')).toBe('');
    });
  });

  describe('friendlyInviteCodeMessage', () => {
    it('每个标准化错误码映射到友好文案', () => {
      expect(friendlyInviteCodeMessage('invite_code_unavailable')).toBe('邀请码无效，请检查后重试');
      expect(friendlyInviteCodeMessage('invite_code_already_bound')).toBe('已绑定其他邀请码，请联系管理员');
      expect(friendlyInviteCodeMessage('invalid_request')).toBe('请输入邀请码');
      expect(friendlyInviteCodeMessage('unknown')).toBe('邀请码提交失败，请稍后重试');
    });
  });

  describe('getMyInviteCodeBinding', () => {
    it('已绑定：解包 5 位码信封 data（含 bound_at）', async () => {
      const spy = jest
        .spyOn(globalThis, 'fetch')
        .mockImplementation(() => successResponse(envelope(20000, { bound: true, bound_at: 1730000000 })));
      const view = await getMyInviteCodeBinding();
      expect(view).toEqual({ bound: true, bound_at: 1730000000 });
      expect(spy).toHaveBeenCalledWith(
        '/openapi/v1/collaboration/invite-codes/me',
        expect.objectContaining({ method: 'GET' }),
      );
    });

    it('未绑定：解包 { bound:false }（无 bound_at）', async () => {
      jest.spyOn(globalThis, 'fetch').mockImplementation(() => successResponse(envelope(20000, { bound: false })));
      const view = await getMyInviteCodeBinding();
      expect(view).toEqual({ bound: false });
      expect(view.bound_at).toBeUndefined();
    });

    it('失败（500）→ InviteCodeServiceError code=unknown（标准化上抛，不静默吞）', async () => {
      jest.spyOn(globalThis, 'fetch').mockImplementation(() => failingResponse(500, { code: 50000, message: 'boom' }));
      await expect(getMyInviteCodeBinding()).rejects.toBeInstanceOf(InviteCodeServiceError);
      await expect(getMyInviteCodeBinding()).rejects.toMatchObject({ code: 'unknown' });
    });
  });

  describe('bindInviteCode', () => {
    it('成功：发送清洗后的 code，解包 { bound:true, bound_at }', async () => {
      const spy = jest
        .spyOn(globalThis, 'fetch')
        .mockImplementation(() => successResponse(envelope(20000, { bound: true, bound_at: 1730000000 })));
      const result = await bindInviteCode(' abc 123 ');
      expect(result).toEqual({ bound: true, bound_at: 1730000000 });
      expect(spy).toHaveBeenCalledWith(
        '/openapi/v1/collaboration/invite-codes/bind',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({ code: 'ABC123' }) }),
      );
    });

    it('无效码（409 invite_code_unavailable）→ InviteCodeServiceError code=invite_code_unavailable', async () => {
      jest
        .spyOn(globalThis, 'fetch')
        .mockImplementation(() =>
          failingResponse(409, envelope(40900, { error_code: 'invite_code_unavailable' }, 'x')),
        );
      await expect(bindInviteCode('ZZZ999')).rejects.toMatchObject({ code: 'invite_code_unavailable' });
    });

    it('已绑不同码（409 invite_code_already_bound）→ InviteCodeServiceError code=invite_code_already_bound', async () => {
      jest
        .spyOn(globalThis, 'fetch')
        .mockImplementation(() =>
          failingResponse(409, envelope(40900, { error_code: 'invite_code_already_bound' }, 'x')),
        );
      await expect(bindInviteCode('ABC123')).rejects.toMatchObject({ code: 'invite_code_already_bound' });
    });

    it('空码经清洗后仍发送（后端兜底 400 invalid_request）→ InviteCodeServiceError code=invalid_request', async () => {
      jest
        .spyOn(globalThis, 'fetch')
        .mockImplementation(() =>
          failingResponse(400, envelope(40000, { error_code: 'invalid_request' }, 'code must not be empty')),
        );
      await expect(bindInviteCode('   ')).rejects.toMatchObject({ code: 'invalid_request' });
    });

    it('未知错误（500）→ InviteCodeServiceError code=unknown', async () => {
      jest.spyOn(globalThis, 'fetch').mockImplementation(() => failingResponse(500, { code: 50000, message: 'boom' }));
      await expect(bindInviteCode('ABC123')).rejects.toMatchObject({ code: 'unknown' });
    });
  });
});
