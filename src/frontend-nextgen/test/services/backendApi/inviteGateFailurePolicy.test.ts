import { resolveInviteGateDisposition } from '@/services/backendApi/inviteGateFailurePolicy';
import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';

/**
 * 邀请码门禁失败处置决策（add-invite-code-gate）：oauth-provider 策略下命中后端 gate 专属
 * `error_code='invite_code_required'`（HTTP 403 或网关误包 2xx）→ 单飞门禁弹窗信号 + `gate-prompt-silent`；
 * ace-gateway / 非 invite_code_required（含通用 'forbidden'、真实权限拒绝）→ `default`，不被误吞。
 * 识别键严格限定专属 error_code，是区别于登录 401 与通用 403 的支点。
 */
describe('resolveInviteGateDisposition', () => {
  beforeEach(() => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    useInviteCodeGateStore.getState().reset();
  });

  afterEach(() => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    useInviteCodeGateStore.getState().reset();
  });

  it('HTTP 403 + error_code=invite_code_required → 单飞弹窗信号 + gate-prompt-silent', () => {
    expect(
      resolveInviteGateDisposition({
        status: 403,
        data: { code: 40300, message: 'x', data: { error_code: 'invite_code_required' } },
      }),
    ).toBe('gate-prompt-silent');
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(true);
  });

  it('网关误包 HTTP 200 + 信封 invite_code_required → gate-prompt-silent(不当作业务成功)', () => {
    expect(
      resolveInviteGateDisposition({
        status: 200,
        data: { code: 40300, data: { error_code: 'invite_code_required' }, request_id: 'r' },
      }),
    ).toBe('gate-prompt-silent');
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(true);
  });

  it('非 invite_code_required 的 403(通用 forbidden)→ default,不弹门禁、不吞提示', () => {
    expect(
      resolveInviteGateDisposition({
        status: 403,
        data: { code: 40300, data: { error_code: 'forbidden' } },
      }),
    ).toBe('default');
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('403 无 error_code → default(不误判为门禁)', () => {
    expect(resolveInviteGateDisposition({ status: 403, data: { message: 'forbidden' } })).toBe('default');
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('成功信封(2xx code)→ default,不触发门禁', () => {
    expect(resolveInviteGateDisposition({ status: 200, data: { code: 20000, data: { bound: false } } })).toBe(
      'default',
    );
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('非对象/null data → default', () => {
    expect(resolveInviteGateDisposition({ status: 403, data: null })).toBe('default');
    expect(resolveInviteGateDisposition({ status: 403 })).toBe('default');
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('ace-gateway 策略 → 一律 default(门禁完全不激活,403 既有处置不变)', () => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    expect(
      resolveInviteGateDisposition({
        status: 403,
        data: { code: 40300, data: { error_code: 'invite_code_required' } },
      }),
    ).toBe('default');
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('单飞:已 pending 时调用仍返回 gate-prompt-silent 但不重复置位(幂等,首信号胜出)', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    expect(resolveInviteGateDisposition({ status: 403, data: { data: { error_code: 'invite_code_required' } } })).toBe(
      'gate-prompt-silent',
    );
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(true);
  });
});
