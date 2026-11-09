// 装配形态契约：empty(Open Core) / collab(协作群极简站) / internal(内部仓覆盖) 三形态共享。
// 全部 type-only import：编译期擦除，本文件运行时为纯类型，槽位真实实现由装配文件静态持有。
// Task 17 起槽位载荷签名只依赖 src/extensions/contracts/ 纯契约文件（其依赖面收敛在
// domain/React/SDK 与既定纯类型家），零 import 被裁剪实现——collab 源切片裁掉任务栈/选路/
// 头像编辑实现后仍可独立 typecheck（见 test/extensions/featureContracts.test.ts）。
import type { AppCapabilities } from '@/capabilities';
import type { ComponentType, LazyExoticComponent } from 'react';
import type { AvatarEditorComponent } from './contracts/avatar';
import type {
  BuildTaskInstructions,
  ComposerCapabilitiesMenuComponent,
  CreateGroupViaExecute,
  GroupTaskExecutionHook,
  ManagerWorkerTasksComponent,
  TaskLinkedSessionComponent,
} from './contracts/groupTasks';
import type {
  ServiceBotRoutingControlComponent,
  ServiceBotRoutingHook,
} from './contracts/groupRouting';
import type { SessionTaskPanelTabHook, TaskPanelBootstrapHook } from './contracts/taskPanelBootstrap';

/** 群任务栈槽位（collab 形态 null：不渲染任务 UI、不发任务引擎请求、相关代码不进产物）。 */
export interface GroupTasksExtension {
  /** Task execute 建群；Task 7A 将共享 groupService 的实现依赖改为此槽位。 */
  createGroupViaExecute: CreateGroupViaExecute;
  /** 协作群任务执行装配（composer 能力菜单数据源）。 */
  useGroupTaskExecution: GroupTaskExecutionHook;
  /** 输入区能力菜单（任务胶囊/工作流）。 */
  ComposerCapabilitiesMenu: ComposerCapabilitiesMenuComponent;
  /** 任务胶囊 → 任务指令消息构造。 */
  buildTaskInstructions: BuildTaskInstructions;
  /** 任务节点下钻单 bot 会话视图（覆盖整个聊天区）。 */
  TaskLinkedSession: TaskLinkedSessionComponent;
  /** task_master_slave 群的 manager/worker 任务栏。 */
  ManagerWorkerTasks: ManagerWorkerTasksComponent;
}

/** 建群 service-bot 选路（TC Bot 分类服务；collab 形态 null：跳过分类查询、不打 routing 标签）。 */
export interface GroupServiceBotRoutingExtension {
  useServiceBotRouting: ServiceBotRoutingHook;
  ServiceBotRoutingControl: ServiceBotRoutingControlComponent;
}

/** 经 slots.ts 注册的组件/hook 槽位集合。 */
export interface AppExtensionSlots {
  groupTasks: GroupTasksExtension | null;
  groupServiceBotRouting: GroupServiceBotRoutingExtension | null;
  /** app.tsx 根容器任务会话联动观察者；null = 不挂载。 */
  taskSessionNavigationObserver: ComponentType | null;
  /** 任务副屏会话 bootstrap（群/单聊头部 hook 消费；collab 形态 null：不重放任务面板页签、不拉取任务栈，见 Task 8）。 */
  taskPanelBootstrap: TaskPanelBootstrapHook | null;
  /** 当前会话可重开的 TaskPanel 快照（Monitor 判空/重建；collab 形态 null：无任务栈恒无快照）。 */
  sessionTaskPanelTab: SessionTaskPanelTabHook | null;
}

/** appExtension 本体（capabilities + 组件装配），三形态各自在装配文件内注解满足。 */
export interface AppExtension {
  capabilities: AppCapabilities;
  /** Bot 头像编辑器（BotWorkshop 专属消费）；collab 形态无该页，置 null。 */
  avatarEditor: LazyExoticComponent<AvatarEditorComponent> | null;
}
