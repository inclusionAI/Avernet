import { extendCapabilities, getCapabilities, sealExtensions } from '@/capabilities';
import { Toaster } from '@/components/ui/sonner';
import { appExtension, registerSidePanelWiring } from '@/extensions';
import { history } from '@umijs/max';
import React from 'react';

extendCapabilities(appExtension.capabilities);
sealExtensions();
// 副屏 SDK 能力装配：经 @/extensions alias 注入（Task 6 overlay）。
// 内源：internal.ts（②③ + 方式①卡片市场）；Open Core：empty.ts（仅②③）。
registerSidePanelWiring();

export { request } from './requestConfig';

export function onRouteChange({ location }: { location: { pathname: string } }) {
  const redirect = getCapabilities().getRuntimeRouteRedirect({ pathname: location.pathname });
  if (redirect.status === 'available' && redirect.value && redirect.value !== location.pathname) {
    history.replace(redirect.value);
  }
}

export function rootContainer(container: React.ReactNode) {
  return React.createElement(React.Fragment, null, container, React.createElement(Toaster));
}
