import React from 'react';
import { Toaster as SonnerToaster } from 'sonner';

// 全局 Toast 容器统一在 app.tsx 挂载,页面内不要重复挂载。
// 规范 docs/design-system/ui-interaction-spec.md §11.5:右下角、错误 6s/可手动关、显著视觉区分。
// - richColors:错误/成功/警告/信息各自着色,错误红底,一眼可辨(此前白底中性卡导致错误不明显)。
// - closeButton:所有提示可手动关闭(错误强制可关,成功可关无害)。
// - position:右下角(此前 2 个协作 hook 违规设 top-center,已改经统一 notify 入口)。
// 错误停留 6s 由 notifyError per-call 注入;成功 4s 由 notifySuccess 注入。
export const Toaster = (props: React.ComponentProps<typeof SonnerToaster>) => (
  <SonnerToaster
    position="bottom-right"
    richColors
    closeButton
    className="z-[var(--z-toast)]"
    {...props}
  />
);
