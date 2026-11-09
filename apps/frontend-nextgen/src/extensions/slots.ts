// 槽位注册表：本模块零业务 import（无环保证），组件经 getExtensionSlots() 解析槽位。
// 装配文件（empty.ts Open / collab.ts 极简 / internal.ts 内源）在 app.tsx 启动链注册真实实现。
import type { AppExtensionSlots } from './contract';

let slots: AppExtensionSlots = {
  groupTasks: null,
  groupServiceBotRouting: null,
  taskSessionNavigationObserver: null,
  taskPanelBootstrap: null,
  sessionTaskPanelTab: null,
};

export function registerExtensionSlots(next: AppExtensionSlots): void {
  slots = next;
}

export function getExtensionSlots(): AppExtensionSlots {
  return slots;
}
