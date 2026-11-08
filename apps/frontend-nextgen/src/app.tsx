import { extendCapabilities, getCapabilities, sealExtensions } from '@/capabilities';
import { ExternalLoginPromptModal } from '@/components/ExternalLoginPromptModal';
import { InviteCodeBindingModal } from '@/components/InviteCodeBindingModal';
import { Toaster } from '@/components/ui/sonner';
import { appExtension, registerSidePanelWiring } from '@/extensions';
import { useErrorNotifyObserver } from '@/hooks/useErrorNotifyObserver';
import { useGatewayLoginRedirect } from '@/hooks/useGatewayLoginRedirect';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { history } from '@umijs/max';
import { useTaskSessionNavigationObserver } from '@/pages/Workspace/Chat/hooks/taskSessionNavigation';
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

/**
 * qiankun master 的 runtime modifier。
 *
 * excludeAssetFilter 无法直接写在 config 里：plugin-qiankun 生成 masterOptions.ts 时使用
 * JSON.stringify，函数字段会被静默丢弃。这里在运行期重建，让 Monaco CDN 资源保留原生
 * <script> 注入路径，避免子应用 loader 被 qiankun 改写为 fetch+eval 后在宿主安全策略下
 * 失败。前缀只由 Internal overlay 的 define 注入；Open Core 下为空时原样返回配置。
 */
interface QiankunRuntimeConfig {
  master?: {
    excludeAssetFilter?: (assetUrl: string) => boolean;
  };
}

export const qiankun = (config: QiankunRuntimeConfig = {}) => {
  const monacoCdnPrefix =
    typeof TEAMCLAW_MONACO_CDN_PREFIX !== 'undefined'
      ? TEAMCLAW_MONACO_CDN_PREFIX
      : '';
  if (!monacoCdnPrefix) return config;

  return {
    ...config,
    master: {
      ...config.master,
      excludeAssetFilter: (assetUrl: string) =>
        assetUrl.startsWith(`${monacoCdnPrefix}/`),
    },
  } satisfies QiankunRuntimeConfig;
};

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

/** 任务副屏 V2 会话联动观察者(task-panel-v2 P4):无 UI,消费 pendingNavigation 跳会话 + 时间窗消息定位。 */
function TaskSessionNavigationObserver() {
  useTaskSessionNavigationObserver();
  return null;
}

export function rootContainer(container: React.ReactNode) {
  return React.createElement(
    React.Fragment,
    null,
    container,
    React.createElement(GatewayLoginRedirector),
    React.createElement(ErrorNotifyObserver),
    React.createElement(TaskSessionNavigationObserver),
    React.createElement(ExternalLoginPromptModal),
    React.createElement(InviteCodeBindingModal),
    React.createElement(Toaster),
  );
}
