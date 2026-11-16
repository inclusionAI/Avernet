import { notifyError, notifySuccess } from '@/components/ui/notify';
import { probeGatewaySession } from '@/services/auth/reauthProbe';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { focusLoginTab, openLoginInNewTab, redirectCurrentTab } from '@/utils/redirectCurrentTab';
import { broadcastAuthReturned, listenAuthReturned } from '@/utils/reauthBroadcast';
import { useEffect, useRef } from 'react';

/** 等待重登期间的探活间隔(登录恢复感知主干轮询,见 ace-gateway-popup-reauth D4)。 */
const PROBE_INTERVAL_MS = 3000;
const REAUTH_RESTORED_MESSAGE = '登录已恢复，请重试刚才的操作。';
const POPUP_BLOCKED_MESSAGE = '登录窗口被浏览器拦截，请允许弹窗后重试，或改用「在当前页登录」。';

export interface UseGatewayLoginRedirectResult {
  /** 是否处于「等待重登」(全局 GatewayReauthModal 弹显条件,仅 redirect 模式)。 */
  open: boolean;
  /** 待用的 ACE pubLogin 链接(探活轮询持续刷新,aceToken 保持新鲜)。 */
  loginUrl: string;
  /** 主操作:新标签页打开登录链接(手势内调用,存活窗口聚焦复用,被拦截时提示)。 */
  onOpenLoginTab: () => void;
  /** 降级操作:当前页导航登录(非浏览器宿主弹窗被拦时的兜底)。 */
  onCurrentTabLogin: () => void;
  /** 用户主动关闭 Modal:清口等待状态(轮询停止;此后新拦截信号可重新登记)。 */
  onDismiss: () => void;
}

/**
 * ace-gateway 重登编排 Hook(经 app.tsx rootContainer 全局挂载 GatewayReauthModal 消费)。
 *
 * 职责(ace-gateway-popup-reauth,替代 `redirect-not-login-to-gateway-login` 的「toast + 当前页
 * replace」硬跳语义——原页面全程不刷新,现场无损):
 * - 弹窗/开窗/提示副作用收口在本 Hook 层,探测点只 set store(守 Service 禁 toast/DOM,D2);
 * - **恢复感知双通道**(D4):等待期间短间隔轮询 `probeGatewaySession`(无条件主干——响应不再是
 *   ACE 拦截体即恢复),并受理同源 `BroadcastChannel('auth-returned')`(秒级快路径——登录回流
 *   tab boot 我们的 SPA 后广播,原页立即探活免等下一轮);探测到新 ACE 体时顺带刷新待用链接
 *   (aceToken 短时效,D6);
 * - **收口**(D5):确认恢复后推进认证代次(晚到的旧代拦截体在 requestRedirect 登记口按防线忽略)、
 *   清口等待信号、提示用户重试刚才的操作;不自动重放过期期间失败的请求;
 * - **boot 广播**:SPA 启动时无条件广播一次(等待重登状态的原页受理,普通新开 tab 的广播被
 *   非等待状态忽略,零副作用)。
 *
 * 单飞沿用 store 层语义:并发命中只首个信号胜出;等待信号清口(reset/resolveReauth)后,下一次
 * 拦截重新登记并重走全流程。prompt 模式(oauth-provider)不归本 Hook,由 ExternalLoginPromptModal 消费。
 */
export function useGatewayLoginRedirect(): UseGatewayLoginRedirectResult {
  const pendingLogin = useLoginRedirectStore((s) => s.pendingLogin);
  const open = pendingLogin?.mode === 'redirect';
  const loginUrl = open ? (pendingLogin?.url ?? '') : '';

  // boot 无条件广播:登录回流 tab(ACE goto 落回本站 SPA)借此唤醒原页快路径感知。
  useEffect(() => {
    broadcastAuthReturned();
  }, []);

  const attemptRef = useRef<Window | null>(null);
  const probingRef = useRef(false);

  // 恢复感知:轮询主干 + 广播受理 → 探活;确认恢复即收口。首探立即执行——晚到旧信号
  // (拦截体登记晚于恢复完成的重登团)在首个探活周期内即时消解,不残留弹闪。
  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    const probe = async (): Promise<void> => {
      if (cancelled || probingRef.current) return;
      probingRef.current = true;
      try {
        const result = await probeGatewaySession();
        if (cancelled) return;
        if (result.state === 'logged-in') {
          useExternalAuthStore.getState().bumpAuthEpoch();
          useLoginRedirectStore.getState().resolveReauth();
          notifySuccess(REAUTH_RESTORED_MESSAGE);
        } else if (result.state === 'unauthenticated' && result.loginUrl) {
          useLoginRedirectStore.getState().refreshRedirectUrl(result.loginUrl);
        }
        // unauthenticated 无新链接或 unknown(不可判定):维持等待,下一轮再探——
        // 探活为正证据制,拿不到肯定性已登录证据绝不收口(真机回归 #1)。
      } finally {
        probingRef.current = false;
      }
    };

    void probe();
    const timer = window.setInterval(() => void probe(), PROBE_INTERVAL_MS);
    const unsubscribe = listenAuthReturned(() => void probe());
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      unsubscribe();
    };
  }, [open]);

  const onOpenLoginTab = (): void => {
    if (!loginUrl) return;
    // 语雀 OAuth 弹窗同款策略:登录窗存活期间聚焦复用,不重复开新 tab。
    const existing = attemptRef.current;
    if (existing && focusLoginTab(existing)) return;
    const popup = openLoginInNewTab(loginUrl);
    if (!popup) {
      notifyError(POPUP_BLOCKED_MESSAGE);
      return;
    }
    attemptRef.current = popup;
  };

  const onCurrentTabLogin = (): void => {
    if (!loginUrl) return;
    redirectCurrentTab(loginUrl);
  };

  const onDismiss = (): void => {
    useLoginRedirectStore.getState().resolveReauth();
  };

  return { open, loginUrl, onOpenLoginTab, onCurrentTabLogin, onDismiss };
}
