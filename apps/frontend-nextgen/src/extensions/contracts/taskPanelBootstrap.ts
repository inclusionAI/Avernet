// 任务副屏会话 bootstrap 槽位契约（collab-form Task 17）：纯类型文件，运行时零字节。
// useSessionTaskPanelBootstrap 属任务栈裁剪面（collab 不重放任务面板页签、不拉任务列表），
// 入参与恢复状态契约从实现提取；实现文件反向注解本契约（type-only，编译期擦除）。
// 依赖红线：仅 SDK（@tc-chat/core）与 React 类型。
import type { PanelHandle, PanelTab } from '@tc-chat/core';
import type { RefObject } from 'react';

/** 任务副屏 bootstrap 入参（原样对应 useSessionTaskPanelBootstrap 的内联对象类型）。 */
export interface TaskPanelBootstrapArgs {
  sessionId: string | null;
  userId: string | null;
  panelRef: RefObject<PanelHandle | null>;
  /** 是否启用进入会话自动恢复；单聊历史未就绪时为 false，避免首屏并发全量任务查询。 */
  enabled?: boolean;
}

/** 仅暴露宿主按钮所需的恢复状态，不依赖可裁剪的任务资产类型。 */
export interface TaskPanelBootstrapResult {
  status: 'idle' | 'searching' | 'not-found' | 'opened' | 'incomplete' | 'error';
  continueRecovery: () => Promise<void>;
}

/** bootstrap hook 契约：恢复任务副屏，并支持继续历史分页或重试。 */
export type TaskPanelBootstrapHook = (args: TaskPanelBootstrapArgs) => TaskPanelBootstrapResult;

/** 当前会话可重开的 TaskPanel 快照查询（useSessionTaskPanelTab）；无快照返回 null。 */
export type SessionTaskPanelTabHook = (sessionId: string | null) => PanelTab | null;
