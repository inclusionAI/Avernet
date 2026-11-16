import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { create } from 'zustand';

/**
 * 网关登录处置的统一状态载体（Store 只做同步 setter，守分层）。
 *
 * `pendingLogin` 是统一信号（单飞：首个胜出，避免并发命中重复处置）：
 * - `{ mode:'redirect', url }`：内部 `ace-gateway` 策略——「等待重登」信号。url 为 ACE 下发的
 *   pubLogin 链接（含 go 回跳与 aceToken）。处置由全局 `GatewayReauthModal` 消费：新标签页打开
 *   登录链接完成重登，原页面不刷新，凭探活/广播感知恢复（ace-gateway-popup-reauth；替代
 *   `redirect-not-login-to-gateway-login` 的「toast + 当前页 replace」硬跳语义）。
 * - `{ mode:'prompt' }`：外部 `oauth-provider` 策略——弹 `ExternalLoginPromptModal`（不携带 ACE pubLogin url），
 *   登录入口经 `/auth/url` provider 取得。
 *
 * Service 层（`httpClient` / raw-fetch 旁路 / `useExternalAuthGuard`）只调 `requestRedirect(url)` /
 * `requestPrompt()`「敲门」；UI 副作用（弹窗 / window.open / 轮询 / 广播受理）由组件层消费
 * （守 Service 禁 toast/DOM）。
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
  /**
   * 内部(ace-gateway)：登记「等待重登」。已 pending 或 url 非法时 no-op（单飞）。
   * `fromEpoch`：请求发出时捕获的认证代次——与当前代次不一致的晚到拦截体（上一代认证期间发出的
   * 请求、其响应晚到于重登成功之后）不再登记，避免恢复后 Modal 被旧信号闪回
   * （与 `requestPrompt` 的代次防线同构，见 ace-gateway-popup-reauth D5）。
   */
  requestRedirect: (url: string, fromEpoch?: number) => void;
  /**
   * 外部(oauth-provider)：登记弹窗提示。已 pending 时 no-op（单飞）。
   * `fromEpoch`：同 `requestRedirect` 的晚到信号防线。
   */
  requestPrompt: (fromEpoch?: number) => void;
  /**
   * 刷新 redirect 模式下待用的登录链接（探活轮询发现网关下发的新 ACE 拦截体时调用）：
   * aceToken 短时效，保持 Modal 消费的 url 持续新鲜（ace-gateway-popup-reauth D6）。
   * 非 redirect 模式或 url 非法时 no-op。
   */
  refreshRedirectUrl: (url: string) => void;
  /** 清口「等待重登」信号：重登恢复（探活确认）或用户关闭 Modal 取消等待时调用。 */
  resolveReauth: () => void;
  /** 重置（测试隔离用；产线收口走 `resolveReauth`）。 */
  reset: () => void;
}

export const useLoginRedirectStore = create<LoginRedirectState>((set, get) => ({
  pendingLogin: undefined,
  pendingLoginUrl: undefined,
  requestRedirect: (url, fromEpoch) => {
    // 晚到信号防线：发起时属于上一代认证的请求，其 ACE 拦截体晚到于重登成功之后，忽略。
    if (fromEpoch !== undefined && useExternalAuthStore.getState().authEpoch !== fromEpoch) return;
    if (get().pendingLogin !== undefined) return; // 单飞：首个信号胜出。
    if (typeof url !== 'string' || url.trim() === '') return; // 非法 url 不登记。
    set({ pendingLogin: { mode: 'redirect', url }, pendingLoginUrl: url });
  },
  requestPrompt: (fromEpoch) => {
    // 旧请求防线：发起时属于上一代认证的请求，其未登录响应晚到于新一轮成功登录，忽略。
    if (fromEpoch !== undefined && useExternalAuthStore.getState().authEpoch !== fromEpoch) return;
    if (get().pendingLogin !== undefined) return; // 单飞：首个信号胜出。
    // 未登录信号即事实：本地登录态快照（可能仍停留在过期前的 authenticated）同步翻转为
    // unauthenticated，否则 useExternalLoginPrompt 的 authenticated 联锁会屏蔽弹窗
    // （P2 回归：会话过期后操作无结果也无提示）。翻转对已 unauthenticated 的调用幂等。
    useExternalAuthStore.getState().setUnauthenticated();
    set({ pendingLogin: { mode: 'prompt' } });
  },
  refreshRedirectUrl: (url) => {
    const pending = get().pendingLogin;
    if (pending?.mode !== 'redirect') return; // 仅等待重登期间刷新链接。
    if (typeof url !== 'string' || url.trim() === '') return;
    set({ pendingLogin: { mode: 'redirect', url }, pendingLoginUrl: url });
  },
  resolveReauth: () => set({ pendingLogin: undefined, pendingLoginUrl: undefined }),
  reset: () => set({ pendingLogin: undefined, pendingLoginUrl: undefined }),
}));
