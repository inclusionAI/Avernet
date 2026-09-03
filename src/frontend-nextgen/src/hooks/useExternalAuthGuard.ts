import { useExternalAuth } from '@/hooks/useExternalAuth';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { useEffect } from 'react';

type GuardMode = 'soft' | 'force';

interface UseExternalAuthGuardOptions {
  mode: GuardMode;
  enabled?: boolean;
}

export interface UseExternalAuthGuardResult {
  status: ReturnType<typeof useExternalAuth>['status'];
  user: ReturnType<typeof useExternalAuth>['user'];
  isAuthenticated: boolean;
  isUnauthenticated: boolean;
  /** force 模式且未登录 → 敏感页内容不渲染（防 stale）。 */
  shouldBlockContent: boolean;
}

const SOFT_PROMPT_DELAY_MS = 1500;

/**
 * 外部登录（oauth-provider 策略）的页面级 guard。仅 `ace-gateway` 不挂载（内部走 ACE 反应式）。
 * - 挂载即 `checkAuth()`（`/auth/user`）主动探活。
 * - 未登录 → 一次性登记 `requestPrompt` 信号（soft 延迟 1.5s / force 立即）；单飞由 `loginRedirectStore` 保证，
 *   消费由全局 `ExternalLoginPromptModal` 承担（guard 不自渲染弹窗）。
 * - `shouldBlockContent`（force + 未登录）供调用方阻断敏感页渲染。
 */
export function useExternalAuthGuard({
  mode,
  enabled = true,
}: UseExternalAuthGuardOptions): UseExternalAuthGuardResult {
  const auth = useExternalAuth();

  useEffect(() => {
    if (!enabled) return;
    void auth.checkAuth();
  }, [enabled, auth.checkAuth]);

  useEffect(() => {
    if (!enabled || auth.status !== 'unauthenticated') return;
    if (mode === 'force') {
      useLoginRedirectStore.getState().requestPrompt();
      return;
    }
    const timer = window.setTimeout(() => {
      useLoginRedirectStore.getState().requestPrompt();
    }, SOFT_PROMPT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [enabled, mode, auth.status]);

  return {
    status: auth.status,
    user: auth.user,
    isAuthenticated: auth.status === 'authenticated',
    isUnauthenticated: auth.status === 'unauthenticated',
    shouldBlockContent: enabled && mode === 'force' && auth.status !== 'authenticated',
  };
}

/**
 * 全系统外部登录主动 boot（仅 `oauth-provider` 策略）：进布局即 `checkAuth` → 401 → `requestPrompt` → 全局 modal。
 * 供 `src/layouts/AppLayout` 挂载（包裹几乎所有业务路由）→ 全业务路由全系统生效（见 design 决策 6）。
 * `ace-gateway` 策略 `enabled=false` 不触发（内部走 ACE 反应式）；soft 不阻断内容,不惊吓已登录用户。
 */
export function useExternalAuthBoot(): void {
  const loginStrategy = useLoginStrategyStore((s) => s.loginStrategy);
  useExternalAuthGuard({ mode: 'soft', enabled: loginStrategy === 'oauth-provider' });
}
