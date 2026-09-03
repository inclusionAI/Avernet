import { extendCapabilities, getCapabilities, sealExtensions } from '@/capabilities';
import { ExternalLoginPromptModal } from '@/components/ExternalLoginPromptModal';
import { Toaster } from '@/components/ui/sonner';
import { appExtension, registerSidePanelWiring } from '@/extensions';
import { useErrorNotifyObserver } from '@/hooks/useErrorNotifyObserver';
import { useGatewayLoginRedirect } from '@/hooks/useGatewayLoginRedirect';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { history } from '@umijs/max';
import React from 'react';

extendCapabilities(appExtension.capabilities);
sealExtensions();
// 副屏 SDK 能力装配：经 @/extensions alias 注入（Task 6 overlay）。
// 内源：internal.ts（②③ + 方式①卡片市场）；Open Core：empty.ts（仅②③）。
registerSidePanelWiring();
// 运行期登录策略：capability（Open Core=oauth-provider / internal=ace-gateway）写入 loginStrategyStore，
// 供 httpClient ACE 体探测分支消费（见 add-external-oauth-login design 决策1/3）。
useLoginStrategyStore.getState().setLoginStrategy(getCapabilities().getLoginStrategy().value);

export { request } from './requestConfig';

export function onRouteChange({ location }: { location: { pathname: string } }) {
  const redirect = getCapabilities().getRuntimeRouteRedirect({ pathname: location.pathname });
  if (redirect.status === 'available' && redirect.value && redirect.value !== location.pathname) {
    history.replace(redirect.value);
  }
}

/** 全局网关登录跳转观察者:无 UI,消费 loginRedirectStore 在未登录时单次 toast + 当前标签页跳转。 */
function GatewayLoginRedirector() {
  useGatewayLoginRedirect();
  return null;
}

/** 全局接口失败默认提示观察者:无 UI,消费 errorNotifyStore 在协议层失败时兜底弹一条错误提示(可被 Hook 取消)。 */
function ErrorNotifyObserver() {
  useErrorNotifyObserver();
  return null;
}

export function rootContainer(container: React.ReactNode) {
  return React.createElement(
    React.Fragment,
    null,
    container,
    React.createElement(GatewayLoginRedirector),
    React.createElement(ErrorNotifyObserver),
    React.createElement(ExternalLoginPromptModal),
    React.createElement(Toaster),
  );
}
