import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

describe('inviteCodeGateStore', () => {
  beforeEach(() => {
    useInviteCodeGateStore.getState().reset();
  });

  it('初始:pendingPrompt=false / binding=null / submitting=false / submitError=null', () => {
    const s = useInviteCodeGateStore.getState();
    expect(s.pendingPrompt).toBe(false);
    expect(s.binding).toBeNull();
    expect(s.submitting).toBe(false);
    expect(s.submitError).toBeNull();
  });

  it('requestPrompt 置 pendingPrompt=true', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(true);
  });

  it('requestPrompt 单飞:已 pending 时 no-op(幂等,首信号胜出)', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    useInviteCodeGateStore.getState().requestPrompt();
    useInviteCodeGateStore.getState().requestPrompt();
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(true);
  });

  it('clearPrompt 清 pendingPrompt', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    useInviteCodeGateStore.getState().clearPrompt();
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it('setBinding / setSubmitting / setSubmitError 状态输入输出', () => {
    const store = useInviteCodeGateStore.getState();
    store.setBinding({ bound: true, bound_at: 1730000000 });
    store.setSubmitting(true);
    store.setSubmitError({ code: 'invite_code_unavailable', message: '邀请码无效' });
    const s = useInviteCodeGateStore.getState();
    expect(s.binding).toEqual({ bound: true, bound_at: 1730000000 });
    expect(s.submitting).toBe(true);
    expect(s.submitError).toEqual({ code: 'invite_code_unavailable', message: '邀请码无效' });
  });

  it('setSubmitError(null) 清错误', () => {
    useInviteCodeGateStore.getState().setSubmitError({ code: 'unknown', message: 'x' });
    useInviteCodeGateStore.getState().setSubmitError(null);
    expect(useInviteCodeGateStore.getState().submitError).toBeNull();
  });

  it('reset 回到初始态', () => {
    const store = useInviteCodeGateStore.getState();
    store.requestPrompt();
    store.setSubmitting(true);
    store.setSubmitError({ code: 'unknown', message: 'x' });
    useInviteCodeGateStore.getState().reset();
    const s = useInviteCodeGateStore.getState();
    expect(s.pendingPrompt).toBe(false);
    expect(s.submitting).toBe(false);
    expect(s.submitError).toBeNull();
    expect(s.binding).toBeNull();
  });
});
