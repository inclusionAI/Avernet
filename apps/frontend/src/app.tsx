// 运行时配置
import { Toaster } from '@/components/ui/sonner';
import loader from '@monaco-editor/loader';
import { history } from '@umijs/max';
import React from 'react';
import ReactDOM from 'react-dom';

// 配置 Monaco Editor 使用阿里内网 CDN 加速
loader.config({
  paths: {
    vs: 'https://gw.alipayobjects.com/os/lib/monaco-editor/0.45.0/min/vs',
  },
  'vs/nls': {
    availableLanguages: {
      '*': 'zh-cn',
    },
  },
});

// 确保 React 和 ReactDOM 全局可用（供 UMD 组件使用）
// 某些 UMD 包在构建时 external 了 React，需要在加载前挂载到 window
if (typeof window !== 'undefined') {
  (window as any).React = React;
  (window as any).ReactDOM = ReactDOM;
}

// 引入自定义样式
import '@/styles/components.css';
import { isBcnDomain } from './utils/platform';

// capabilities 注入入口：先执行 @ext（开源=空 / 内部=各模块 extend），再冻结契约。
// 必须在任何 useExt/getExt 消费前完成。@ext alias 见 config.ts / config.internal.ts。
import { sealExtensions } from '@/capabilities';
import '@ext';
sealExtensions();

export { getInitialState, render } from './pages/Bootstrap';
export { request } from './requestConfig';

/**
 * BCN 域名路由守卫
 * BCN 专属域名只允许访问 /bcn 相关页面，其余路径强制跳转
 * （是否为 BCN 域名由 isBcnDomain() 经 @ext 注入判断，开源默认恒 false）
 */
export function onRouteChange({
  location,
}: {
  location: { pathname: string };
}) {
  if (isBcnDomain() && !location.pathname.startsWith('/bcn')) {
    history.replace('/bcn/chat/list');
  }
}

export function rootContainer(container: React.ReactNode) {
  return React.createElement(
    React.Fragment,
    null,
    container,
    React.createElement(Toaster),
  );
}

// export const reactQuery = {
//   queryClient: {
//     defaultOptions: {
//       queries: { refetchOnWindowFocus: false },
//     },
//   },
// };
