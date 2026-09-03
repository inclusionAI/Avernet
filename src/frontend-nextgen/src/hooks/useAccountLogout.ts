// 顶栏头像「退出登录」收口 Hook。仅外部 OAuth 登录形态（loginStrategy==='oauth-provider'，
// 即 Open Core=阿里云部署）可用；内部 overlay（ace-gateway）无 /openapi/v1/auth/* 契约，入口不渲染。
// 编排（POST /openapi/v1/auth/logout → 成功刷新 reloadCurrentTab；失败 toast）全部复用 useExternalAuth，
// 本 hook 只做「形态门控 + 委托」，遵循 hook 层不新建编排序列的老规矩。
import { getCapabilities } from '@/capabilities';
import { useExternalAuth } from '@/hooks/useExternalAuth';

export interface UseAccountLogoutResult {
  /** 是否渲染退出菜单：Open Core（oauth-provider）true；internal（ace-gateway）false。 */
  canLogout: boolean;
  /** 退出执行中（菜单行走 spinner + disabled），来自 useExternalAuthStore。 */
  isLoggingOut: boolean;
  /** 退出编排：成功后整页 reload（useExternalAuth 内 reloadCurrentTab）；失败 toast 已在其中，不 reject 打断调用方。 */
  logout: () => Promise<void>;
}

export function useAccountLogout(): UseAccountLogoutResult {
  const { isLoggingOut, logout } = useExternalAuth();
  // HelpMenu/AppHeader 同款 capability 渲染期读取：登录策略 boot 期即定，不随用户操作变化。
  const strategy = getCapabilities().getLoginStrategy();
  const canLogout = strategy.status === 'available' && strategy.value === 'oauth-provider';
  return { canLogout, isLoggingOut, logout };
}
