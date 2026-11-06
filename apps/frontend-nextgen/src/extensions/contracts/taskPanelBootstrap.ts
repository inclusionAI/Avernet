// 任务副屏会话 bootstrap 槽位契约（collab-form Task 17）：纯类型文件，运行时零字节。
// useSessionTaskPanelBootstrap 属任务栈裁剪面（collab 不重放任务面板页签、不拉任务列表），
// 入参与 void 返回签名从实现原样搬出；实现文件反向注解本契约（type-only，编译期擦除）。
// 依赖红线：仅 SDK（@tc-chat/core）与 React 类型。
import type { PanelHandle } from '@tc-chat/core';
import type { RefObject } from 'react';

/** 任务副屏 bootstrap 入参（原样对应 useSessionTaskPanelBootstrap 的内联对象类型）。 */
export interface TaskPanelBootstrapArgs {
  sessionId: string | null;
  userId: string | null;
  panelRef: RefObject<PanelHandle | null>;
}

/** bootstrap hook 契约：进入会话用任务列表反查最新任务并打开副屏；返回 void。 */
export type TaskPanelBootstrapHook = (args: TaskPanelBootstrapArgs) => void;
