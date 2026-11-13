// Open 形态真实群任务槽位注册（既有 GroupChatPane 套件专用，import 副作用注册）：
// GroupChatPane 在模块作用域解析槽位常量（按模块求值一次性绑定），而测试文件体内的
// registerExtensionSlots 晚于其全部静态 import 执行、无法改写已捕获的常量——故本文件以
// import 形式供套件在「GroupChatPane import 之上」引入（import 按声明序求值），
// 使套件恢复真实 Open 行为（jest 全局 setup 只注册零行为占位，见其头注释）。
// 本文件的 import 晚于套件 jest.mock 提升执行 → 各套件对 TaskLinkedSession 等的 mock
// 在此正常生效，注册绑定的即是套件桩件（in-file = mocks honored）。
// 其余槽位沿用全局 setup 默认，本文件只升级 groupTasks（与 src/extensions/empty.ts 同构）。
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';
import type { AppExtensionSlots } from '@/extensions/contract';
import { ManagerWorkerTasks } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import { useGroupTaskExecution } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import { buildTaskInstructions } from '@/services/tasks/taskMapper';
import { createGroupViaExecute } from '@/services/workspace/groupExecuteService';

registerExtensionSlots({
  ...getExtensionSlots(),
  groupTasks: {
    createGroupViaExecute,
    useGroupTaskExecution,
    ComposerCapabilitiesMenu,
    buildTaskInstructions,
    TaskLinkedSession,
    ManagerWorkerTasks,
  },
} satisfies AppExtensionSlots);
