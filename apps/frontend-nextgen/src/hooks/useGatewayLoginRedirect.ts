import { notifyError } from '@/components/ui/notify';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { redirectCurrentTab } from '@/utils/redirectCurrentTab';
import { useEffect, useRef } from 'react';

const REDIRECT_TOAST_MESSAGE = '未登录，正在跳转登录…';

/**
 * 顶层观察者(经 app.tsx 的 rootContainer 全局挂载 GatewayLoginRedirector):
 * 消费 loginRedirectStore.pendingLogin —— 仅 `redirect` 模式（内部 ace-gateway）在 Hook 层完成
 * 「一次 toast + 当前标签页 window.location.replace」；`prompt` 模式（外部 oauth-provider）由全局
 * ExternalLoginPromptModal 消费，本观察者不处理（守 toast/弹窗在 Hook/组件层，Service 只 set store）。
 *
 * 为什么副作用在 Hook 而非探测点(httpClient/旁路):Service 禁 toast/DOM,探测点只 set store;
 * toast/跳转上移到 Hook 层(见 openspec/changes/redirect-not-login-to-gateway-login/design.md D2)。
 * 复用 @/components/ui/notify 的 notifyError(集中停留时长/可关闭性)。
 *
 * 单飞:store 已 pending 时 requestRedirect/requestPrompt no-op(并发只首个信号胜出);本 hook 再用 firedRef 守卫,
 * 即便 React StrictMode 双调 effect 或重渲染,toast 与 location.replace 各只一次(见 design.md D3)。
 * 若探测在 React 挂载前先 set 了 store,本 hook 挂载时 effect 仍会补触发(读当前 pendingLogin)。
 *
 * 全球级(不防跨刷新死循环):仅在单次页面生命周期内保证单飞;登录回跳后仍未登录会重新探测——
 * 该场景(sessionStorage 计数)按决策列为 Non-Goal / follow-up。
 */
export function useGatewayLoginRedirect(): void {
  const pendingLogin = useLoginRedirectStore((s) => s.pendingLogin);
  const firedRef = useRef(false);

  useEffect(() => {
    if (!pendingLogin || firedRef.current) return;
    // 外部 prompt 模式由全局 ExternalLoginPromptModal 组件消费；本观察者只处理内部硬跳转。
    if (pendingLogin.mode !== 'redirect') return;
    firedRef.current = true;
    notifyError(REDIRECT_TOAST_MESSAGE);
    redirectCurrentTab(pendingLogin.url ?? '');
  }, [pendingLogin]);
}
