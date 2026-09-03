import { create } from 'zustand';

/**
 * 外部 OAuth provider 登录态（纯数据，无副作用）。仅承载 `/auth/user`、`/auth/url`、`/auth/logout`
 * 编排所需状态；业务请求在 hook 层（`useExternalAuth`）完成（守 Store 禁 async/API/toast）。
 *
 * 仅 `loginStrategy==='oauth-provider'`（Open Core 默认）策略下由 `useExternalAuth` 驱动；
 * `ace-gateway`（internal/员工）不使用本 store，身份走 `getHumanIdentity` 既有 Open Core/internal 解析。
 *
 * `ExternalAuthUser` 形状与 `src/services/auth/authService.ts` 的 `AuthUser` 一致（结构同型，无需 store→service import），
 * 便于 `getHumanIdentity` 在 oauth-provider 策略下直接映射为 `HumanIdentity`。
 */
export type ExternalAuthStatus = 'unknown' | 'authenticated' | 'unauthenticated' | 'error';

export interface ExternalAuthUser {
  userId: string;
  displayName: string;
  provider: string;
  avatarUrl?: string;
}

export interface ExternalAuthState {
  status: ExternalAuthStatus;
  user: ExternalAuthUser | null;
  loginUrl: string | null;
  isCheckingAuth: boolean;
  isLoadingLoginUrl: boolean;
  isLoggingOut: boolean;
  error: string | null;
  setCheckingAuth: (loading: boolean) => void;
  setAuthenticated: (user: ExternalAuthUser) => void;
  setUnauthenticated: () => void;
  setAuthError: (message: string) => void;
  setLoginUrl: (url: string | null) => void;
  setLoadingLoginUrl: (loading: boolean) => void;
  setLoggingOut: (loading: boolean) => void;
  reset: () => void;
}

const initialState = {
  status: 'unknown' as ExternalAuthStatus,
  user: null as ExternalAuthUser | null,
  loginUrl: null as string | null,
  isCheckingAuth: false,
  isLoadingLoginUrl: false,
  isLoggingOut: false,
  error: null as string | null,
};

export const useExternalAuthStore = create<ExternalAuthState>((set) => ({
  ...initialState,
  setCheckingAuth: (isCheckingAuth) => set({ isCheckingAuth }),
  setAuthenticated: (user) => set({ status: 'authenticated', user, error: null, isCheckingAuth: false }),
  setUnauthenticated: () => set({ status: 'unauthenticated', user: null, error: null, isCheckingAuth: false }),
  setAuthError: (message) => set({ status: 'error', user: null, error: message, isCheckingAuth: false }),
  setLoginUrl: (loginUrl) => set({ loginUrl }),
  setLoadingLoginUrl: (isLoadingLoginUrl) => set({ isLoadingLoginUrl }),
  setLoggingOut: (isLoggingOut) => set({ isLoggingOut }),
  reset: () => set(initialState),
}));
