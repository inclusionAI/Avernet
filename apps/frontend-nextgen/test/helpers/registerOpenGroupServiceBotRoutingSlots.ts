// Open 形态真实建群选路槽位注册（CreateGroupModal 套件专用，import 副作用注册）：
// CreateGroupModal 经 serviceBotRoutingSlots 在模块作用域解析选路槽位（按模块求值一次性绑定），
// 而测试文件体内的 registerExtensionSlots 晚于其全部静态 import 执行、无法改写已捕获的常量——
// 故本文件以 import 形式供套件在「CreateGroupModal import 之上」引入（import 按声明序求值），
// 使套件恢复真实 Open 行为（jest 全局 setup 只注册零行为占位，见其头注释）。
// 本文件的 import 晚于套件 jest.mock 提升执行 → 注册的真实 useServiceBotRouting 依赖的
// queryIsServiceBot 绑定即套件桩件（in-file mocks honored），与生产 empty.ts 装配同构。
// 其余槽位沿用全局 setup 默认，本文件只升级 groupServiceBotRouting（与 src/extensions/empty.ts 同构）。
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';
import type { AppExtensionSlots } from '@/extensions/contract';
import { ServiceBotRoutingControl } from '@/pages/Workspace/components/Modals/ServiceBotRoutingControl';
import { useServiceBotRouting } from '@/pages/Workspace/hooks/useServiceBotRouting';

registerExtensionSlots({
  ...getExtensionSlots(),
  groupServiceBotRouting: { useServiceBotRouting, ServiceBotRoutingControl },
} satisfies AppExtensionSlots);
