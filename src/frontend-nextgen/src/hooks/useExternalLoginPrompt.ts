import { useExternalAuth } from '@/hooks/useExternalAuth';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useCallback, useEffect, useState } from 'react';

/**
 * 外部登录提示弹窗编排（仅 `loginStrategy==='oauth-provider'`）。
 * 订阅 `loginRedirectStore.pendingLogin{mode:'prompt'}`（来源：`useExternalAuthGuard` 主动 checkAuth 401，
 * 或 `httpClient` oauth 策略下反应式 ACE 体 → `triggerLoginPrompt`）。单飞保证页面生命周期内只弹一次。
 *
 * 副作用（navigate）由 `useExternalAuth.login` → `navigateToUrl` 接缝承担；组件 (`ExternalLoginPromptModal`) 仅消费
 * 本 hook 返回的 `{open,onDismiss,onLogin,loadingLoginUrl}`，不直接 import Store（守 Component 禁 import Store）。
 */
export interface UseExternalLoginPromptResult {
  open: boolean;
  onDismiss: () => void;
  onLogin: () => void;
  loadingLoginUrl: boolean;
}

export function useExternalLoginPrompt(): UseExternalLoginPromptResult {
  const pendingLogin = useLoginRedirectStore((s) => s.pendingLogin);
  const shouldPrompt = pendingLogin?.mode === 'prompt';
  const auth = useExternalAuth();
  const [dismissed, setDismissed] = useState(false);

  // 信号消失/重置时清 dismissed，允许下次策略切换后再次弹窗。
  useEffect(() => {
    if (!shouldPrompt) setDismissed(false);
  }, [shouldPrompt]);

  const onDismiss = useCallback(() => setDismissed(true), []);
  const onLogin = useCallback(() => {
    void auth.login();
  }, [auth]);

  return {
    open: shouldPrompt && !dismissed,
    onDismiss,
    onLogin,
    loadingLoginUrl: auth.isLoadingLoginUrl,
  };
}
