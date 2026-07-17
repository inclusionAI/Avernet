/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * BCN Auth Store - 一期 BCN 开源登录态（纯数据，无副作用）。
 *
 * 仅承载 /auth/user、/auth/url、/auth/logout 编排所需状态；业务请求在 hook 层完成。
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

export type BcnAuthStatus =
  | 'unknown'
  | 'authenticated'
  | 'unauthenticated'
  | 'error';

export interface BcnAuthUser {
  userId: string;
  name: string | null;
  provider: string;
  avatar: string | null;
}

interface BcnAuthState {
  status: BcnAuthStatus;
  user: BcnAuthUser | null;
  loginUrl: string | null;
  isCheckingAuth: boolean;
  isLoadingLoginUrl: boolean;
  isLoggingOut: boolean;
  error: string | null;

  setCheckingAuth: (loading: boolean) => void;
  setAuthenticated: (user: BcnAuthUser) => void;
  setUnauthenticated: () => void;
  setAuthError: (message: string) => void;
  setLoginUrl: (url: string | null) => void;
  setLoadingLoginUrl: (loading: boolean) => void;
  setLoggingOut: (loading: boolean) => void;
  reset: () => void;
}

const initialState = {
  status: 'unknown' as BcnAuthStatus,
  user: null as BcnAuthUser | null,
  loginUrl: null as string | null,
  isCheckingAuth: false,
  isLoadingLoginUrl: false,
  isLoggingOut: false,
  error: null as string | null,
};

export const useBcnAuthStore = create<BcnAuthState>()(
  devtools(
    (set) => ({
      ...initialState,

      setCheckingAuth: (isCheckingAuth) =>
        set({ isCheckingAuth }, false, 'bcnAuth/setCheckingAuth'),

      setAuthenticated: (user) =>
        set(
          {
            status: 'authenticated',
            user,
            error: null,
            isCheckingAuth: false,
          },
          false,
          'bcnAuth/setAuthenticated',
        ),

      setUnauthenticated: () =>
        set(
          {
            status: 'unauthenticated',
            user: null,
            error: null,
            isCheckingAuth: false,
          },
          false,
          'bcnAuth/setUnauthenticated',
        ),

      setAuthError: (message) =>
        set(
          {
            status: 'error',
            user: null,
            error: message,
            isCheckingAuth: false,
          },
          false,
          'bcnAuth/setAuthError',
        ),

      setLoginUrl: (loginUrl) =>
        set({ loginUrl }, false, 'bcnAuth/setLoginUrl'),

      setLoadingLoginUrl: (isLoadingLoginUrl) =>
        set({ isLoadingLoginUrl }, false, 'bcnAuth/setLoadingLoginUrl'),

      setLoggingOut: (isLoggingOut) =>
        set({ isLoggingOut }, false, 'bcnAuth/setLoggingOut'),

      reset: () => set(initialState, false, 'bcnAuth/reset'),
    }),
    { name: 'BcnAuthStore' },
  ),
);
