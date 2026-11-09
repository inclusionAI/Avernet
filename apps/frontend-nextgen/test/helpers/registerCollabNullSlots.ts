// collab 形态全 null 槽位注册（GroupChatComposerCollabSlots 套件专用，import 副作用注册）：
// GroupChatComposer 在模块作用域解析任务槽位常量（求值一次性绑定），文件体内的
// registerExtensionSlots 晚于其静态 import 执行、无法改写已捕获的常量——故本文件以
// import 形式供套件在「composer import 之上」引入（import 按声明序求值）。
// 与 test/helpers/registerOpenGroupTaskSlots（Open 真实装配）互为形态对照组：
// 本文件注册全 null，使 composer 在求值期绑定 null 槽位（菜单与任务发送分支整体消失），
// 对齐生产 collab 装配形态（src/extensions/slots.ts 默认态即全 null）。
import { registerExtensionSlots } from '@/extensions/slots';
import type { AppExtensionSlots } from '@/extensions/contract';

registerExtensionSlots({
  groupTasks: null,
  groupServiceBotRouting: null,
  taskSessionNavigationObserver: null,
  taskPanelBootstrap: null,
  sessionTaskPanelTab: null,
} satisfies AppExtensionSlots);
