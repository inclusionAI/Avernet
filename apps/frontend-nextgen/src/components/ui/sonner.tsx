import React from 'react';
import { createPortal } from 'react-dom';
import { Toaster as SonnerToaster } from 'sonner';

// 全局 Toast 容器统一在 app.tsx 挂载,页面内不要重复挂载。
// 规范 docs/design-system/ui-interaction-spec.md §11.5:页面上方居中、错误 6s/可手动关、显著视觉区分。
// - richColors:错误/成功/警告/信息各自着色,错误红底,一眼可辨(此前白底中性卡导致错误不明显)。
// - position:页面上方居中，由全局容器统一控制，业务调用不单独设置位置。
// 错误停留 6s 由 notifyError per-call 注入;成功 4s 由 notifySuccess 注入。
//
// 挂载点必须 Portal 到 document.body：sonner 原位渲染会停留在应用根容器（#root）内；
// 本应用在内网以 qiankun 子应用（[data-qiankun='aixcoding'] 嵌入宿主时，宿主挂载容器
// 一旦带 transform/z-index 形成层叠上下文，整个子应用子树（含 toast）会被压到 body 级
// Radix Portal 渲染的 Modal 遮罩（--z-dialog）之下——弹窗内接口报错的提示被蒙层挡住。
// Portal 后 toaster 与 Modal 遮罩同为 body 直接子节点，z-index 才真正可比。
export const Toaster = (props: React.ComponentProps<typeof SonnerToaster>) =>
  createPortal(
    <SonnerToaster position="top-center" richColors className="z-[var(--z-toast)]" {...props} />,
    document.body,
  );
