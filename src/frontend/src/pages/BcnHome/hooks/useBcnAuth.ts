/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * useBcnAuth - 一期 BCN 开源 OAuth 登录态编排。
 */

import * as BcnController from '@/services/backend-api/BcnController';
import { useBcnAuthStore } from '@/stores/bcnAuthStore';
import { extractErrorMessage } from '@/utils/requestErrorHandler';
import { useCallback, useEffect } from 'react';
import { toast } from 'sonner';

const BCN_AUTH_RETURN_TO_KEY = 'bcn:auth:returnTo';

function getCurrentReturnTo(): string {
  if (typeof window === 'undefined') return '/';
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function isSafeReturnTo(value: string | null): value is string {
  return !!value && value.startsWith('/') && !value.startsWith('//');
}

function persistReturnTo() {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(BCN_AUTH_RETURN_TO_KEY, getCurrentReturnTo());
  } catch (error) {
    console.warn('[useBcnAuth] 保存登录回跳地址失败:', error);
  }
}

function consumeReturnTo(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const value = window.localStorage.getItem(BCN_AUTH_RETURN_TO_KEY);
    window.localStorage.removeItem(BCN_AUTH_RETURN_TO_KEY);
    return isSafeReturnTo(value) ? value : null;
  } catch (error) {
    console.warn('[useBcnAuth] 读取登录回跳地址失败:', error);
    return null;
  }
}

export function restoreBcnAuthReturnToIfNeeded(): boolean {
  if (typeof window === 'undefined') return false;
  const searchParams = new URLSearchParams(window.location.search);
  if (searchParams.get('login') !== 'success') return false;

  const returnTo = consumeReturnTo();
  if (!returnTo || returnTo === getCurrentReturnTo()) return false;

  window.location.replace(returnTo);
  return true;
}

export function useBcnAuth() {
  const status = useBcnAuthStore((state) => state.status);
  const user = useBcnAuthStore((state) => state.user);
  const loginUrl = useBcnAuthStore((state) => state.loginUrl);
  const isCheckingAuth = useBcnAuthStore((state) => state.isCheckingAuth);
  const isLoadingLoginUrl = useBcnAuthStore(
    (state) => state.isLoadingLoginUrl,
  );
  const isLoggingOut = useBcnAuthStore((state) => state.isLoggingOut);
  const setCheckingAuth = useBcnAuthStore((state) => state.setCheckingAuth);
  const setAuthenticated = useBcnAuthStore((state) => state.setAuthenticated);
  const setUnauthenticated = useBcnAuthStore(
    (state) => state.setUnauthenticated,
  );
  const setAuthError = useBcnAuthStore((state) => state.setAuthError);
  const setLoginUrl = useBcnAuthStore((state) => state.setLoginUrl);
  const setLoadingLoginUrl = useBcnAuthStore(
    (state) => state.setLoadingLoginUrl,
  );
  const setLoggingOut = useBcnAuthStore((state) => state.setLoggingOut);

  useEffect(() => {
    restoreBcnAuthReturnToIfNeeded();
  }, []);

  const checkAuth = useCallback(async () => {
    setCheckingAuth(true);
    try {
      const res = await BcnController.getAuthUser();
      setAuthenticated({
        userId: res.user_id,
        name: res.name ?? null,
        provider: res.provider,
        avatar: res.avatar ?? null,
      });
      return true;
    } catch (error: any) {
      const statusCode = error?.response?.status;
      if (statusCode === 401) {
        setUnauthenticated();
        return false;
      }

      const message = extractErrorMessage(error, '获取登录态失败');
      console.error('[useBcnAuth] 获取登录态失败:', error);
      setAuthError(message);
      return false;
    }
  }, [setAuthError, setAuthenticated, setCheckingAuth, setUnauthenticated]);

  const loadLoginUrl = useCallback(async () => {
    setLoadingLoginUrl(true);
    try {
      const res = await BcnController.getAuthUrl({
        returnTo: getCurrentReturnTo(),
      });
      const firstUrl = res.providers?.[0]?.url || null;
      if (!firstUrl) {
        setLoginUrl(null);
        toast.error('登录地址获取失败，请稍后重试');
        return null;
      }
      setLoginUrl(firstUrl);
      return firstUrl;
    } catch (error) {
      const message = extractErrorMessage(error, '登录地址获取失败，请稍后重试');
      console.error('[useBcnAuth] 获取登录地址失败:', error);
      setLoginUrl(null);
      toast.error(message);
      return null;
    } finally {
      setLoadingLoginUrl(false);
    }
  }, [setLoadingLoginUrl, setLoginUrl]);

  const login = useCallback(async () => {
    persistReturnTo();
    const targetUrl = loginUrl || (await loadLoginUrl());
    if (!targetUrl) return;
    window.location.href = targetUrl;
  }, [loadLoginUrl, loginUrl]);

  const logout = useCallback(async () => {
    setLoggingOut(true);
    try {
      await BcnController.logoutAuth();
      window.location.reload();
    } catch (error) {
      const message = extractErrorMessage(error, '退出登录失败，请稍后重试');
      console.error('[useBcnAuth] 退出登录失败:', error);
      toast.error(message);
    } finally {
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
