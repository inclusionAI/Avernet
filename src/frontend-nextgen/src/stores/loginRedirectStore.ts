import { create } from 'zustand';

/**
 * 网关登录处置的统一状态载体（Store 只做同步 setter，守分层）。
 *
 * `pendingLogin` 是统一信号（单飞：首个胜出，避免并发命中刷新页面）：
 * - `{ mode:'redirect', url }`：内部 `ace-gateway` 策略——硬跳转到 ACE body 提供的登录链接。
 * - `{ mode:'prompt' }`：外部 `oauth-provider` 策略——弹 `ExternalLoginPromptModal`（不携带 ACE pubLogin url），
 *   登录入口经 `/auth/url` provider 取得。
 *
 * Service 层（`httpClient` / raw-fetch 旁路 / `useExternalAuthGuard`）只调 `requestRedirect(url)` / `requestPrompt()`
 * 「敲门」；toast + `window.location.replace` 由顶层观察者 `useGatewayLoginRedirect`（redirect 模式）消费；
 * prompt 模式由全局 `ExternalLoginPromptModal` 组件消费（守 Service 禁 toast/DOM）。
 *
 * `pendingLoginUrl` 为 `redirect` 模式的 url（向后兼容既有 httpClient/observer 测试读取）。
 */
export type LoginRedirectMode = 'redirect' | 'prompt';

export interface PendingLogin {
  mode: LoginRedirectMode;
  url?: string;
}

interface LoginRedirectState {
  pendingLogin: PendingLogin | undefined;
  /** 向后兼容：等于 `pendingLogin?.mode==='redirect' ? pendingLogin.url : undefined`。 */
  pendingLoginUrl: string | undefined;
  /** 内部(ace-gateway)：登记硬跳转。已 pending 或 url 非法时 no-op（单飞）。 */
  requestRedirect: (url: string) => void;
  /** 外部(oauth-provider)：登记弹窗提示。已 pending 时 no-op（单飞）。 */
  requestPrompt: () => void;
  /** 重置（测试隔离用；产线一般无需调用）。 */
  reset: () => void;
}

export const useLoginRedirectStore = create<LoginRedirectState>((set, get) => ({
  pendingLogin: undefined,
  pendingLoginUrl: undefined,
  requestRedirect: (url) => {
    if (get().pendingLogin !== undefined) return; // 单飞：首个信号胜出。
    if (typeof url !== 'string' || url.trim() === '') return; // 非法 url 不登记。
    set({ pendingLogin: { mode: 'redirect', url }, pendingLoginUrl: url });
  },
  requestPrompt: () => {
    if (get().pendingLogin !== undefined) return; // 单飞：首个信号胜出。
    set({ pendingLogin: { mode: 'prompt' } });
  },
  reset: () => set({ pendingLogin: undefined, pendingLoginUrl: undefined }),
}));
