// 顶栏头像「退出登录」收口 Hook。
// - oauth-provider（Open Core/阿里云部署）：委托 useExternalAuth（POST /openapi/v1/auth/logout → 刷新）
// - ace-gateway（内部 overlay）：跳转 ACE 统一登录登出地址（getAceGatewayLogoutUrl capability），
//   ACE 域会话清理后经 returnUrl 回到当前站点，触发网关登录重定向
// 本 hook 只做「形态门控 + 委托」，遵循 hook 层不新建编排序列的老规矩。
import { getCapabilities } from '@/capabilities';
import { useExternalAuth } from '@/hooks/useExternalAuth';
import { navigateToUrl } from '@/utils/redirectCurrentTab';
import { useCallback } from 'react';

export interface UseAccountLogoutResult {
  /** 是否渲染退出菜单：两种策略均支持退出（oauth-provider 始终 true；ace-gateway 有登出 URL 时为 true）。 */
  canLogout: boolean;
  /** 退出执行中（菜单行走 spinner + disabled），来自 useExternalAuthStore。 */
  isLoggingOut: boolean;
  /** 退出编排：oauth-provider → POST /auth/logout + 刷新；ace-gateway → 跳转 ACE 登出 URL。 */
  logout: () => Promise<void>;
}

export function useAccountLogout(): UseAccountLogoutResult {
  const { isLoggingOut, logout: oauthLogout } = useExternalAuth();
  const strategy = getCapabilities().getLoginStrategy();
  const aceLogoutUrl = getCapabilities().getAceGatewayLogoutUrl();
  const isAce = strategy.status === 'available' && strategy.value === 'ace-gateway';
  // ace-gateway 形态：有登出 URL 时 canLogout=true，缺失时回退 false（兜底安全）
  const canLogout = isAce
    ? aceLogoutUrl.status === 'available' && !!aceLogoutUrl.value
    : strategy.status === 'available' && strategy.value === 'oauth-provider';

  const aceLogout = useCallback(async (): Promise<void> => {
    if (!aceLogoutUrl.value) return;
    const currentUrl = typeof window !== 'undefined'
      ? `${window.location.origin}${window.location.pathname}${window.location.search}${window.location.hash}`
      : '/';
    const url = new URL(aceLogoutUrl.value);
    if (!url.searchParams.has('returnUrl')) {
      url.searchParams.set('returnUrl', currentUrl);
    }
    navigateToUrl(url.toString());
  }, [aceLogoutUrl.value]);

  return {
    canLogout,
    isLoggingOut,
    logout: isAce ? aceLogout : oauthLogout,
  };
}
