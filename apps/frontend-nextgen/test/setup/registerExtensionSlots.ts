// jest 全局装配：单元测试不加载 app.tsx，槽位在此注册非空默认以对齐「app 启动后槽位非空」的运行时形态；
// collab 形态断言用例在文件内自行 registerExtensionSlots(全 null) 覆盖。
//
// 为什么全局 setup 不能注册真实 UI 组件（不能整体 import @/extensions/empty）：
// setupFiles 先于测试文件的 jest.mock 注册执行，被 setup 预 import 的组件模块会把真实 service
// 绑定冻结进每文件共享的 module registry；测试文件随后 jest.mock 的下部 service 不会改写已捕获的
// import 绑定——实测会使 test/services/workspace 群聊类、ManagerWorkerTasks.test.tsx 等大量既有
// 用例的 mock 落空（真实请求逃逸）。因此：
// - 纯 mapper 槽位注册**真实实现**（零可 mock 依赖叶子）；
// - 其余 hooks/services 与 4 个 UI 组件槽位注册**零行为占位**（占位实现调用即抛错，组件渲染空
//   Fragment）——实测 useServiceBotRouting/useSessionTaskPanelBootstrap 等含可 mock 服务依赖的
//   真实注册同样会落空其自身测试的 jest.mock（useTaskSwitcherTasks/CreateGroupModal 等被波及）。
// - 观察者槽位同样用 `() => null` 占位。
// 需要真实组件行为的用例请在测试文件内 import 真实模块并 registerExtensionSlots 覆盖
// （slots.test.ts 第二用例即该覆盖通道的示范）；生产侧真实装配在 src/extensions/empty.ts
// 经 AppExtension 类型契约约束（typecheck 校验），与线上启动副作用一致。
import React from 'react';
import { buildTaskInstructions } from '@/services/tasks/taskMapper';
import { registerExtensionSlots } from '@/extensions/slots';
import type { AppExtensionSlots } from '@/extensions/contract';

/** 零行为占位组件：渲染空 Fragment，仅保证槽位非空（typeof 兼容各组件签名）。 */
const emptyFragment = () => React.createElement(React.Fragment);

/**
 * 调用即抛错的占位实现：用例若依赖真实行为会立刻显性失败并被指引到覆盖通道，
 * 而非静默拿到空数据。未来消费方测试应在文件内注册真实现（见文件头注释）。
 */
const createGroupViaExecuteStub = async () => {
  throw new Error('jest 全局槽位默认占位：需要真实 createGroupViaExecute 的用例请在测试文件内 registerExtensionSlots 覆盖');
};
const useGroupTaskExecutionStub = () => {
  throw new Error('jest 全局槽位默认占位：需要真实 useGroupTaskExecution 的用例请在测试文件内 registerExtensionSlots 覆盖');
};
const useServiceBotRoutingStub = () => {
  throw new Error('jest 全局槽位默认占位：需要真实 useServiceBotRouting 的用例请在测试文件内 registerExtensionSlots 覆盖');
};
const useSessionTaskPanelBootstrapStub = () => {
  throw new Error('jest 全局槽位默认占位：需要真实 useSessionTaskPanelBootstrap 的用例请在测试文件内 registerExtensionSlots 覆盖');
};
const useSessionTaskPanelTabStub = () => {
  throw new Error('jest 全局槽位默认占位：需要真实 useSessionTaskPanelTab 的用例请在测试文件内 registerExtensionSlots 覆盖');
};

registerExtensionSlots({
  groupTasks: {
    createGroupViaExecute: createGroupViaExecuteStub,
    useGroupTaskExecution: useGroupTaskExecutionStub,
    ComposerCapabilitiesMenu: emptyFragment,
    buildTaskInstructions,
    TaskLinkedSession: emptyFragment,
    ManagerWorkerTasks: emptyFragment,
  },
  groupServiceBotRouting: { useServiceBotRouting: useServiceBotRoutingStub, ServiceBotRoutingControl: emptyFragment },
  taskSessionNavigationObserver: () => null,
  taskPanelBootstrap: useSessionTaskPanelBootstrapStub,
  sessionTaskPanelTab: useSessionTaskPanelTabStub,
} satisfies AppExtensionSlots);
