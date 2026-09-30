import { notifyError } from '@/components/ui/notify';
import { getAuthProviders, getCurrentAuthUser, logoutAuthSession } from '@/services/auth/authApiController';
import { toAuthUser } from '@/services/auth/authService';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { navigateToUrl, reloadCurrentTab } from '@/utils/redirectCurrentTab';
import { useCallback } from 'react';

/** axios 形错误透传（umi `request` 经 `skipErrorHandler` 在 401 原样 reject）：读 `error.response.status`。 */
interface AxiosLikeError {
  response?: { status?: number; data?: { message?: string } };
  message?: string;
}

function isAxiosLikeError(e: unknown): e is AxiosLikeError {
  return typeof e === 'object' && e !== null && 'response' in e;
}

function errorMessage(e: unknown, fallback: string): string {
  if (isAxiosLikeError(e)) return e.response?.data?.message || e.message || fallback;
  if (e instanceof Error) return e.message || fallback;
  return fallback;
}

/**
 * 外部 OAuth provider 登录态编排（仅 `loginStrategy==='oauth-provider'` 时使用；hook 层，守分层）。
 * - `checkAuth`：`GET /auth/user` → authenticated；401（`error.response.status`） → unauthenticated；其余 → error。
 * - `loadLoginUrl`：`GET /auth/url` → `providers[0].url`，缺失则提示。
 * - `login`：取/拉 provider url → `window.location.href`（经 `navigateToUrl` 接缝）。
 * - `logout`：`POST /auth/logout` → 刷新（经 `reloadCurrentTab` 接缝）。
 * toast / navigate 副作用在 hook 层（Service 禁 toast/DOM）；`/auth/*` 经 umi `request` + `skipErrorHandler`。
 */
export function useExternalAuth() {
  const status = useExternalAuthStore((s) => s.status);
  const user = useExternalAuthStore((s) => s.user);
  const loginUrl = useExternalAuthStore((s) => s.loginUrl);
  const isCheckingAuth = useExternalAuthStore((s) => s.isCheckingAuth);
  const isLoadingLoginUrl = useExternalAuthStore((s) => s.isLoadingLoginUrl);
  const isLoggingOut = useExternalAuthStore((s) => s.isLoggingOut);
  const setCheckingAuth = useExternalAuthStore((s) => s.setCheckingAuth);
  const setAuthenticated = useExternalAuthStore((s) => s.setAuthenticated);
  const setUnauthenticated = useExternalAuthStore((s) => s.setUnauthenticated);
  const setAuthError = useExternalAuthStore((s) => s.setAuthError);
  const setLoginUrl = useExternalAuthStore((s) => s.setLoginUrl);
  const setLoadingLoginUrl = useExternalAuthStore((s) => s.setLoadingLoginUrl);
  const setLoggingOut = useExternalAuthStore((s) => s.setLoggingOut);

  const checkAuth = useCallback(async (): Promise<boolean> => {
    setCheckingAuth(true);
    try {
      const dto = await getCurrentAuthUser();
      setAuthenticated(toAuthUser(dto));
      return true;
    } catch (error) {
      if (isAxiosLikeError(error) && error.response?.status === 401) {
        setUnauthenticated();
        return false;
      }
      console.error('[useExternalAuth] 获取登录态失败:', error);
      setAuthError(errorMessage(error, '获取登录态失败'));
      return false;
    }
  }, [setAuthError, setAuthenticated, setCheckingAuth, setUnauthenticated]);

  const loadLoginUrl = useCallback(async (): Promise<string | null> => {
    setLoadingLoginUrl(true);
    try {
      const dto = await getAuthProviders();
      const firstUrl = dto.providers?.[0]?.url || null;
      if (!firstUrl) {
        setLoginUrl(null);
        notifyError('登录地址获取失败，请稍后重试');
        return null;
      }
      setLoginUrl(firstUrl);
      return firstUrl;
    } catch (error) {
      console.error('[useExternalAuth] 获取登录地址失败:', error);
      setLoginUrl(null);
      notifyError('登录地址获取失败，请稍后重试');
      return null;
    } finally {
      setLoadingLoginUrl(false);
    }
  }, [setLoadingLoginUrl, setLoginUrl]);

  const login = useCallback(async (): Promise<void> => {
    const target = loginUrl || (await loadLoginUrl());
    if (!target) return;
    navigateToUrl(target);
  }, [loadLoginUrl, loginUrl]);

  const logout = useCallback(async (): Promise<void> => {
    setLoggingOut(true);
    try {
      await logoutAuthSession();
      reloadCurrentTab();
    } catch (error) {
      console.error('[useExternalAuth] 退出登录失败:', error);
      notifyError('退出登录失败，请稍后重试');
      setLoggingOut(false);
    }
  }, [setLoggingOut]);

  return {
    status,
    user,
    loginUrl,
    isCheckingAuth,
    isLoadingLoginUrl,
    isLoggingOut,
    checkAuth,
    loadLoginUrl,
    login,
    logout,
  };
}
