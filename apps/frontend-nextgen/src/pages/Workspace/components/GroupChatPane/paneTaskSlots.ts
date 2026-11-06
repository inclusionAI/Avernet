// GroupChatPane 群任务栈槽位解析（模块作用域一次性绑定，供 index.tsx 消费）：
// index.tsx 已逼近组件 300 行门禁（scripts/ci-guardrails.mjs TC-G005），本小模块承接槽位常量，
// 解析语义保持在模块求值期（先于任何渲染），不得改为组件体内按次解析——collab 形态要求任务栈实现
// 可被 tree-shaking 排除，且 hook/组件身份按构建恒定（React hooks 规则）。
// 模块图不变量（见 src/extensions/slots.ts 注释）：本模块解析的是「求值时刻」的注册表，因此不得被
// src/extensions 装配图静态引达（否则会捕获全 null 默认槽位，静默丢弃 Open 任务 UI）。
import { getExtensionSlots } from '@/extensions/slots';

/** collab 形态占位：不发起任务装配，composer 收到 null 后不渲染任务菜单。 */
function useNullGroupTaskExecution(): null {
  return null;
}

// 任务栈槽位（collab 形态注册 null）：hook/组件身份按构建恒定，不违反 hooks 规则。
export const useResolvedGroupTaskExecution =
  getExtensionSlots().groupTasks?.useGroupTaskExecution ?? useNullGroupTaskExecution;
export const GroupTaskLinkedSession = getExtensionSlots().groupTasks?.TaskLinkedSession;
