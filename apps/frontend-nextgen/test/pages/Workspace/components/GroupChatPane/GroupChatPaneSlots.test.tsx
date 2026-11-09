// GroupChatPane 任务栈槽位接线回归（注册表层）：组件经模块作用域常量消费槽位（见 paneTaskSlots.ts），
// 槽位常量按模块求值一次性绑定 → 无法「null 后再 import 组件」验证渲染，菜单/下钻的
// 组件级渲染仍由既有 GroupChatPane 套件经「import 前真实注册」链路覆盖。
// Open 真实实现按 test/setup/registerExtensionSlots.ts 头注释的覆盖通道 in-file 注册：
// 晚于本文件 jest.mock 提升、早于断言执行，镜像 src/extensions/empty.ts 的启动装配内容。
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';
import type { AppExtensionSlots, GroupTasksExtension } from '@/extensions/contract';
import { ManagerWorkerTasks } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import { useGroupTaskExecution } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import { buildTaskInstructions } from '@/services/tasks/taskMapper';
import { createGroupViaExecute } from '@/services/workspace/groupExecuteService';

// Open 形态 groupTasks 槽位（与 src/extensions/empty.ts 注册内容同构）。
const openGroupTasks: GroupTasksExtension = {
  createGroupViaExecute,
  useGroupTaskExecution,
  ComposerCapabilitiesMenu,
  buildTaskInstructions,
  TaskLinkedSession,
  ManagerWorkerTasks,
};

describe('GroupChatPane 任务槽位接线', () => {
  // 每用例前重注册真实槽位：保持用例顺序无关（末位 collab null 用例的自覆盖不外泄）。
  beforeEach(() => {
    registerExtensionSlots({ ...getExtensionSlots(), groupTasks: openGroupTasks } satisfies AppExtensionSlots);
  });

  it('Open 装配（in-file 注册真实实现）下槽位为真实实现', () => {
    expect(getExtensionSlots().groupTasks?.useGroupTaskExecution).toBe(useGroupTaskExecution);
    expect(getExtensionSlots().groupTasks?.TaskLinkedSession).toBe(TaskLinkedSession);
  });

  it('collab 空槽位下组件解析为 null（菜单/下钻不渲染）', () => {
    registerExtensionSlots({ groupTasks: null, groupServiceBotRouting: null, taskSessionNavigationObserver: null, taskPanelBootstrap: null, sessionTaskPanelTab: null });
    const slots = getExtensionSlots();
    expect(slots.groupTasks?.ComposerCapabilitiesMenu ?? null).toBeNull();
    expect(slots.groupTasks?.ManagerWorkerTasks ?? null).toBeNull();
  });

  it('collab 空槽位下 paneTaskSlots 解析为 null 兜底（下钻分支跳出）', () => {
    // 组件经模块作用域一次性绑定槽位，同文件内无法「null 后再求值」——用 resetModules 取全新
    // 注册表，先按 collab 形态注册全 null，再首次求值 paneTaskSlots，覆盖 `?? useNullGroupTaskExecution`
    // 兜底与 TaskLinkedSession 的 undefined 解析（index.tsx 下钻分支由此跳出）。
    // 顺序安全：resetModules 只影响其后新 require 的模块实例，上方用例的静态绑定不受波及；
    // beforeEach 仍会为后续用例重注册真实槽位，可任意调序。
    jest.resetModules();
    const { registerExtensionSlots: registerOnFreshRegistry } = require('@/extensions/slots') as {
      registerExtensionSlots: (next: AppExtensionSlots) => void;
    };
    registerOnFreshRegistry({ groupTasks: null, groupServiceBotRouting: null, taskSessionNavigationObserver: null, taskPanelBootstrap: null, sessionTaskPanelTab: null });
    const paneSlots = require('@/pages/Workspace/components/GroupChatPane/paneTaskSlots') as {
      GroupTaskLinkedSession: unknown;
      useResolvedGroupTaskExecution: () => unknown;
    };
    expect(paneSlots.GroupTaskLinkedSession).toBeUndefined();
    expect(paneSlots.useResolvedGroupTaskExecution()).toBeNull();
  });
});
