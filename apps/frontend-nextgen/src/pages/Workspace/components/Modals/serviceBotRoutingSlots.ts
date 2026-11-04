// 建群弹窗 service-bot 选路槽位解析（模块作用域一次性绑定，供 CreateGroupModal 消费）：
// CreateGroupModal.tsx 已达组件 300 行门禁（scripts/ci-guardrails.mjs TC-G005），本小模块承接
// 槽位常量，解析语义保持在模块求值期（先于任何渲染），与 paneTaskSlots.ts 同构：不得改为组件体内
// 按次解析——collab 形态要求选路实现可被 tree-shaking 排除，且 hook 身份按构建恒定（React hooks 规则）。
// 模块图不变量（见 src/extensions/slots.ts 注释）：本模块解析的是「求值时刻」的注册表，因此不得被
// src/extensions 装配图静态引达（否则会捕获全 null 默认槽位，静默丢弃 Open 选路能力）。
import { getExtensionSlots } from '@/extensions/slots';
import { useNoopServiceBotRouting } from '@/pages/Workspace/hooks/noopServiceBotRouting';

// 建群选路槽位（collab 形态 noop：跳过 TC 分类查询，提交不被 routing.ready 阻塞）。
export const useResolvedServiceBotRouting =
  getExtensionSlots().groupServiceBotRouting?.useServiceBotRouting ?? useNoopServiceBotRouting;
