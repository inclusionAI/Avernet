// Open 形态真实任务副屏 bootstrap 槽位注册（useGroupChatHeaderPanels 既有消费套件专用，import 副作用注册）：
// useGroupChatHeaderPanels 在模块作用域解析 taskPanelBootstrap 槽位（按模块求值一次性绑定），
// 而测试文件体内的 registerExtensionSlots 晚于其全部静态 import 执行、无法改写已捕获的
// hook——故本文件以 import 形式供套件在「hook/GroupWorkspaceArea import 之上」引入
// （import 按声明序求值），使套件恢复真实 Open 行为（jest 全局 setup 只注册
// 零行为占位，见其头注释）。未 jest.mock services/myTask 的套件沿用真实绑定：
// userId 为 null 或 store 无活跃任务时 bootstrap effect 早退、不发请求，
// 与槽位化前的直接 import 行为一致。本文件的 import 晚于套件 jest.mock 提升执行：
// 需要真实 bootstrap 行为但自身 jest.mock 了其下游服务的套件照常注册到真实 hook
// （消化的是套件自身 mock 后的下游）；确要桩掉本 hook 模块自身的套件，注册到的是其自身 mock。
// 其余槽位沿用全局 setup 默认，本文件只升级 taskPanelBootstrap（与 src/extensions/empty.ts 同构）。
import { useSessionTaskPanelBootstrap } from '@/hooks/useSessionTaskPanelBootstrap';
import { useSessionTaskPanelTab } from '@/hooks/useSessionTaskPanelTab';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';
import type { AppExtensionSlots } from '@/extensions/contract';

// sessionTaskPanelTab 为纯 store 选择器（无网络/副作用），与 bootstrap 同形态注册真实实现，
// 供「关闭后 Monitor 仍可点并用 task tab 快照重开」等真实行为断言消费。
registerExtensionSlots({
  ...getExtensionSlots(),
  taskPanelBootstrap: useSessionTaskPanelBootstrap,
  sessionTaskPanelTab: useSessionTaskPanelTab,
} satisfies AppExtensionSlots);
