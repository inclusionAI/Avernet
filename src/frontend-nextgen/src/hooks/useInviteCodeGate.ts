import { getCapabilities } from '@/capabilities';
import {
  bindInviteCode,
  friendlyInviteCodeMessage,
  getMyInviteCodeBinding,
  InviteCodeServiceError,
  normalizeCode,
} from '@/services/collaboration/inviteCodeService';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { reloadCurrentTab } from '@/utils/redirectCurrentTab';
import { useCallback, useEffect, useRef } from 'react';

/** 门禁是否激活：oauth-provider 策略 + inviteCodeGate capability enabled 双门控（ace-gateway/disabled 不激活）。 */
function isGateActive(): boolean {
  return (
    useLoginStrategyStore.getState().loginStrategy === 'oauth-provider' &&
    getCapabilities().getInviteCodeGatePolicy().value === 'enabled'
  );
}

/**
 * 主动检测当前登录 Human 的邀请码绑定状态（进布局、登录态确认后调用）。
 * `bound:false` → 单飞登记门禁弹窗信号；`bound:true` → 不动；错误**静默吞**（dev mock 未配 `/me` 的 404、
 * 网络错误等，主动检测是 best-effort，反应式 403 兜底会兜），不误弹门禁、不报 toast。
 */
export async function checkInviteCodeBinding(): Promise<void> {
  if (!isGateActive()) return;
  try {
    const view = await getMyInviteCodeBinding();
    if (!view.bound) {
      useInviteCodeGateStore.getState().requestPrompt();
    }
  } catch {
    // 静默：主动检测失败不弹门禁、不报 toast（反应式 403 兜底；dev mock 404 不噪音）。
  }
}

/** 弹窗内联错误态形状（与 store `InviteCodeSubmitError` 同构，`code` 为标准化错误码字符串）。 */
export interface InviteCodeSubmitErrorView {
  code: string;
  message: string;
}

/**
 * 邀请码门禁提交编排。供 `InviteCodeBindingModal` 消费（提交邀请码 + 提交中态 + 内联错误态 + 清除错误）。
 * 成功 → 置绑定 + 清 prompt + `window.location.reload()` 回流（重载后 `/me` 查得 bound:true 不再弹、业务请求重发即成）。
 * 失败（`invite_code_unavailable`/`invite_code_already_bound`/`invalid_request`/空输入）→ 内联错误态，弹窗保持打开。
 */
export function useInviteCodeGate(): {
  submitCode: (rawCode: string) => Promise<void>;
  submitting: boolean;
  submitError: InviteCodeSubmitErrorView | null;
  clearSubmitError: () => void;
} {
  const submitting = useInviteCodeGateStore((s) => s.submitting);
  const submitError = useInviteCodeGateStore((s) => s.submitError);

  const submitCode = useCallback(async (rawCode: string): Promise<void> => {
    const store = useInviteCodeGateStore.getState();
    const code = normalizeCode(rawCode);
    if (!code) {
      store.setSubmitError({ code: 'invalid_request', message: friendlyInviteCodeMessage('invalid_request') });
      return;
    }
    store.setSubmitting(true);
    store.setSubmitError(null);
    try {
      const result = await bindInviteCode(code);
      useInviteCodeGateStore.getState().setBinding({ bound: result.bound, bound_at: result.bound_at });
      useInviteCodeGateStore.getState().clearPrompt();
      // 回流：整页 reload 当前 URL（经 reloadCurrentTab 测试接缝，jsdom 可 mock）。bcs_session cookie 仍在、
      // 登录态不变；重载后主动 /me 查得 bound:true 不再弹、被阻断的业务请求重发即成。
      reloadCurrentTab();
    } catch (error) {
      const errorCode = error instanceof InviteCodeServiceError ? error.code : 'unknown';
      useInviteCodeGateStore.getState().setSubmitError({
        code: errorCode,
        message: friendlyInviteCodeMessage(errorCode),
      });
    } finally {
      useInviteCodeGateStore.getState().setSubmitting(false);
    }
  }, []);

  const clearSubmitError = useCallback(() => {
    useInviteCodeGateStore.getState().setSubmitError(null);
  }, []);

  return { submitCode, submitting, submitError, clearSubmitError };
}

/**
 * 全系统邀请码门禁主动 boot（仅 `oauth-provider` + capability `enabled`）：挂在 `AppLayout`（包裹几乎所有业务路由）
 * → 全系统生效。登录态确认（`externalAuthStore.status === 'authenticated'`）后一次性 `checkInviteCodeBinding()`
 * → 未绑弹窗。与登录门禁 boot（`useExternalAuthBoot`）无竞态：登录先于门禁（本 boot 等 authenticated 才触发）。
 * `ace-gateway` / `disabled` 不触发。`checkedRef` 保证单次（subtree remount 后会重新允许一次，符合 reload 回流后重查）。
 */
export function useInviteCodeGateBoot(): void {
  const loginStrategy = useLoginStrategyStore((s) => s.loginStrategy);
  const authStatus = useExternalAuthStore((s) => s.status);
  const checkedRef = useRef(false);

  useEffect(() => {
    if (loginStrategy !== 'oauth-provider') return;
    if (getCapabilities().getInviteCodeGatePolicy().value !== 'enabled') return;
    if (authStatus !== 'authenticated') return;
    if (checkedRef.current) return;
    checkedRef.current = true;
    void checkInviteCodeBinding();
  }, [loginStrategy, authStatus]);
}
